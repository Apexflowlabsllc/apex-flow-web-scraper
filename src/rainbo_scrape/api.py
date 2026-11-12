"""Authenticated hosted-style API: Bearer key, credit metering, one scrape per request. Binds to loopback by default."""
import asyncio
import logging
import json
import secrets
import time
from collections import defaultdict, deque
from functools import partial
from aiohttp import web
from .billing import Billing, QuotaExceeded, cost_of
from .client import ascrape
from .policy import ScrapeError

ALLOWED = {"url", "render", "chunks", "extract", "max_age", "screenshot", "wait_selector", "recipe", "outputs", "page", "fallback", "max_tokens"}
LIMIT = 262144  # hosted response budget in bytes
OUTPUTS = {"markdown", "text", "data", "evidence", "chunks", "links", "structured_data", "diff"}
SOURCE_FIELDS = {"url", "title", "receipt", "warnings", "untrusted_source", "validation_errors", "archive", "extracted"}
# Failures where the live site refused or could not serve us; a public archive copy may still exist.
ARCHIVABLE = {"access_challenge", "http_error", "upstream_error", "rate_limited", "fetch_failed", "dns_failed",
              "robots_denied", "robots_unavailable"}
HINT = 'The live site did not serve this page. Retry with "fallback":"archive" to read the latest Internet Archive copy, labelled with its capture time.'
RETRYABLE = {408, 429, 500, 502, 503, 504}
CODES = {400: "invalid_request", 401: "invalid_key", 402: "insufficient_credits", 413: "request_too_large",
         415: "unsupported_media_type", 429: "rate_limited", 502: "source_failed", 504: "timeout"}


def fail(status, message, code=None, headers=None, **extra):
    """One error shape for REST and MCP: a stable code, whether a retry can help, and what to do next."""
    body = {"error": message, "code": code or CODES.get(status, "error"), "retryable": status in RETRYABLE, **extra}
    return web.json_response(body, status=status, headers=headers)


async def find_snapshot(url):
    """Latest Internet Archive capture of a URL, or None. Only archive.org is contacted."""
    import aiohttp
    from urllib.parse import quote
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as client:
            async with client.get("https://archive.org/wayback/available?url=" + quote(url, safe="")) as response:
                closest = (await response.json(content_type=None)).get("archived_snapshots", {}).get("closest") or {}
    except Exception:
        return None
    stamp = str(closest.get("timestamp", ""))
    if not closest.get("available") or closest.get("status") != "200" or not (stamp.isdigit() and len(stamp) == 14):
        return None
    return stamp


def failure(exc, asked_archive=False):
    """Error fields for a scrape that failed: the engine's own code and, where it applies, the archive hint."""
    code = getattr(exc, "code", None) if isinstance(exc, ScrapeError) else None
    fields = {"code": code or "invalid_request", "retryable": code in {"rate_limited", "upstream_error", "fetch_failed"}}
    if code in ARCHIVABLE and not asked_archive:
        fields["hint"] = HINT
    return fields


def _size(value):
    return len(json.dumps(value).encode())


