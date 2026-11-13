"""Hosted site crawl: a background job that follows a site's own pages through the metered scrape path.

Every page goes through the same admission, robots, credit and change-tracking code as a single
scrape, so a crawl can never spend more than the pages it returns.
"""
import asyncio
import json
import logging
import secrets
import time
from urllib.parse import urldefrag, urlsplit

from aiohttp import web

from .policy import ScrapeError, check_url

PAGE_LIMITS = {"free": 10, "pass": 100, "starter": 100, "growth": 500, "scale": 1000}
RUNNING_JOBS = {"free": 1}
KEEP_SECONDS = 7 * 86400
DEADLINE = 1800
SKIP = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".css", ".js", ".zip", ".gz", ".mp4", ".mp3", ".woff", ".woff2")


def prepare(h):
    h.db.executescript("""
        CREATE TABLE IF NOT EXISTS crawl_jobs(id TEXT PRIMARY KEY,account TEXT,url TEXT,status TEXT,page_limit INTEGER,
            created REAL,finished REAL,options TEXT,done INTEGER DEFAULT 0,failed INTEGER DEFAULT 0,credits INTEGER DEFAULT 0,note TEXT);
        CREATE TABLE IF NOT EXISTS crawl_pages(job TEXT,seq INTEGER,url TEXT,status INTEGER,payload TEXT,PRIMARY KEY(job,seq));
        CREATE INDEX IF NOT EXISTS crawl_jobs_account ON crawl_jobs(account,created);
    """)
    # A restart ends in-memory jobs; say so instead of leaving them "running" forever.
    h.db.execute("UPDATE crawl_jobs SET status='interrupted',finished=?,note='Service restarted. Pages already collected are kept; start a new crawl for the rest.' WHERE status='running'", (time.time(),))


async def sitemap_urls(url, limit):
    """Sitemap URLs for seeding a crawl. Honors robots.txt like every other request."""
    from .discovery import site_map
    from .engine import Fetcher
    from .settings import Settings
    from .store import Store
    settings = Settings()
    async with asyncio.timeout(30):
        async with Fetcher(settings, Store(settings)) as fetcher:
            found = await site_map(fetcher, url, limit=limit, include_links=False, max_sitemaps=5)
    return [u.get("url") if isinstance(u, dict) else u for u in found.get("urls", [])]


