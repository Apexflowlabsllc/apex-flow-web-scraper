import asyncio
import hashlib
import json
import re
import time
from urllib.parse import urlsplit
from .browser import render
from .extract import extract, validate_recipe
from .parser import parse
from .network import Fetcher
from .policy import ScrapeError, canonical_url, check_url


def validate_request(request, settings):
    if not isinstance(request, dict):
        raise ValueError("Job must be a JSON object")
    allowed = {"urls", "max_pages", "max_depth", "max_seconds", "max_bytes", "concurrency",
               "render", "recipe", "wait_selector", "scrolls", "include_paths", "exclude_paths", "cache",
               "max_age", "screenshot", "extract", "change_tracking", "follow_external", "chunks", "robots"}
    if set(request)-allowed:
        raise ValueError("Unknown job options: "+", ".join(set(request)-allowed))
    urls = request.get("urls")
    if not isinstance(urls, list) or not 1 <= len(urls) <= 100:
        raise ValueError("Provide 1–100 starting URLs")
    result = dict(request)
    result["urls"] = list(dict.fromkeys(check_url(u, settings.allow_test_loopback) for u in urls))
    for name, default, low, high in [("max_pages", 20, 1, 1000), ("max_depth", 1, 0, 10),
            ("max_seconds", 300, 5, 3600), ("max_bytes", 50*1024*1024, 1024, 500*1024*1024),
            ("concurrency", 3, 1, 4), ("scrolls", 0, 0, 10)]:
        value = result.get(name, default)
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ValueError(f"{name} must be an integer from {low} to {high}")
        result[name] = value
    if len(result["urls"]) > result["max_pages"]:
        raise ValueError("Starting URL count exceeds max_pages")
    result.setdefault("render", "auto")
    if result["render"] not in {"auto", "never", "always"}:
        raise ValueError("render must be auto, never or always")
    if result.setdefault("robots", "obey") not in {"obey", "requested"}:
        raise ValueError("robots must be obey or requested")
    result.setdefault("cache", True)
    if not isinstance(result["cache"], bool):
        raise ValueError("cache must be boolean")
    if not isinstance(result.setdefault("follow_external", False), bool):
        raise ValueError("follow_external must be boolean")
    max_age = result.get("max_age")
    if max_age is not None and (isinstance(max_age, bool) or not isinstance(max_age, int) or not 0 <= max_age <= 31536000000):
        raise ValueError("max_age must be milliseconds from 0 to 31536000000")
    if result.get("chunks") is not None:
        from .chunking import chunk_markdown
        chunk_markdown("", result["chunks"])  # validates the size
    for key in ("screenshot", "change_tracking"):
        if not isinstance(result.setdefault(key, False), bool):
            raise ValueError(key + " must be boolean")
    if result.get("extract"):
        from .llm import validate_extract_spec
        validate_extract_spec(result["extract"])
    if result.get("recipe"):
        validate_recipe(result["recipe"])
    selector = result.get("wait_selector")
    if selector:
        validate_recipe({"fields": {"wait": {"selector": selector}}})
    for key in ("include_paths", "exclude_paths"):
        value = result.setdefault(key, [])
        if not isinstance(value, list) or len(value) > 20 or any(not isinstance(v, str) or len(v) > 200 for v in value):
            raise ValueError(key+" must be a list of up to 20 path prefixes")
    return result


async def scrape(fetcher, url, options):
    started = time.monotonic()
    if options.get("robots") == "requested":
        fetcher.requested_origins.add("://".join(urlsplit(url)[:2]))
    response = await fetcher.get(url, cache=options.get("cache", True), max_age=options.get("max_age"))
    if response["status"] >= 400:
        raise ScrapeError("http_error", f"Site returned HTTP {response['status']}")
    result = await parse(response["body"], response["url"],
                                    response["headers"].get("content-type", "text/html"), options.get("recipe"))
    mode = options.get("render", "auto")
    rendered = False
    shot = None
    if result["kind"] == "html" and (mode == "always" or options.get("screenshot") or (mode == "auto" and result.get("quality", {}).get("needs_render"))):
        response = await render(fetcher, response["url"], options.get("wait_selector"), options.get("scrolls", 0),
                                screenshot=bool(options.get("screenshot")))
        shot = response.pop("screenshot", None)
        result = await parse(response["body"], response["url"], "text/html", options.get("recipe"))
        result["browser"] = response["browser"]
        rendered = True
    result.update(url=response["url"], status=response["status"], cache=response["cache"], rendered=rendered,
                  elapsed_ms=round((time.monotonic()-started)*1000, 2), engine="Apex Flow Web Scraper 0.2",
                  content_sha256=hashlib.sha256(result["text"].encode()).hexdigest(),
                  hosted_scraping_calls=0)
    result["receipt"] = {"requested_url": url, "final_url": response["url"], "status": response["status"],
                         "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                         "robots_txt": "disallowed_read_on_request" if getattr(fetcher, "robots_overridden", None) else "allowed",
                         "user_agent": fetcher.settings.user_agent, "rendered": rendered,
                         "content_sha256": result["content_sha256"], "source_sha256": result.get("source_sha256")}
    if options.get("chunks"):
        from .chunking import chunk_markdown
        result["chunks"] = chunk_markdown(result.get("markdown") or result["text"], options["chunks"])
    if shot:
        import base64
        result["screenshot_png_base64"] = base64.b64encode(shot).decode()
    if options.get("extract") and result["kind"] in {"html", "pdf", "docx", "text"}:
        from .llm import extract_json
        try:
            result["extracted"] = await extract_json(result.get("markdown") or result["text"], options["extract"])
        except ScrapeError as exc:
            result["extracted"] = {"data": None, "valid": False, "errors": [str(exc)], "error_code": exc.code}
    return result