def _part(result, page, pages, piece=20000):
    """One of `pages` consecutive parts: long text is cut on line breaks and long lists are sliced."""
    out = {}
    for field, value in result.items():
        if field in ("markdown", "text") and isinstance(value, str) and len(value) > 4000:
            # Cut on line breaks; a single very long line is cut every 20,000 characters.
            pieces = [line[i:i + piece] for line in value.splitlines(keepends=True) for i in range(0, len(line), piece)]
            target, parts, current, used = len(value) / pages, [], [], 0
            for piece in pieces:
                if used >= target and len(parts) < pages - 1:
                    parts.append("".join(current))
                    current, used = [], 0
                current.append(piece)
                used += len(piece)
            parts.append("".join(current))
            out[field] = parts[page - 1] if page <= len(parts) else ""
        elif isinstance(value, list) and _size(value) > 8000:
            step = -(-len(value) // pages)
            out[field] = value[(page - 1) * step:page * step]
        else:
            out[field] = value
    return out


def paginate(result, page, limit=LIMIT, max_tokens=None):
    """Large documents are returned in parts that fit the response budget, or the caller's token budget, instead of failing."""
    least, piece = 1, 20000
    if max_tokens:
        budget = max_tokens * 4          # about four characters per token
        longest = max((len(result[f]) for f in ("markdown", "text") if isinstance(result.get(f), str)), default=0)
        least, piece = max(1, -(-longest // budget)), max(200, min(20000, budget // 4))
        if least > 60:
            raise ValueError("max_tokens is too small for this document; it would need more than 60 parts")
    if least == 1 and _size(result) <= limit:
        if page != 1:
            raise ValueError("page is out of range; this result has 1 page")
        return result, None
    for pages in range(max(2, least), 61):
        if all(_size(_part(result, n, pages, piece)) <= limit - 400 for n in range(1, pages + 1)):
            if page > pages:
                raise ValueError(f"page is out of range; this result has {pages} pages")
            return _part(result, page, pages, piece), {
                "page": page, "pages": pages, "next_page": page + 1 if page < pages else None,
                "note": "Document returned in parts. Request the next page with the same options; send max_age to reuse the fetch."}
    raise ValueError("Result exceeds the 256 KiB hosted response limit. Use a smaller source or the self-hosted edition.")


def credits_for(body, rendered):
    kind = "render" if rendered or body.get("screenshot") else "page"
    total = cost_of(kind)
    if body.get("extract"):
        total += cost_of("extract")
    return total


def make_app(billing, scrape_fn=ascrape, hosted=None, snapshot_fn=find_snapshot, search_provider=None):
    active = defaultdict(int)
    recent = defaultdict(deque)
    api_recent = defaultdict(deque)
    real_engine = scrape_fn is ascrape
    if hosted and scrape_fn is ascrape:
        scrape_fn = partial(ascrape, browser_slots=asyncio.BoundedSemaphore(2))
    total_limit = 20 if hosted else 4
    total_active = 0
    rendering_active = 0
    buy = {"buy_url": hosted.site + "/#pricing"} if hosted else {}

    @web.middleware
    async def guard(request, handler):
        if hosted and not request.path.startswith("/v1/"):
            return await handler(request)
        header = request.headers.get("Authorization", "")
        key = header[7:] if header.startswith("Bearer ") else ""
        account = billing.account_for(key) if key else None
        if not account:
            extra = {"docs": hosted.site + "/docs.html", "get_key_url": hosted.site + "/trial.html"} if hosted else {}
            return fail(401, "missing or invalid API key", **extra)
        if hosted and not hosted.entitled(account):
            return fail(402, "Subscription is inactive or its paid period has ended. Open billing from your dashboard.",
                        code="inactive_plan", **buy)
        # Search and usage are credit-free, but must not permit unbounded requests.
        if hosted:
            now = time.monotonic()
            requests = api_recent[account]
            while requests and requests[0] <= now - 60:
                requests.popleft()
            if len(requests) >= billing.limits(account)["rpm"]:
                return fail(429, "API request limit reached. Please retry shortly.", retry_after=60,
                            headers={"Retry-After": "60", "Cache-Control": "no-store"})
            requests.append(now)
        request["account"] = account
        try:
            response = await handler(request)
        except (ValueError, KeyError, TypeError, ScrapeError) as exc:
            details = failure(exc, getattr(exc, "asked_archive", False))
            response = fail(400, str(exc), details.pop("code"), **{k: v for k, v in details.items() if k != "retryable"})
        response.headers.update({"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store",
                                 "X-Request-Id": secrets.token_hex(8),
                                 "X-Credits-Remaining": str(billing.remaining(account))})
        if hosted:
            limit = billing.limits(account)["rpm"]
            response.headers.update({"X-RateLimit-Limit": str(limit),
                                     "X-RateLimit-Remaining": str(max(0, limit - len(api_recent[account])))})
        return response

    async def scrape(request):
        if request.content_type != "application/json":
            raise web.HTTPUnsupportedMediaType()
        body = await request.json()
        return await perform(request["account"], body)

    async def perform(account, body):
        nonlocal total_active, rendering_active
        if not isinstance(body, dict) or not isinstance(body.get("url"), str) or set(body) - ALLOWED:
            raise ValueError("body must be a JSON object with a url and only these options: " + ", ".join(sorted(ALLOWED)))
        page = body.pop("page", 1)
        if isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= 60:
            raise ValueError("page must be a whole number from 1 to 60")
        fallback = body.pop("fallback", None)
        if fallback not in (None, "archive"):
            raise ValueError('fallback must be "archive" when supplied')
        max_tokens = body.pop("max_tokens", None)
        if max_tokens is not None and (isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or not 200 <= max_tokens <= 60000):
            raise ValueError("max_tokens must be a whole number from 200 to 60000")
        outputs = body.pop("outputs", None)
        if outputs is not None:
            if not isinstance(outputs, list) or not 1 <= len(outputs) <= len(OUTPUTS) or any(
                not isinstance(field, str) or field not in OUTPUTS for field in outputs
            ):
                raise ValueError("outputs must list one or more of: " + ", ".join(sorted(OUTPUTS)))
            if "chunks" in outputs and "chunks" not in body:
                body["chunks"] = 1500
        if hosted and body.get("extract"):
            from .llm import providers
            if not providers():
                return fail(400, "Model extraction is available in the self-hosted edition. Hosted scraping returns Markdown, text and chunks.",
                            code="not_available_hosted")
        now = time.monotonic()
        q = recent[account]
        while q and q[0] < now - 60:
            q.popleft()
        limits = billing.limits(account)
        may_render = body.get("render", "auto") != "never" or bool(body.get("screenshot"))
        if len(q) >= limits["rpm"] or active[account] >= limits["concurrency"] or total_active >= total_limit or (not hosted and may_render and rendering_active >= 2):
            return fail(429, "Request limit reached. Please retry shortly.", retry_after=10, headers={"Retry-After": "10"})
        worst = credits_for(body, rendered=body.get("render", "auto") != "never")
        left = billing.remaining(account)
        if worst > left:
            return fail(402, f"needs up to {worst} credits, {left} left", credits_left=left, **buy)
        try:
            if hosted:
                from .hosted_features import admit_attempt
                admit_attempt(hosted, account)
            billing.reserve(account, worst)
        except QuotaExceeded as exc:
            return fail(402, str(exc), **buy)
        q.append(now)
        active[account] += 1
        total_active += 1
        rendering_active += int(may_render)
        started = time.monotonic()
        try:
            url = body.pop("url")
            async with asyncio.timeout(90):
                try:
                    result = await scrape_fn(url, **body)
                except ScrapeError as exc:
                    # The live site refused or failed. Only when asked, read the public Internet Archive copy instead.
                    exc.asked_archive = fallback == "archive"
                    stamp = await snapshot_fn(url) if fallback == "archive" and exc.code in ARCHIVABLE else None
                    if not stamp:
                        raise
                    result = dict(await scrape_fn(f"https://web.archive.org/web/{stamp}id_/{url}", **{**body, "render": "never"}))
                    result["url"] = url
                    result["archive"] = {
                        "provider": "Internet Archive Wayback Machine", "live_page_served": False, "reason": exc.code,
                        "captured": f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}T{stamp[8:10]}:{stamp[10:12]}:{stamp[12:14]}Z",
                        "snapshot_url": f"https://web.archive.org/web/{stamp}/{url}",
                        "note": "Archived copy, not the live page. Content may be out of date."}
                    result["warnings"] = list(result.get("warnings") or []) + ["archived_copy"]
            response_result = dict(result) if outputs is None else {
                field: value for field, value in result.items() if field in outputs or field in SOURCE_FIELDS
            }
            pagination = None
            if hosted:
                response_result, pagination = paginate(response_result, page, max_tokens=max_tokens)
            actual = credits_for(body, rendered=bool(result.get("rendered")))
            billing.refund(account, worst - actual)
        except BaseException:
            billing.refund(account, worst)
            raise
        finally:
            active[account] -= 1
            total_active -= 1
            rendering_active -= int(may_render)
            logging.info("scrape account=%s duration_ms=%d", account, (time.monotonic()-started)*1000)
        reply = {"credits_used": actual, "credits_left": billing.remaining(account), "result": response_result}
        longest = max((len(response_result[f]) for f in ("markdown", "text") if isinstance(response_result.get(f), str)), default=0)
        if longest:
            reply["estimated_tokens"] = -(-longest // 4)   # about four characters per token
        if hosted:
            # Compared with this account's previous copy of the same URL, so repeat reads show what moved.
            change = hosted.save_page(account, result, diff=bool(outputs and "diff" in outputs))
            if change:
                reply["change"] = change
            if pagination:
                reply["pagination"] = pagination
        return web.json_response(reply)

    async def batch(request):
        return await batch_for(request["account"], await request.json())

    async def batch_for(account, body):
        started = time.monotonic()
        if not isinstance(body, dict):
            raise ValueError("Expected a JSON object")
        urls = body.pop("urls", None)
        if not isinstance(urls, list) or not 1 <= len(urls) <= 10 or any(not isinstance(u,str) for u in urls) or set(body) - (ALLOWED - {"url"}):
            raise ValueError("Supply 1–10 urls and supported scrape options")
        # Run as many pages at once as the plan allows; results stay in the order the URLs were given.
        slots = max(1, min(len(urls), billing.limits(account)["concurrency"]))
        done = {}
        pending = iter(enumerate(urls))
        stop = False

        async def one(url):
            try:
                async with asyncio.timeout(max(0.1, 80-(time.monotonic()-started))):
                    response = await perform(account, {**body, "url": url})
                return {"url": url, "status": response.status, **json.loads(response.body)}
            except TimeoutError:
                return {"url": url, "status": 504, "code": "timeout", "retryable": True,
                        "error": "Batch deadline reached; this request was refunded."}
            except (ValueError, KeyError, TypeError, ScrapeError) as exc:
                return {"url": url, "status": 400, **failure(exc, getattr(exc, "asked_archive", False)), "error": str(exc)}
            except web.HTTPException as exc:
                return {"url": url, "status": exc.status, "code": CODES.get(exc.status, "error"),
                        "retryable": exc.status in RETRYABLE, "error": exc.reason}
            except Exception:
                return {"url": url, "status": 502, "code": "source_failed", "retryable": True,
                        "error": "Source request failed; reserved credits were returned."}

        async def worker():
            nonlocal stop
            for index, url in pending:
                if stop:
                    return
                item = await one(url)
                if item["status"] in (429, 504):
                    stop = True  # capacity or deadline reached: the rest is returned as unprocessed for a retry
                done[index] = item

        await asyncio.gather(*(worker() for _ in range(slots)))
        results = [done[i] for i in sorted(done)]
        return web.json_response({"results": results, "processed": len(results),
                                  "unprocessed": [u for i, u in enumerate(urls) if i not in done],
                                  "concurrency": slots, "duration_ms": int((time.monotonic()-started)*1000),
                                  "credits_left": billing.remaining(account)})

    async def usage_for(account, body=None):
        data = {"credits_left": billing.remaining(account)}
        if hosted:
            from .hosted_features import usage_details
            data.update(usage_details(hosted, account))
        return web.json_response(data)

    async def usage(request):
        return await usage_for(request["account"])

    app = web.Application(middlewares=[guard], client_max_size=50000)
    app.router.add_post("/v1/scrape", scrape)
    app.router.add_get("/v1/usage", usage)
    app.router.add_post("/v1/batch", batch)
    if hosted:
        from .mcp_http import install
        operations = hosted.install(app)
        operations.update({"/scrape": perform, "/batch": batch_for, "/usage": usage_for})
        from . import hosted_crawl, hosted_search
        hosted_crawl.prepare(hosted)
        operations.update(hosted_crawl.install(hosted, app, perform, hosted_crawl.sitemap_urls if real_engine else None))
        operations.update(hosted_search.install(hosted, app, search_provider))
        install(app, billing, operations, hosted.site)
    return app


def run(host="127.0.0.1", port=8850, path=None):
    from .settings import Settings
    billing = Billing(path or Settings().data_dir / "billing.db")
    web.run_app(make_app(billing), host=host, port=port, access_log=None)