def _site(url):
    """example.com and www.example.com are one site; a start URL often redirects between them."""
    host = urlsplit(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _plan(h, account):
    return h.db.execute("SELECT plan FROM accounts WHERE id=?", (account,)).fetchone()[0]


def _links(result):
    for link in result.get("links") or []:
        value = link.get("url") if isinstance(link, dict) else link
        if isinstance(value, str):
            yield value


def install(h, app, perform, map_urls=None):
    tasks = {}

    async def run(job, account, start, limit, options, include, max_depth):
        host = _site(start)
        queue, seen, seq, active = [(start, 0)], {start}, 0, 0
        scrape_options = {k: options[k] for k in ("render", "fallback", "recipe") if k in options}
        wanted = options.get("outputs") or ["markdown"]
        note = None

        def offer(url, depth):
            url = urldefrag(url)[0]
            parts = urlsplit(url)
            if (parts.scheme in ("http", "https") and _site(url) == host and url not in seen and depth <= max_depth
                    and not parts.path.lower().endswith(SKIP) and (not include or include in url) and len(seen) < limit * 4):
                seen.add(url)
                queue.append((url, depth))

        if map_urls:
            try:
                for url in await map_urls(start, limit * 2):
                    offer(url, 1)
            except Exception:
                pass

        async def worker():
            nonlocal seq, active, note
            while not note:
                if h.db.execute("SELECT status FROM crawl_jobs WHERE id=?", (job,)).fetchone()[0] != "running":
                    return
                done = h.db.execute("SELECT done FROM crawl_jobs WHERE id=?", (job,)).fetchone()[0]
                if done + active >= limit:
                    return
                if not queue:
                    if not active:
                        return
                    await asyncio.sleep(0.05)
                    continue
                url, depth = queue.pop(0)
                active += 1
                try:
                    item = None
                    for attempt in range(6):
                        try:
                            response = await perform(account, named=False, body={"url": url, "outputs": sorted(set(wanted) | {"links"}), **scrape_options})
                            item = {"status": response.status, **json.loads(response.body)}
                        except (ValueError, KeyError, TypeError, ScrapeError) as exc:
                            item = {"status": 400, "error": str(exc), "code": getattr(exc, "code", None) or "invalid_request"}
                        except web.HTTPException as exc:
                            item = {"status": exc.status, "error": exc.reason, "code": "rate_limited" if exc.status == 429 else "error"}
                        except Exception:
                            item = {"status": 502, "error": "Source request failed; no credits were charged.", "code": "source_failed"}
                        if item["status"] != 429:
                            break
                        await asyncio.sleep(min(15, item.get("retry_after") or 10))   # shared capacity: wait our turn
                    if item["status"] == 402:
                        note = "Stopped: not enough credits. Add credits and start a new crawl for the remaining pages."
                        return
                    if item["status"] == 429:
                        note = "Stopped: request limits were reached repeatedly. Pages already collected are kept."
                        return
                    if item["status"] == 200:
                        result = item.get("result", {})
                        for link in _links(result):
                            offer(link, depth + 1)
                        if "links" not in wanted:
                            result.pop("links", None)
                    seq += 1
                    h.db.execute("INSERT INTO crawl_pages VALUES(?,?,?,?,?)", (job, seq, url, item["status"], json.dumps({"url": url, **item})))
                    ok = item["status"] == 200
                    h.db.execute("UPDATE crawl_jobs SET done=done+?,failed=failed+?,credits=credits+? WHERE id=?",
                                 (int(ok), int(not ok), item.get("credits_used", 0) if ok else 0, job))
                finally:
                    active -= 1

        status = "completed"
        try:
            async with asyncio.timeout(DEADLINE):
                await asyncio.gather(*(worker() for _ in range(min(4, h.billing.limits(account)["concurrency"]))))
        except TimeoutError:
            note = "Stopped at the 30-minute crawl deadline. Pages already collected are kept."
        except asyncio.CancelledError:
            status = "cancelled"
        except Exception:
            logging.exception("crawl failed job=%s", job)
            status, note = "failed", "The crawl stopped unexpectedly. Pages already collected are kept."
        finally:
            tasks.pop(job, None)
            if note and status == "completed":
                status = "stopped"
            h.db.execute("UPDATE crawl_jobs SET status=?,finished=?,note=COALESCE(?,note) WHERE id=? AND status='running'",
                         (status, time.time(), note, job))

    async def start_for(account, body):
        allowed = {"url", "limit", "render", "outputs", "include", "max_depth", "fallback", "recipe"}
        if not isinstance(body, dict) or set(body) - allowed:
            raise ValueError("Use url with optional " + ", ".join(sorted(allowed - {"url"})))
        try:
            start = urldefrag(check_url(body.get("url")))[0]
        except ScrapeError as exc:
            raise ValueError(str(exc))
        plan = _plan(h, account)
        most = PAGE_LIMITS.get(plan, 100)
        limit = body.get("limit", min(25, most))
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= most:
            raise ValueError(f"limit must be 1–{most} on this plan")
        depth = body.get("max_depth", 3)
        if isinstance(depth, bool) or not isinstance(depth, int) or not 0 <= depth <= 10:
            raise ValueError("max_depth must be 0–10")
        include = body.get("include")
        if include is not None and (not isinstance(include, str) or not 1 <= len(include) <= 200):
            raise ValueError("include must be 1–200 characters that every crawled URL contains")
        if body.get("render", "never") not in ("never", "auto", "always"):
            raise ValueError("render must be never, auto or always")
        running = h.db.execute("SELECT COUNT(*) FROM crawl_jobs WHERE account=? AND status='running'", (account,)).fetchone()[0]
        if running >= RUNNING_JOBS.get(plan, 2):
            return web.json_response({"error": "A crawl is already running on this account. Wait for it to finish or cancel it.",
                                      "code": "crawl_in_progress", "retryable": True}, status=429, headers={"Retry-After": "30"})
        if h.billing.remaining(account) < 1:
            return web.json_response({"error": "Not enough credits", "code": "insufficient_credits", "retryable": False,
                                      "credits_left": 0, "buy_url": h.site + "/#pricing"}, status=402)
        options = {"render": body.get("render", "never"), **{k: body[k] for k in ("outputs", "fallback", "recipe") if k in body}}
        job = "crawl_" + secrets.token_hex(12)
        h.db.execute("DELETE FROM crawl_pages WHERE job IN (SELECT id FROM crawl_jobs WHERE created<?)", (time.time() - KEEP_SECONDS,))
        h.db.execute("DELETE FROM crawl_jobs WHERE created<?", (time.time() - KEEP_SECONDS,))
        h.db.execute("INSERT INTO crawl_jobs(id,account,url,status,page_limit,created,options) VALUES(?,?,?,'running',?,?,?)",
                     (job, account, start, limit, time.time(), json.dumps(options)))
        tasks[job] = asyncio.create_task(run(job, account, start, limit, options, include, depth))
        return web.json_response({"id": job, "status": "running", "url": start, "limit": limit,
                                  "status_url": h.site + "/v1/crawl/" + job,
                                  "note": "Poll status_url. Each page costs the usual 1 or 3 credits; pages that fail are free. Results are kept for 7 days."})

    def row(account, job):
        found = h.db.execute("SELECT id,url,status,page_limit,created,finished,done,failed,credits,note FROM crawl_jobs WHERE id=? AND account=?",
                             (job if isinstance(job, str) else "", account)).fetchone()
        if not found:
            raise web.HTTPNotFound(text='{"error":"No such crawl on this account.","code":"not_found","retryable":false}', content_type="application/json")
        return found

    async def status_for(account, body):
        if not isinstance(body, dict):
            raise ValueError("Expected an id")
        found = row(account, body.get("id"))
        offset = body.get("offset", 0)
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("offset must be 0 or more")
        results, used = [], 0
        for seq, payload in h.db.execute("SELECT seq,payload FROM crawl_pages WHERE job=? AND seq>? ORDER BY seq LIMIT 50", (found[0], offset)):
            if results and used + len(payload) > 230000:
                break
            results.append(json.loads(payload))
            used += len(payload)
            offset = seq
        more = h.db.execute("SELECT COUNT(*) FROM crawl_pages WHERE job=? AND seq>?", (found[0], offset)).fetchone()[0]
        reply = {"id": found[0], "url": found[1], "status": found[2], "limit": found[3], "created": found[4], "finished": found[5],
                 "pages_done": found[6], "pages_failed": found[7], "credits_used": found[8],
                 "credits_left": h.billing.remaining(account), "results": results,
                 "next_offset": offset if more or found[2] == "running" else None}
        if found[9]:
            reply["note"] = found[9]
        return web.json_response(reply)

    async def cancel_for(account, body):
        found = row(account, body.get("id") if isinstance(body, dict) else None)
        h.db.execute("UPDATE crawl_jobs SET status='cancelled',finished=? WHERE id=? AND status='running'", (time.time(), found[0]))
        return web.json_response({"id": found[0], "status": "cancelled" if found[2] == "running" else found[2]})

    async def start(request):
        return await start_for(request["account"], await request.json())

    async def status(request):
        offset = request.query.get("offset", "0")
        return await status_for(request["account"], {"id": request.match_info["job"], "offset": int(offset) if offset.isdigit() else -1})

    async def cancel(request):
        return await cancel_for(request["account"], {"id": request.match_info["job"]})

    app.router.add_post("/v1/crawl", start)
    app.router.add_get("/v1/crawl/{job}", status)
    app.router.add_delete("/v1/crawl/{job}", cancel)
    return {"/crawl": start_for, "/crawl/status": status_for, "/crawl/cancel": cancel_for}
