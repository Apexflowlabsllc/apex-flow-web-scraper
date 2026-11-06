"""Authenticated hosted-style API: Bearer key, credit metering, one scrape per request. Binds to loopback by default."""
import json
from aiohttp import web
from .billing import Billing, QuotaExceeded, cost_of
from .client import ascrape
from .policy import ScrapeError

ALLOWED = {"url", "render", "chunks", "extract", "max_age", "screenshot", "wait_selector"}


def credits_for(body, rendered):
    kind = "render" if rendered or body.get("screenshot") else "page"
    total = cost_of(kind)
    if body.get("extract"):
        total += cost_of("extract")
    return total


def make_app(billing, scrape_fn=ascrape):
    @web.middleware
    async def guard(request, handler):
        header = request.headers.get("Authorization", "")
        key = header[7:] if header.startswith("Bearer ") else ""
        account = billing.account_for(key) if key else None
        if not account:
            return web.json_response({"error": "missing or invalid API key"}, status=401)
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
        if not isinstance(body, dict) or not isinstance(body.get("url"), str) or set(body) - ALLOWED:
            raise ValueError("body must be a JSON object with a url and only these options: " + ", ".join(sorted(ALLOWED)))
        account = request["account"]
        worst = credits_for(body, rendered=body.get("render", "auto") != "never")
        left = billing.remaining(account)
        if worst > left:
            return web.json_response({"error": f"needs up to {worst} credits, {left} left", "credits_left": left}, status=402)
        url = body.pop("url")
        result = await scrape_fn(url, **body)
        actual = credits_for(body, rendered=bool(result.get("rendered")))
        try:
            left = billing.charge(account, "render" if result.get("rendered") or body.get("screenshot") else "page")
            if body.get("extract"):
                left = billing.charge(account, "extract")
        except QuotaExceeded as exc:
            return web.json_response({"error": str(exc)}, status=402)
        return web.json_response({"credits_used": actual, "credits_left": left, "result": dict(result)})

    async def usage(request):
        return web.json_response({"credits_left": billing.remaining(request["account"])})

    app = web.Application(middlewares=[guard], client_max_size=50000)
    app.router.add_post("/v1/scrape", scrape)
    app.router.add_get("/v1/usage", usage)
    return app


def run(host="127.0.0.1", port=8850, path=None):
    from .settings import Settings
    billing = Billing(path or Settings().data_dir / "billing.db")
    web.run_app(make_app(billing), host=host, port=port, access_log=None)
