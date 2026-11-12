"""Bounded hosted discovery, account-scoped search, and anonymous funnel counts."""
import asyncio
import difflib
import hashlib
import logging
import json
import re
import time
from aiohttp import web
from .billing import QuotaExceeded
from .discovery import site_map
from .engine import Fetcher
from .policy import check_url
from .settings import Settings
from .store import Store


def prepare(h):
    h.db.executescript("""
        CREATE VIRTUAL TABLE IF NOT EXISTS saved_pages USING fts5(account UNINDEXED,url UNINDEXED,title,body,tokenize='porter unicode61');
        CREATE TABLE IF NOT EXISTS funnel(day TEXT,event TEXT,plan TEXT,count INTEGER,PRIMARY KEY(day,event,plan));
        CREATE TABLE IF NOT EXISTS attempt_counts(account TEXT,period REAL,count INTEGER,PRIMARY KEY(account,period));
        CREATE TABLE IF NOT EXISTS page_versions(account TEXT,url TEXT,sha TEXT,first_seen REAL,checked REAL,changed REAL,PRIMARY KEY(account,url));
    """)


def event(h, name, plan=""):
    day=time.strftime('%Y-%m-%d',time.gmtime())
    h.db.execute("INSERT INTO funnel VALUES(?,?,?,1) ON CONFLICT(day,event,plan) DO UPDATE SET count=count+1", (day,name,plan))


def save_page(h, account, result, diff=False):
    """Index the page for this account only and report how it differs from the account's previous copy."""
    if not result.get("url"):
        return None
    try:
        url = result["url"]
        body = str(result.get("text") or result.get("markdown") or '')
        sha = result.get("content_sha256") or hashlib.sha256(body.encode()).hexdigest()
        now = time.time()
        keep = h.billing.limits(account).get("saved_pages", 100)
        before = h.db.execute("SELECT sha,checked,changed FROM page_versions WHERE account=? AND url=?", (account, url)).fetchone()
        if before is None:
            change = {"status": "new"}
        elif before[0] == sha:
            change = {"status": "unchanged", "previous_check": before[1], "last_changed": before[2]}
        else:
            change = {"status": "changed", "previous_check": before[1], "previous_sha256": before[0], "last_changed": now}
            if diff:
                old = h.db.execute("SELECT body FROM saved_pages WHERE account=? AND url=?", (account, url)).fetchone()
                lines = list(difflib.unified_diff((old[0] if old else '').splitlines(), body[:20000].splitlines(),
                                                  "previous", "current", n=1, lineterm=''))
                change["diff"] = "\n".join(lines[:200])[:12000]
                change["diff_truncated"] = len(lines) > 200 or len(body) > 20000
        change["content_sha256"] = sha
        h.db.execute("DELETE FROM saved_pages WHERE account=? AND url=?", (account, url))
        h.db.execute("INSERT INTO saved_pages(account,url,title,body) VALUES(?,?,?,?)",
                     (account, url, str(result.get("title") or '')[:1000], body[:20000]))
        h.db.execute("INSERT INTO page_versions VALUES(?,?,?,?,?,?) ON CONFLICT(account,url) DO UPDATE SET sha=excluded.sha,checked=excluded.checked,changed=excluded.changed",
                     (account, url, sha, now, now, now if before is None or before[0] != sha else before[2]))
        # Only the caller's latest pages are kept (100 on the trial, 1,000 on paid plans). No cross-account lookup.
        h.db.execute("DELETE FROM saved_pages WHERE account=? AND rowid NOT IN (SELECT rowid FROM saved_pages WHERE account=? ORDER BY rowid DESC LIMIT ?)", (account, account, keep))
        h.db.execute("DELETE FROM page_versions WHERE account=? AND url NOT IN (SELECT url FROM saved_pages WHERE account=?)", (account, account))
        return change
    except Exception:
        logging.warning("saved-page index unavailable account=%s", account)
        return None


def usage_details(h, account):
    """Everything a client needs to plan work: plan, limits, renewal and the last 14 days of spend."""
    from .billing import PLANS
    limits = h.billing.limits(account)
    plan = next(name for name, value in PLANS.items() if value is limits)
    from .hosted_crawl import PAGE_LIMITS
    crawl_pages = PAGE_LIMITS.get(plan, 100)
    order = h.db.execute("SELECT period_end FROM orders WHERE account=?", (account,)).fetchone()
    days = h.db.execute("""SELECT strftime('%Y-%m-%d',ts,'unixepoch'),SUM(credits),SUM(kind!='refund')
                           FROM usage WHERE account=? AND ts>? GROUP BY 1 ORDER BY 1""", (account, time.time()-14*86400)).fetchall()
    saved = h.db.execute("SELECT COUNT(*) FROM page_versions WHERE account=?", (account,)).fetchone()[0]
    return {"plan": plan, "credits_total": limits["credits"], "period_end": (order[0] or None) if order else None,
            "limits": {"requests_per_minute": limits["rpm"], "concurrent_scrapes": limits["concurrency"],
                       "batch_urls": 10, "saved_pages": limits.get("saved_pages", 100), "crawl_pages": crawl_pages},
            "saved_pages": saved,
            "daily": [{"day": d, "credits": max(0, c or 0), "requests": n or 0} for d, c, n in days],
            "buy_url": h.site + "/#pricing"}


