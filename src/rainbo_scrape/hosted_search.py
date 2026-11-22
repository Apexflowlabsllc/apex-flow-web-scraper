"""Whole-web search for hosted accounts using one explicitly configured provider."""
import os
from urllib.parse import urlsplit

import aiohttp
from aiohttp import web

from .billing import QuotaExceeded, cost_of

CREDITS = cost_of("search")  # keep endpoint reservation aligned with the central price table


_PROVIDER_KEYS = {
    "brave": "BRAVE_SEARCH_API_KEY",
    "tavily": "TAVILY_API_KEY",
    "exa": "EXA_API_KEY",
}


def provider_name():
    """Return the one provider selected for this process, or None if unavailable/ambiguous."""
    available = [name for name, key in _PROVIDER_KEYS.items() if os.environ.get(key)]
    selected = os.environ.get("APEX_SEARCH_PROVIDER", "").strip().lower()
    if selected:
        return selected if selected in available else None
    return available[0] if len(available) == 1 else None


def configured():
    return provider_name() is not None


async def brave(query, count):
    headers = {"X-Subscription-Token": os.environ["BRAVE_SEARCH_API_KEY"], "Accept": "application/json"}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as client:
        async with client.get("https://api.search.brave.com/res/v1/web/search", params={"q": query, "count": str(count)},
                              headers=headers) as response:
            if response.status != 200:
                raise aiohttp.ClientError("search provider returned HTTP %s" % response.status)
            data = await response.json()
    return _normalize(data.get("web", {}).get("results", []), "description", count)


async def tavily(query, count):
    headers = {"Authorization": "Bearer " + os.environ["TAVILY_API_KEY"], "Content-Type": "application/json"}
    payload = {"query": query, "topic": "general", "search_depth": "basic", "max_results": count,
               "include_answer": False, "include_raw_content": False}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as client:
        async with client.post("https://api.tavily.com/search", json=payload, headers=headers) as response:
            if response.status != 200:
                raise aiohttp.ClientError("search provider returned HTTP %s" % response.status)
            data = await response.json()
    return _normalize(data.get("results", []), "content", count)


async def exa(query, count):
    headers = {"x-api-key": os.environ["EXA_API_KEY"], "Content-Type": "application/json"}
    payload = {"query": query, "type": "auto", "numResults": count, "contents": {"highlights": True}}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as client:
        async with client.post("https://api.exa.ai/search", json=payload, headers=headers) as response:
            if response.status != 200:
                raise aiohttp.ClientError("search provider returned HTTP %s" % response.status)
            data = await response.json()
    return _normalize(data.get("results", []), "highlights", count)


def _normalize(results, snippet_field, count):
    normalized = []
    for result in results:
        url = result.get("url")
        if not isinstance(url, str):
            continue
        try:
            parsed = urlsplit(url)
        except ValueError:
            continue
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            continue
        snippet = result.get(snippet_field)
        if isinstance(snippet, list):
            snippet = " ".join(item for item in snippet if isinstance(item, str))
        if not isinstance(snippet, str):
            snippet = result.get("text") or result.get("summary") or ""
        normalized.append({"url": url, "title": result.get("title") or "", "snippet": snippet})
        if len(normalized) >= count:
            break
    return normalized


async def search(query, count):
    name = provider_name()
    fetch = {"brave": brave, "tavily": tavily, "exa": exa}.get(name)
    if fetch is None:
        raise aiohttp.ClientError("search provider is not configured unambiguously")
    return await fetch(query, count)


def install(h, app, provider=None):
    async def search_for(account, body):
        fetch = provider or (search if configured() else None)
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
