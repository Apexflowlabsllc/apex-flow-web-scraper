"""Whole-web search for hosted accounts using one explicitly configured provider."""
import os
from urllib.parse import urlsplit

import aiohttp
from aiohttp import web

from .billing import QuotaExceeded, cost_of

_PROVIDER_CREDITS = {
    "brave": ("APEX_SEARCH_BRAVE_CREDITS", 130),
    "tavily": ("APEX_SEARCH_TAVILY_CREDITS", 210),
    "exa": ("APEX_SEARCH_EXA_BASE_CREDITS", 180),
}
_EXA_EXTRA_CREDITS = ("APEX_SEARCH_EXA_EXTRA_RESULT_CREDITS", 25)


_PROVIDER_KEYS = {
    "brave": "BRAVE_SEARCH_API_KEY",
    "tavily": "TAVILY_API_KEY",
    "exa": "EXA_API_KEY",
}


def local_provider_name(env=None):
    """Select a provider for local BYOK calls without hosted billing settings."""
    env = os.environ if env is None else env
    available = [name for name, key in _PROVIDER_KEYS.items() if env.get(key)]
    selected = env.get("APEX_SEARCH_PROVIDER", "").strip().lower()
    name = selected if selected else (available[0] if len(available) == 1 else None)
    return name if name in _PROVIDER_KEYS and name in available else None


async def local_search(query, count):
    """Search directly with a provider key held in this process environment."""
    name = local_provider_name()
    fetch = {"brave": brave, "tavily": tavily, "exa": exa}.get(name)
    if fetch is None:
        raise ValueError("Configure exactly one local provider key (BRAVE_SEARCH_API_KEY, TAVILY_API_KEY, or EXA_API_KEY); set APEX_SEARCH_PROVIDER when more than one is present.")
    return await fetch(query, count)


def provider_name():
    """Return the one provider selected for this process, or None if unavailable/ambiguous."""
    available = [name for name, key in _PROVIDER_KEYS.items() if os.environ.get(key)]
    selected = os.environ.get("APEX_SEARCH_PROVIDER", "").strip().lower()
    name = selected if selected else (available[0] if len(available) == 1 else None)
    if name not in available or name not in _PROVIDER_CREDITS:
        return None
    cost_env, default_cost = _PROVIDER_CREDITS[name]
    if _positive_credit_config(cost_env, default_cost) is None:
        return None
    if name == "exa" and _positive_credit_config(*_EXA_EXTRA_CREDITS) is None:
        return None
    return name


def _positive_credit_config(key, default):
    raw = os.environ.get(key)
    if raw is None:
        return default
    if not raw.isascii() or not raw.isdecimal():
        return None
    value = int(raw)
    return value if 1 <= value <= 100000 else None


def credit_reservation(name, limit):
    """Credits to reserve and charge for this request, including Exa result overage."""
    if name not in _PROVIDER_CREDITS:
        return cost_of("search")
    env, default = _PROVIDER_CREDITS[name]
    base = _positive_credit_config(env, default)
    if base is None:
        raise ValueError("invalid search provider credit configuration")
    if name != "exa":
        return base
    extra = _positive_credit_config(*_EXA_EXTRA_CREDITS)
    if extra is None:
        raise ValueError("invalid Exa result credit configuration")
    return base + extra * max(0, limit - 10)


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
        selected_provider = provider_name() if provider is None else None
        fetch = provider or (search if selected_provider else None)
        if fetch is None:
            return web.json_response({"error": "Web search is not enabled on this service yet. Use /v1/search for pages you have already scraped.",
                                      "code": "not_configured", "retryable": False}, status=503)
        query = body.get("query") if isinstance(body, dict) else None
        if not isinstance(query, str) or not 1 <= len(query) <= 300 or set(body) - {"query", "limit"}:
            raise ValueError("Use query (1–300 characters) and optional limit")
        limit = body.get("limit", 10)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
            raise ValueError("limit must be 1–20")
        reserved = credit_reservation(selected_provider, limit)
        try:
            h.billing.reserve(account, reserved, kind="search")
        except QuotaExceeded:
            return web.json_response({"error": "Not enough credits", "code": "insufficient_credits", "retryable": False,
                                      "credits_left": h.billing.remaining(account), "buy_url": h.site + "/#pricing"}, status=402)
        try:
            results = await fetch(query, limit)
        except BaseException:
            h.billing.refund(account, reserved)
            raise
        credits_used = reserved
        return web.json_response({"results": results, "credits_used": credits_used, "credits_left": h.billing.remaining(account),
                                  "note": "Pass a result url to /v1/scrape to read it."})

    async def websearch(request):
        return await search_for(request["account"], await request.json())

    app.router.add_post("/v1/websearch", websearch)
    return {"/websearch": search_for}
