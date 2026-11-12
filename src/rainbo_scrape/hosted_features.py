"""Bounded hosted discovery, account-scoped search, and anonymous funnel counts."""
import asyncio
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
    """)


def event(h, name, plan=""):
    day=time.strftime('%Y-%m-%d',time.gmtime())
    h.db.execute("INSERT INTO funnel VALUES(?,?,?,1) ON CONFLICT(day,event,plan) DO UPDATE SET count=count+1", (day,name,plan))


def save_page(h, account, result):
    # Only the caller's latest 100 successful URLs are indexed. No cross-account lookup.
    if not result.get("url"):
        return
    try:
        h.db.execute("DELETE FROM saved_pages WHERE account=? AND url=?",(account,result["url"]))
        h.db.execute("INSERT INTO saved_pages(account,url,title,body) VALUES(?,?,?,?)",
                     (account,result["url"],str(result.get("title") or '')[:1000],str(result.get("text") or result.get("markdown") or '')[:20000]))
        h.db.execute("DELETE FROM saved_pages WHERE account=? AND rowid NOT IN (SELECT rowid FROM saved_pages WHERE account=? ORDER BY rowid DESC LIMIT 100)",(account,account))
    except Exception:
        logging.warning("saved-page index unavailable account=%s",account)


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
        words=re.findall(r'\w+',query.lower())[:12]
        if not words:
            raise ValueError("query needs at least one word")
        expr=' OR '.join('"'+w+'"' for w in words)
        rows=h.db.execute("SELECT url,title,snippet(saved_pages,3,'[',']','…',32) FROM saved_pages WHERE saved_pages MATCH ? AND account=? ORDER BY bm25(saved_pages) LIMIT 10",(expr,account)).fetchall()
        return web.json_response({"results":[{"url":r[0],"title":r[1],"snippet":r[2]} for r in rows],"scope":"Your latest 100 successfully scraped URLs; not a web search engine.","credits_used":0})

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
