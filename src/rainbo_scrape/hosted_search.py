"""Whole-web search for hosted accounts. Runs only when the operator has set a search provider key."""
import os

import aiohttp
from aiohttp import web

from .billing import QuotaExceeded

CREDITS = 70  # a search costs us half a US cent; this stays above cost on every plan


def configured():
    return bool(os.environ.get("BRAVE_SEARCH_API_KEY"))


async def brave(query, count):
    headers = {"X-Subscription-Token": os.environ["BRAVE_SEARCH_API_KEY"], "Accept": "application/json"}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as client:
        async with client.get("https://api.search.brave.com/res/v1/web/search", params={"q": query, "count": str(count)},
                              headers=headers) as response:
            if response.status != 200:
                raise aiohttp.ClientError("search provider returned HTTP %s" % response.status)
            data = await response.json()
    return [{"url": r.get("url"), "title": r.get("title"), "snippet": r.get("description")}
            for r in data.get("web", {}).get("results", []) if r.get("url")][:count]


def install(h, app, provider=None):
    async def search_for(account, body):
        fetch = provider or (brave if configured() else None)
        if fetch is None:
            return web.json_response({"error": "Web search is not enabled on this service yet. Use /v1/search for pages you have already scraped.",
                                      "code": "not_configured", "retryable": False}, status=503)
        query = body.get("query") if isinstance(body, dict) else None
        if not isinstance(query, str) or not 1 <= len(query) <= 300 or set(body) - {"query", "limit"}:
            raise ValueError("Use query (1–300 characters) and optional limit")
        limit = body.get("limit", 10)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
            raise ValueError("limit must be 1–20")
        try:
            h.billing.reserve(account, CREDITS)
        except QuotaExceeded:
            return web.json_response({"error": "Not enough credits", "code": "insufficient_credits", "retryable": False,
                                      "credits_left": h.billing.remaining(account), "buy_url": h.site + "/#pricing"}, status=402)
        try:
            results = await fetch(query, limit)
        except BaseException:
            h.billing.refund(account, CREDITS)
            raise
        if not results:
            h.billing.refund(account, CREDITS)
        return web.json_response({"results": results, "credits_used": CREDITS if results else 0, "credits_left": h.billing.remaining(account),
                                  "note": "Pass a result url to /v1/scrape to read it."})

    async def search(request):
        return await search_for(request["account"], await request.json())

    app.router.add_post("/v1/websearch", search)
    return {"/websearch": search_for}
