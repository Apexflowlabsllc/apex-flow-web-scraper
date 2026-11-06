"""Authenticated hosted-style API: Bearer key, credit metering, one scrape per request. Binds to loopback by default."""
import asyncio
import logging
import json
import time
from collections import defaultdict, deque
from aiohttp import web
from .billing import Billing, QuotaExceeded, cost_of
from .client import ascrape
from .policy import ScrapeError

ALLOWED = {"url", "render", "chunks", "extract", "max_age", "screenshot", "wait_selector", "recipe"}


def credits_for(body, rendered):
    kind = "render" if rendered or body.get("screenshot") else "page"
    total = cost_of(kind)
    if body.get("extract"):
        total += cost_of("extract")
    return total


def make_app(billing, scrape_fn=ascrape, hosted=None):
    active = defaultdict(int)
    recent = defaultdict(deque)
    total_active = 0
    rendering_active = 0
    @web.middleware
    async def guard(request, handler):
        if hosted and not request.path.startswith("/v1/"):
            return await handler(request)
        header = request.headers.get("Authorization", "")
        key = header[7:] if header.startswith("Bearer ") else ""
        account = billing.account_for(key) if key else None
        if not account:
            return web.json_response({"error": "missing or invalid API key"}, status=401)
        if hosted and not hosted.entitled(account):
            return web.json_response({"error": "Subscription is inactive or its paid period has ended. Open billing from your dashboard."}, status=402)
        request["account"] = account
        try:
            response = await handler(request)
        except (ValueError, KeyError, TypeError, ScrapeError) as exc:
            response = web.json_response({"error": str(exc)}, status=400)
        response.headers.update({"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})
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
        if hosted and body.get("extract"):
            return web.json_response({"error": "Model extraction is available in the self-hosted edition. Hosted scraping returns Markdown, text and chunks."}, status=400)
        now = time.monotonic()
        q = recent[account]
        while q and q[0] < now - 60:
            q.popleft()
        limits = billing.limits(account)
        may_render = body.get("render", "auto") != "never" or bool(body.get("screenshot"))
        if len(q) >= limits["rpm"] or active[account] >= limits["concurrency"] or total_active >= 4 or (may_render and rendering_active >= 2):
            return web.json_response({"error": "Request limit reached. Please retry shortly."}, status=429, headers={"Retry-After": "10"})
        worst = credits_for(body, rendered=body.get("render", "auto") != "never")
        left = billing.remaining(account)
        if worst > left:
            return web.json_response({"error": f"needs up to {worst} credits, {left} left", "credits_left": left}, status=402)
        try:
            if hosted:
                from .hosted_features import admit_attempt
                admit_attempt(hosted, account)
            billing.reserve(account, worst)
        except QuotaExceeded as exc:
            return web.json_response({"error": str(exc)}, status=402)
        q.append(now)
        active[account] += 1
        total_active += 1
        rendering_active += int(may_render)
        started = time.monotonic()
        try:
            url = body.pop("url")
            async with asyncio.timeout(90):
                result = await scrape_fn(url, **body)
            if hosted and len(json.dumps(dict(result)).encode()) > 262144:
                raise ValueError("Result exceeds the 256 KiB hosted response limit. Use a smaller source or the self-hosted edition.")
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
        left = billing.remaining(account)
        if hosted:
            hosted.save_page(account, result)
        return web.json_response({"credits_used": actual, "credits_left": left, "result": dict(result)})

    async def batch(request):
        started = time.monotonic()
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("Expected a JSON object")
        urls = body.pop("urls", None)
        if not isinstance(urls, list) or not 1 <= len(urls) <= 10 or any(not isinstance(u,str) for u in urls) or set(body) - (ALLOWED - {"url"}):
            raise ValueError("Supply 1–10 urls and supported scrape options")
        results=[]
        for url in urls:
            try:
                async with asyncio.timeout(max(0.1, 80-(time.monotonic()-started))):
                    response=await perform(request["account"], {**body,"url":url})
                results.append({"url":url,"status":response.status,**json.loads(response.body)})
            except TimeoutError:
                results.append({"url":url,"status":504,"error":"Batch deadline reached; this request was refunded."})
                break
            except (ValueError,KeyError,TypeError,ScrapeError) as exc:
                results.append({"url":url,"status":400,"error":str(exc)})
            except web.HTTPException as exc:
                results.append({"url":url,"status":exc.status,"error":exc.reason})
                if exc.status==429:
                    break
            except Exception:
                results.append({"url":url,"status":502,"error":"Source request failed; reserved credits were returned."})
        return web.json_response({"results":results,"processed":len(results),"unprocessed":urls[len(results):],
                                  "credits_left":billing.remaining(request["account"])})

    async def usage(request):
        return web.json_response({"credits_left": billing.remaining(request["account"])})

    app = web.Application(middlewares=[guard], client_max_size=50000)
    app.router.add_post("/v1/scrape", scrape)
    app.router.add_get("/v1/usage", usage)
    app.router.add_post("/v1/batch", batch)
    if hosted:
        hosted.install(app)
    return app


def run(host="127.0.0.1", port=8850, path=None):
    from .settings import Settings
    billing = Billing(path or Settings().data_dir / "billing.db")
    web.run_app(make_app(billing), host=host, port=port, access_log=None)
