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
               "render", "recipe", "wait_selector", "scrolls", "include_paths", "exclude_paths", "cache"}
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
    result.setdefault("cache", True)
    if not isinstance(result["cache"], bool):
        raise ValueError("cache must be boolean")
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
    response = await fetcher.get(url, cache=options.get("cache", True))
    if response["status"] >= 400:
        raise ScrapeError("http_error", f"Site returned HTTP {response['status']}")
    result = await parse(response["body"], response["url"],
                                    response["headers"].get("content-type", "text/html"), options.get("recipe"))
    mode = options.get("render", "auto")
    rendered = False
    if result["kind"] == "html" and (mode == "always" or (mode == "auto" and len(result["text"]) < 80)):
        response = await render(fetcher, response["url"], options.get("wait_selector"), options.get("scrolls", 0))
        result = await parse(response["body"], response["url"], "text/html", options.get("recipe"))
        result["browser"] = response["browser"]
        rendered = True
    result.update(url=response["url"], status=response["status"], cache=response["cache"], rendered=rendered,
                  elapsed_ms=round((time.monotonic()-started)*1000, 2), engine="Apex Flow Web Scraper 0.1",
                  content_sha256=hashlib.sha256(result["text"].encode()).hexdigest(),
                  hosted_scraping_calls=0)
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
                store.save_page(ident, url, result=result)
                if depth < options["max_depth"] and not duplicate:
                    links = []
                    for link in result.get("links", []):
                        p = urlsplit(link["url"])
                        if p.netloc not in origins:
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