def admit_attempt(h, account):
    period=h.db.execute("SELECT period_start FROM accounts WHERE id=?",(account,)).fetchone()[0]
    maximum=h.billing.limits(account)["credits"]*2
    h.db.execute("INSERT OR IGNORE INTO attempt_counts VALUES(?,?,0)",(account,period))
    updated=h.db.execute("UPDATE attempt_counts SET count=count+1 WHERE account=? AND period=? AND count<?",(account,period,maximum)).rowcount
    if not updated:
        raise web.HTTPTooManyRequests(text='{"error":"Request-attempt allowance reached. Contact support to review repeated failures."}',content_type="application/json")


def install(h, app):
    mapping=0
    async def map_site(request):
        return await map_for(request["account"], await request.json())

    async def map_for(account, body):
        nonlocal mapping
        if not isinstance(body,dict) or set(body)-{"url","limit","search"}:
            raise ValueError("Use url, limit and optional search")
        url=check_url(body.get("url"))
        limit=body.get("limit",1000)
        if isinstance(limit,bool) or not isinstance(limit,int) or not 1<=limit<=1000:
            raise ValueError("limit must be 1–1000")
        if body.get("search") is not None and (not isinstance(body["search"],str) or len(body["search"])>200):
            raise ValueError("search must be at most 200 characters")
        if mapping>=2:
            raise web.HTTPTooManyRequests(headers={"Retry-After":"10"})
        admit_attempt(h, account)
        try:
            h.billing.reserve(account,1)
        except QuotaExceeded:
            return web.json_response({"error":"Not enough credits"},status=402)
        mapping+=1
        try:
            settings=Settings()
            async with asyncio.timeout(45):
                async with Fetcher(settings,Store(settings)) as fetcher:
                    result=await site_map(fetcher,url,limit=limit,search=body.get("search"),max_sitemaps=20)
            if not result["urls"]:
                h.billing.refund(account,1)
                return web.json_response({"credits_used":0,"credits_left":h.billing.remaining(account),"result":result})
            while len(json.dumps(result).encode())>250000 and result["urls"]:
                result["urls"].pop()
                result["truncated"]=True
            result["count"]=len(result["urls"])
            return web.json_response({"credits_used":1,"credits_left":h.billing.remaining(account),"result":result})
        except BaseException:
            h.billing.refund(account,1)
            raise
        finally:
            mapping-=1

    async def search(request):
        return await search_for(request["account"], await request.json())

    async def search_for(account, body):
        query=body.get("query") if isinstance(body,dict) else None
        if not isinstance(query,str) or not 1<=len(query)<=200:
            raise ValueError("query must contain 1–200 characters")
        limit=body.get("limit",10)
        if isinstance(limit,bool) or not isinstance(limit,int) or not 1<=limit<=25:
            raise ValueError("limit must be 1–25")
        words=re.findall(r'\w+',query.lower())[:12]
        if not words:
            raise ValueError("query needs at least one word")
        expr=' OR '.join('"'+w+'"' for w in words)
        rows=h.db.execute("SELECT url,title,snippet(saved_pages,3,'[',']','…',32) FROM saved_pages WHERE saved_pages MATCH ? AND account=? ORDER BY bm25(saved_pages) LIMIT ?",(expr,account,limit)).fetchall()
        keep=h.billing.limits(account).get("saved_pages",100)
        return web.json_response({"results":[{"url":r[0],"title":r[1],"snippet":r[2]} for r in rows],"scope":f"Your latest {keep:,} successfully scraped URLs; not a web search engine.","credits_used":0})

    async def funnel(request):
        h.limit(request,"funnel",120)
        body=await request.json()
        if not isinstance(body,dict) or body.get("event")!="page_view":
            raise ValueError("Unsupported event")
        event(h,"page_view")
        return web.json_response({"ok":True})

    app.router.add_post('/v1/map',map_site)
    app.router.add_post('/v1/search',search)
    app.router.add_post('/api/event',funnel)
    return {"/map": map_for, "/search": search_for}