async def run_job(store, ident, options):
    origins = {urlsplit(url).netloc for url in options["urls"]}
    seen_hashes = {}
    # Preserve duplicate evidence across resumed runs.
    with store.db() as db:
        for row in db.execute("SELECT url,result FROM pages WHERE job=? AND state='done'", (ident,)):
            result = json.loads(row[1])
            seen_hashes[result.get("content_sha256")] = row[0]
    browser_lock = asyncio.Lock()
    async with Fetcher(store.settings, store) as fetcher:
        fetcher.charge = lambda n: store.charge(ident, n, options["max_bytes"])
        async def page_task(item):
            url, depth = item["url"], item["depth"]
            try:
                # Rendering jobs are serialized on the small server; HTTP-only jobs can fan out.
                if options["render"] != "never":
                    async with browser_lock:
                        result = await scrape(fetcher, url, options)
                else:
                    result = await scrape(fetcher, url, options)
                digest = result["content_sha256"]
                duplicate = seen_hashes.get(digest)
                if duplicate:
                    result["duplicate_of"] = duplicate
                else:
                    seen_hashes[digest] = url
                if options.get("change_tracking"):
                    result["change"] = store.change_against_previous(url, ident, result)
                store.save_page(ident, url, result=result)
                if depth < options["max_depth"] and not duplicate:
                    links = []
                    for link in result.get("links", []):
                        p = urlsplit(link["url"])
                        if p.netloc not in origins and not options.get("follow_external"):
                            continue
                        if options["include_paths"] and not any(p.path.startswith(x) for x in options["include_paths"]):
                            continue
                        if any(p.path.startswith(x) for x in options["exclude_paths"]):
                            continue
                        links.append(link["url"])
                    store.enqueue(ident, links, depth+1, options["max_pages"])
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                store.save_page(ident, url, error={"code": getattr(exc, "code", "extraction_failed"),
                                                 "message": str(exc)[:500]})
        while True:
            status = store.status(ident)
            if status["cancel"]:
                store.finish(ident, "cancelled")
                return
            if time.time() >= status["deadline"] or status["bytes"] >= options["max_bytes"]:
                store.finish(ident, "limited", "Job time or transfer budget reached")
                return
            items = store.claim_pages(ident, options["concurrency"])
            if not items:
                counts = status["counts"]
                final = "partial" if counts.get("failed") and counts.get("done") else "failed" if counts.get("failed") else "completed"
                store.finish(ident, final)
                return
            tasks = [asyncio.create_task(page_task(item)) for item in items]
            pending = set(tasks)
            heartbeat = time.monotonic()
            while pending:
                _, pending = await asyncio.wait(pending, timeout=0.4)
                status = store.status(ident)
                stop = status["cancel"] or time.time() >= status["deadline"] or status["bytes"] >= options["max_bytes"]
                if stop:
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    store.finish(ident, "cancelled" if status["cancel"] else "limited", "Cancelled" if status["cancel"] else "Budget reached")
                    return
                if time.monotonic()-heartbeat >= 5:
                    store.event(ident, f"Working: {len(pending)} page(s); {fetcher.transferred} bytes fetched")
                    heartbeat = time.monotonic()


async def one_page(settings, store, url, render_mode="auto", recipe=None):
    options = validate_request({"urls": [url], "max_pages": 1, "max_depth": 0, "render": render_mode,
                                **({"recipe": recipe} if recipe else {})}, settings)
    async with Fetcher(settings, store) as fetcher:
        async with asyncio.timeout(50):
            return await scrape(fetcher, options["urls"][0], options)
