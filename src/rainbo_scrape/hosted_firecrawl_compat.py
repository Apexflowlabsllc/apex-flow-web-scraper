"""A deliberately partial Firecrawl v2 wire adapter over Apex's hosted features.

This preserves Apex billing and source-safety rules. Unsupported input is
rejected instead of being accepted and silently ignored. See the site guide for
the exact supported subset; this is not a claim of complete SDK compatibility.
"""
import asyncio
import json
import logging
import secrets
import time
from datetime import datetime, timezone

from aiohttp import web
from .policy import ScrapeError


def install(app, perform, map_for, websearch_for, crawl_start_for, crawl_status_for, crawl_cancel_for,
            batch_for, hosted):
    batch_tasks = {}
    hosted.db.executescript("""
        CREATE TABLE IF NOT EXISTS firecrawl_batch_jobs(
            id TEXT PRIMARY KEY, account TEXT NOT NULL, status TEXT NOT NULL, urls TEXT NOT NULL,
            options TEXT NOT NULL, total INTEGER NOT NULL, completed INTEGER NOT NULL DEFAULT 0,
            credits INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL, finished REAL);
        CREATE TABLE IF NOT EXISTS firecrawl_batch_pages(
            job TEXT NOT NULL, seq INTEGER NOT NULL, url TEXT NOT NULL, status INTEGER NOT NULL,
            payload TEXT NOT NULL, credits INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(job,seq));
        CREATE INDEX IF NOT EXISTS firecrawl_batch_account ON firecrawl_batch_jobs(account,created);
    """)
    hosted.db.execute("UPDATE firecrawl_batch_jobs SET status='failed',finished=? WHERE status='scraping'", (time.time(),))
    def error(message, code="unsupported_option", status=400):
        return web.json_response({"success": False, "error": message, "code": code}, status=status)

    def unpack(response):
        try:
            return json.loads(response.body.decode("utf-8"))
        except (AttributeError, UnicodeDecodeError, json.JSONDecodeError):
            return {}

    def forwarded(response, data):
        if response.status < 200 or response.status >= 300:
            return error(data.get("error", "Request failed"), data.get("code", "request_failed"), response.status)
        return None

    def page_data(result, credits_used=None, change=None, screenshot=False):
        metadata = dict(result.get("metadata") or {})
        receipt = result.get("receipt") or {}
        metadata.update({
            "title": result.get("title") or metadata.get("title"),
            "sourceURL": receipt.get("final_url") or result.get("url"),
            "url": result.get("url") or receipt.get("final_url"),
            "statusCode": receipt.get("status") or result.get("status"),
            "contentType": result.get("kind"),
        })
        if isinstance(credits_used, int):
            metadata["apexCreditsUsed"] = credits_used
        data = {"metadata": {k: v for k, v in metadata.items() if v is not None}}
        if isinstance(result.get("markdown"), str):
            data["markdown"] = result["markdown"]
        if isinstance(result.get("links"), list):
            data["links"] = [link.get("url") if isinstance(link, dict) else link
                              for link in result["links"] if isinstance(link, (str, dict))]
        if screenshot and result.get("screenshot_png_base64"):
            data["screenshot"] = "data:image/png;base64," + result["screenshot_png_base64"]
        if result.get("warning"):
            data["warning"] = result["warning"]
        if result.get("archive"):
            data["warning"] = "Apex returned an Internet Archive copy; it is not the live page."
            data["metadata"]["archive"] = result["archive"]
        if change:
            data["changeTracking"] = {"changeStatus": change.get("status"),
                                      "diff": change.get("diff"),
                                      "previousScrapeAt": change.get("previous_check")}
        return data

    def translate_actions(actions):
        """Translate the safe subset of Firecrawl's declarative actions."""
        if not isinstance(actions, list) or len(actions) > 8:
            raise ValueError("actions must be a list of at most 8 items")
        translated = []
        for action in actions:
            if not isinstance(action, dict):
                raise ValueError("each action must be an object")
            kind = action.get("type")
            if kind in {"click", "wait"} and set(action) == {"type", "selector"}:
                translated.append(action)
            elif kind == "wait" and set(action) == {"type", "milliseconds"}:
                milliseconds = action["milliseconds"]
                if isinstance(milliseconds, bool) or not isinstance(milliseconds, int) or not 0 <= milliseconds <= 5000:
                    raise ValueError("wait milliseconds must be an integer from 0 to 5000")
                translated.append({"type": "wait", "ms": milliseconds})
            elif kind == "scroll" and set(action) <= {"type", "direction", "amount"}:
                direction, amount = action.get("direction", "down"), action.get("amount", 1)
                if direction not in {"up", "down"} or isinstance(amount, bool) or not isinstance(amount, int) or not 1 <= amount <= 10:
                    raise ValueError("scroll direction must be up/down and amount must be 1–10")
                translated.append({"type": "scroll", "direction": direction, "amount": amount})
            else:
                raise ValueError(f"Firecrawl action {kind!r} is not supported by Apex's bounded read-only browser actions")
        return translated

    async def scrape(request):
        if request.content_type != "application/json":
            return error("Content-Type must be application/json", "invalid_request", 415)
        body = await request.json()
        if not isinstance(body, dict):
            return error("Request body must be a JSON object")
        allowed = {"url", "formats", "onlyMainContent", "maxAge", "screenshot", "actions"}
        extra = set(body) - allowed
        if extra:
            return error("Unsupported Apex compatibility options: " + ", ".join(sorted(extra)))
        url = body.get("url")
        if not isinstance(url, str) or not url:
            return error("url is required")
        if body.get("onlyMainContent", True) is not True:
            return error("onlyMainContent:false is not supported; Apex returns the main content")
        formats = body.get("formats", ["markdown"])
        if not isinstance(formats, list) or not formats:
            return error("formats must be a non-empty list")
        parsed = []
        for item in formats:
            kind = item if isinstance(item, str) else item.get("type") if isinstance(item, dict) else None
            if isinstance(item, dict) and set(item) - {"type"}:
                return error("Format-specific options are not supported by this compatibility subset")
            if kind not in {"markdown", "links", "screenshot"}:
                return error(f"Format {kind!r} is not supported; supported formats are markdown, links, screenshot")
            parsed.append(kind)
        if "maxAge" in body and (isinstance(body["maxAge"], bool) or not isinstance(body["maxAge"], int)
                                 or not 0 <= body["maxAge"] <= 31536000000):
            return error("maxAge must be milliseconds from 0 to 31536000000")
        if "screenshot" in body and not isinstance(body["screenshot"], bool):
            return error("screenshot must be a boolean")
        apex_body = {"url": url, "render": "auto"}
        if "maxAge" in body:
            apex_body["max_age"] = body["maxAge"]
        if body.get("actions") is not None:
            try:
                apex_body["actions"] = translate_actions(body["actions"])
            except ValueError as exc:
                return error(str(exc))
        want_screenshot = "screenshot" in parsed or body.get("screenshot", False)
        if want_screenshot:
            apex_body["screenshot"] = True
        response = await perform(request["account"], apex_body)
        payload = unpack(response)
        failure = forwarded(response, payload)
        if failure:
            return failure
        result = payload.get("result") or {}
        data = page_data(result, payload.get("credits_used"), payload.get("change"), want_screenshot)
        selected = {}
        if "markdown" in parsed and "markdown" in data:
            selected["markdown"] = data["markdown"]
        if "links" in parsed and "links" in data:
            selected["links"] = data["links"]
        selected["metadata"] = data["metadata"]
        if want_screenshot and "screenshot" in data:
            selected["screenshot"] = data["screenshot"]
        if data.get("warning"):
            selected["warning"] = data["warning"]
        if data.get("changeTracking"):
            selected["changeTracking"] = data["changeTracking"]
        return web.json_response({"success": True, "data": selected,
                                 "creditsUsed": payload.get("credits_used", 0)})

    async def map_route(request):
        body = await request.json()
        if not isinstance(body, dict):
            return error("Request body must be a JSON object")
        allowed = {"url", "search", "limit", "sitemap"}
        extra = set(body) - allowed
        if extra:
            return error("Unsupported Apex compatibility options: " + ", ".join(sorted(extra)))
        if body.get("sitemap", "include") != "include":
            return error("Only sitemap='include' is supported")
        request_body = {key: body[key] for key in ("url", "search", "limit") if key in body}
        response = await map_for(request["account"], request_body)
        payload = unpack(response)
        failure = forwarded(response, payload)
        if failure:
            return failure
        found = payload.get("result") or {}
        links = []
        for item in found.get("urls", []):
            if isinstance(item, str):
                links.append({"url": item})
            elif isinstance(item, dict) and isinstance(item.get("url"), str):
                links.append({key: item[key] for key in ("url", "title", "description") if item.get(key) is not None})
        reply = {"success": True, "links": links}
        if found.get("truncated"):
            reply["warning"] = "Apex's map limit truncated this result. Increase plan capacity or refine the search."
        return web.json_response(reply)

    async def search(request):
        body = await request.json()
        if not isinstance(body, dict):
            return error("Request body must be a JSON object")
        allowed = {"query", "limit", "sources"}
        extra = set(body) - allowed
        if extra:
            return error("Unsupported Apex compatibility options: " + ", ".join(sorted(extra)))
        sources = body.get("sources", [{"type": "web"}])
        if not isinstance(sources, list) or not sources or any(not isinstance(s, dict) or s.get("type") != "web" or set(s) != {"type"} for s in sources):
            return error("This adapter supports only the Firecrawl web search source")
        request_body = {key: body[key] for key in ("query", "limit") if key in body}
        response = await websearch_for(request["account"], request_body)
        payload = unpack(response)
        failure = forwarded(response, payload)
        if failure:
            return failure
        return web.json_response({"success": True,
                                 "data": {"web": [{"url": item.get("url"), "title": item.get("title"),
                                                    "description": item.get("snippet")}
                                                   for item in payload.get("results", [])]}})

    async def crawl(request):
        body = await request.json()
        if not isinstance(body, dict):
            return error("Request body must be a JSON object")
        allowed = {"url", "limit", "maxDiscoveryDepth", "includePaths", "excludePaths",
                   "ignoreRobotsTxt", "allowExternalLinks", "allowSubdomains", "sitemap", "scrapeOptions"}
        extra = set(body) - allowed
        if extra:
            return error("Unsupported Apex compatibility options: " + ", ".join(sorted(extra)))
        if body.get("ignoreRobotsTxt", False) is not False:
            return error("Apex always obeys robots.txt")
        if body.get("allowExternalLinks", False) is not False or body.get("allowSubdomains", False) is not False:
            return error("This adapter is same-site only; external links and subdomains are not enabled")
        if body.get("includePaths") or body.get("excludePaths"):
            return error("includePaths/excludePaths patterns are not supported yet; Apex crawl is bounded to same-site depth and limit")
        if body.get("sitemap", "include") != "include":
            return error("Only sitemap='include' is supported")
        scrape_options = body.get("scrapeOptions", {})
        if not isinstance(scrape_options, dict):
            return error("scrapeOptions must be an object")
        supported_scrape = {"formats", "onlyMainContent"}
        extra_scrape = set(scrape_options) - supported_scrape
        if extra_scrape:
            return error("Unsupported scrapeOptions: " + ", ".join(sorted(extra_scrape)))
        if scrape_options.get("onlyMainContent", True) is not True:
            return error("onlyMainContent:false is not supported")
        formats = scrape_options.get("formats", [{"type": "markdown"}])
        if not isinstance(formats, list) or not formats or any(
                item != "markdown" and item != {"type": "markdown"} and item != "links" and item != {"type": "links"}
                for item in formats):
            return error("Crawl supports only markdown and links formats")
        outputs = ["markdown"] + (["links"] if "links" in formats or {"type": "links"} in formats else [])
        start_body = {"url": body.get("url"), "limit": body.get("limit", 25), "render": "auto",
                      "outputs": outputs, "max_depth": body.get("maxDiscoveryDepth", 3)}
        response = await crawl_start_for(request["account"], start_body)
        payload = unpack(response)
        failure = forwarded(response, payload)
        if failure:
            return failure
        return web.json_response({"success": True, "id": payload["id"], "url": payload["url"]})

    async def crawl_status(request):
        offset_raw = request.query.get("offset", "0")
        if not offset_raw.isdigit():
            return error("offset must be a non-negative integer")
        response = await crawl_status_for(request["account"], {"id": request.match_info["job"],
                                                                 "offset": int(offset_raw)})
        payload = unpack(response)
        failure = forwarded(response, payload)
        if failure:
            return failure
        state = payload.get("status")
        status = {"running": "scraping", "completed": "completed", "cancelled": "cancelled",
                  "failed": "failed", "stopped": "failed", "interrupted": "failed"}.get(state, "failed")
        created = payload.get("created")
        finished = payload.get("finished")
        now = time.time()
        iso = lambda stamp: datetime.fromtimestamp(stamp, timezone.utc).isoformat().replace("+00:00", "Z") if stamp else None
        data = []
        for page in payload.get("results", []):
            result = page.get("result") or {}
            result.setdefault("url", page.get("url"))
            data.append(page_data(result, page.get("credits_used")))
        total = (payload.get("pages_done", 0) + payload.get("pages_failed", 0))
        reply = {"status": status, "total": total, "completed": payload.get("pages_done", 0),
                 "creditsUsed": payload.get("credits_used", 0), "createdAt": iso(created),
                 "completedAt": iso(finished), "duration": max(0, (finished or now) - created) if created else 0,
                 "data": data}
        next_offset = payload.get("next_offset")
        reply["next"] = f"/v2/crawl/{request.match_info['job']}?offset={next_offset}" if next_offset is not None else None
        if payload.get("note"):
            reply["warning"] = payload["note"]
        return web.json_response(reply)

    async def crawl_cancel(request):
        response = await crawl_cancel_for(request["account"], {"id": request.match_info["job"]})
        payload = unpack(response)
        failure = forwarded(response, payload)
        if failure:
            return failure
        return web.json_response({"success": True, "id": payload.get("id"), "status": payload.get("status")})

    def batch_status(job, account):
        row = hosted.db.execute(
            "SELECT id,status,total,completed,credits,created,finished FROM firecrawl_batch_jobs WHERE id=? AND account=?",
            (job, account)).fetchone()
        if not row:
            raise web.HTTPNotFound(text=json.dumps({"error": "Batch scrape job not found."}), content_type="application/json")
        return row

    async def run_batch(job, account, urls, options):
        def save_page(index, url, item):
            status = item.get("status", 502)
            credits = item.get("credits_used", 0) if status == 200 else 0
            result = item.get("result") if status == 200 else None
            if isinstance(result, dict):
                result = page_data(result, credits,
                                   screenshot=options.get("screenshot", False)
                                   or "screenshot" in options.get("_compat_formats", []))
                for field in ("markdown", "links", "screenshot"):
                    if field not in options.get("_compat_formats", []) and not (
                            field == "screenshot" and options.get("screenshot", False)):
                        result.pop(field, None)
            else:
                result = {"metadata": {"url": url, "sourceURL": url, "statusCode": status,
                                       "error": item.get("error", "Scrape failed")}}
            hosted.db.execute("INSERT OR REPLACE INTO firecrawl_batch_pages VALUES(?,?,?,?,?,?)",
                              (job, index, url, status, json.dumps(result), credits))
            hosted.db.execute("UPDATE firecrawl_batch_jobs SET completed=completed+1,credits=credits+? WHERE id=?",
                              (credits, job))

        try:
            for start in range(0, len(urls), 10):
                state = hosted.db.execute("SELECT status FROM firecrawl_batch_jobs WHERE id=?", (job,)).fetchone()
                if not state or state[0] != "scraping":
                    return
                pending = [(start + offset, url) for offset, url in enumerate(urls[start:start + 10])]
                attempt = 0
                while pending:
                    state = hosted.db.execute("SELECT status FROM firecrawl_batch_jobs WHERE id=?", (job,)).fetchone()
                    if not state or state[0] != "scraping":
                        return
                    chunk = [url for _, url in pending]
                    call_options = {key: value for key, value in options.items() if not key.startswith("_")}
                    response = await batch_for(account, {"urls": chunk, **call_options})
                    payload = unpack(response)
                    if response.status == 429 or response.status >= 500:
                        if attempt < 4:
                            await asyncio.sleep(min(15, 2 ** attempt))
                            attempt += 1
                            continue
                        for index, url in pending:
                            save_page(index, url, {"status": response.status,
                                                   "error": payload.get("error", "Batch service remained unavailable"),
                                                   "code": payload.get("code", "retry_exhausted")})
                        break
                    if response.status != 200:
                        for index, url in pending:
                            save_page(index, url, {"status": response.status,
                                                   "error": payload.get("error", "Batch request failed"),
                                                   "code": payload.get("code", "request_failed")})
                        break
                    entries = payload.get("results", []) if response.status == 200 else []
                    positions = {}
                    for pair in pending:
                        positions.setdefault(pair[1], []).append(pair)
                    retry = []
                    for item in entries:
                        if not isinstance(item, dict):
                            continue
                        candidates = positions.get(item.get("url"), [])
                        if not candidates:
                            continue
                        index, url = candidates.pop(0)
                        if item.get("status") == 429 and attempt < 4:
                            retry.append((index, url))
                        else:
                            save_page(index, url, item)
                    for candidates in positions.values():
                        retry.extend(candidates)
                    if not retry:
                        break
                    if attempt >= 4:
                        for index, url in retry:
                            save_page(index, url, {"status": 429,
                                                   "error": "Batch capacity remained unavailable after retries",
                                                   "code": "retry_exhausted"})
                        break
                    pending = retry
                    await asyncio.sleep(min(15, 2 ** attempt))
                    attempt += 1
            hosted.db.execute("UPDATE firecrawl_batch_jobs SET status='completed',finished=? WHERE id=? AND status='scraping'",
                              (time.time(), job))
        except asyncio.CancelledError:
            hosted.db.execute("UPDATE firecrawl_batch_jobs SET status='cancelled',finished=? WHERE id=? AND status='scraping'",
                              (time.time(), job))
            raise
        except Exception:
            logging.exception("Firecrawl-compatible batch failed job=%s", job)
            hosted.db.execute("UPDATE firecrawl_batch_jobs SET status='failed',finished=? WHERE id=? AND status='scraping'",
                              (time.time(), job))
        finally:
            batch_tasks.pop(job, None)

    async def batch_scrape(request):
        body = await request.json()
        if not isinstance(body, dict):
            return error("Request body must be a JSON object", "invalid_request")
        urls = body.get("urls")
        if not isinstance(urls, list) or not urls or any(not isinstance(url, str) or not url.strip() for url in urls):
            return error("urls must be a non-empty array of URL strings", "invalid_request")
        plan = hosted.db.execute("SELECT plan FROM accounts WHERE id=?", (request["account"],)).fetchone()
        from .hosted_crawl import PAGE_LIMITS
        max_urls = PAGE_LIMITS.get(plan[0] if plan else "", 100)
        if len(urls) > max_urls:
            return error(f"This plan supports at most {max_urls} URLs per batch job", "batch_limit", 413)
        allowed = {"urls", "formats", "onlyMainContent", "maxAge", "screenshot", "actions",
                   "ignoreInvalidURLs"}
        extra = set(body) - allowed
        if extra:
            return error("Unsupported Apex compatibility options: " + ", ".join(sorted(extra)))
        if body.get("ignoreInvalidURLs", False) is not False:
            return error("ignoreInvalidURLs:true is not supported; invalid or unsafe destinations are reported per URL")
        if body.get("onlyMainContent", True) is not True:
            return error("onlyMainContent:false is not supported; Apex returns the main content")
        formats = body.get("formats", ["markdown"])
        if not isinstance(formats, list) or not formats:
            return error("formats must be a non-empty list")
        selected = []
        for item in formats:
            kind = item if isinstance(item, str) else item.get("type") if isinstance(item, dict) else None
            if isinstance(item, dict) and set(item) - {"type"}:
                return error("Format-specific options are not supported by this compatibility subset")
            if kind not in {"markdown", "links", "screenshot"}:
                return error(f"Format {kind!r} is not supported; supported formats are markdown, links, screenshot")
            selected.append(kind)
        if "maxAge" in body and (isinstance(body["maxAge"], bool) or not isinstance(body["maxAge"], int)
                                 or not 0 <= body["maxAge"] <= 31536000000):
            return error("maxAge must be milliseconds from 0 to 31536000000")
        if "screenshot" in body and not isinstance(body["screenshot"], bool):
            return error("screenshot must be a boolean")
        apex_options = {"outputs": sorted(set(selected)), "render": "auto"}
        if "maxAge" in body:
            apex_options["max_age"] = body["maxAge"]
        if body.get("actions") is not None:
            try:
                apex_options["actions"] = translate_actions(body["actions"])
            except ValueError as exc:
                return error(str(exc))
        if "screenshot" in selected or body.get("screenshot", False):
            apex_options["screenshot"] = True
        apex_options["_compat_formats"] = selected
        hosted.db.execute("DELETE FROM firecrawl_batch_pages WHERE job IN (SELECT id FROM firecrawl_batch_jobs WHERE created<?)",
                          (time.time() - 7 * 86400,))
        hosted.db.execute("DELETE FROM firecrawl_batch_jobs WHERE created<?", (time.time() - 7 * 86400,))
        job = "batch_" + secrets.token_hex(12)
        created = time.time()
        hosted.db.execute("INSERT INTO firecrawl_batch_jobs(id,account,status,urls,options,total,created) VALUES(?,?, 'scraping', ?,?,?,?)",
                          (job, request["account"], json.dumps(urls), json.dumps(apex_options), len(urls), created))
        batch_tasks[job] = asyncio.create_task(run_batch(job, request["account"], urls, apex_options))
        return web.json_response({"success": True, "id": job,
                                  "url": f"/v2/batch/scrape/{job}",
                                  "status": "scraping"}, status=200)

    async def batch_status_route(request):
        job_id = request.match_info["job"]
        row = batch_status(job_id, request["account"])
        total, completed, credits, created, finished = row[2], row[3], row[4], row[5], row[6]
        skip_raw = request.query.get("skip", "0")
        if not skip_raw.isdigit():
            return error("skip must be a non-negative integer", "invalid_request")
        skip = int(skip_raw)
        data = [json.loads(item[0]) for item in hosted.db.execute(
            "SELECT payload FROM firecrawl_batch_pages WHERE job=? AND seq>=? ORDER BY seq LIMIT 50", (job_id, skip))]
        end = skip + len(data)
        more = hosted.db.execute("SELECT 1 FROM firecrawl_batch_pages WHERE job=? AND seq>=? LIMIT 1", (job_id, end)).fetchone()
        now = time.time()
        iso = lambda stamp: datetime.fromtimestamp(stamp, timezone.utc).isoformat().replace("+00:00", "Z") if stamp else None
        next_url = f"/v2/batch/scrape/{job_id}?skip={end}" if more else None
        return web.json_response({"status": row[1], "total": total, "completed": completed,
                                  "creditsUsed": credits, "createdAt": iso(created),
                                  "completedAt": iso(finished), "expiresAt": iso(created + 7 * 86400),
                                  "duration": max(0, (finished or now) - created), "next": next_url, "data": data})

    async def batch_errors(request):
        row = batch_status(request.match_info["job"], request["account"])
        records = []
        for seq, url, status, payload in hosted.db.execute(
                "SELECT seq,url,status,payload FROM firecrawl_batch_pages WHERE job=? AND status!=200 ORDER BY seq LIMIT 500",
                (row[0],)):
            item = json.loads(payload)
            records.append({"id": f"{row[0]}-{seq}", "url": url, "status": status,
                            "error": item.get("metadata", {}).get("error", "Scrape failed")})
        return web.json_response({"errors": records})

    async def batch_cancel(request):
        row = batch_status(request.match_info["job"], request["account"])
        if row[1] == "scraping":
            task = batch_tasks.get(row[0])
            hosted.db.execute("UPDATE firecrawl_batch_jobs SET status='cancelled',finished=? WHERE id=? AND status='scraping'",
                              (time.time(), row[0]))
            if task:
                task.cancel()
        return web.json_response({"status": "cancelled" if row[1] == "scraping" else row[1]})

    def wrap(handler):
        async def wrapped(request):
            try:
                return await handler(request)
            except (ValueError, KeyError, TypeError, ScrapeError) as exc:
                return error(str(exc), getattr(exc, "code", None) or "invalid_request")
            except web.HTTPException as exc:
                return error(exc.reason, "request_failed", exc.status)
        return wrapped

    app.router.add_post("/v2/scrape", wrap(scrape))
    app.router.add_post("/v2/map", wrap(map_route))
    app.router.add_post("/v2/search", wrap(search))
    app.router.add_post("/v2/crawl", wrap(crawl))
    app.router.add_get("/v2/crawl/{job}", wrap(crawl_status))
    app.router.add_delete("/v2/crawl/{job}", wrap(crawl_cancel))
    app.router.add_post("/v2/batch/scrape", wrap(batch_scrape))
    app.router.add_get("/v2/batch/scrape/{job}/errors", wrap(batch_errors))
    app.router.add_get("/v2/batch/scrape/{job}", wrap(batch_status_route))
    app.router.add_delete("/v2/batch/scrape/{job}", wrap(batch_cancel))
