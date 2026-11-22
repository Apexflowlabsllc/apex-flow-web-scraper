"""A deliberately partial Firecrawl v2 wire adapter over Apex's hosted features.

This preserves Apex billing and source-safety rules. Unsupported input is
rejected instead of being accepted and silently ignored. See the site guide for
the exact supported subset; this is not a claim of complete SDK compatibility.
"""
import json
import time
from datetime import datetime, timezone

from aiohttp import web
from .policy import ScrapeError


def install(app, perform, map_for, websearch_for, crawl_start_for, crawl_status_for, crawl_cancel_for):
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

    async def batch_scrape(request):
        return error("Firecrawl-compatible async batch is not implemented. Use Apex /v1/batch for its ordered, bounded synchronous batch API.",
                     "compatibility_not_implemented", 501)

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
