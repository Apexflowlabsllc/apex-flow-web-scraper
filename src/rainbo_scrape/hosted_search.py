"""Whole-web search for hosted accounts using one explicitly configured provider."""
import os
import ipaddress
import re
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


async def local_search(query, count, include_domains=None, exclude_domains=None, topic="general"):
    """Search directly with a provider key held in this process environment."""
    name = local_provider_name()
    fetch = {"brave": brave, "tavily": tavily, "exa": exa}.get(name)
    if fetch is None:
        raise ValueError("Configure exactly one local provider key (BRAVE_SEARCH_API_KEY, TAVILY_API_KEY, or EXA_API_KEY); set APEX_SEARCH_PROVIDER when more than one is present.")
    if topic not in ("general", "news", "images"):
        raise ValueError("topic must be 'general', 'news', or 'images'")
    if topic == "images" and name != "brave":
        raise ValueError("Image search requires the configured Brave provider's dedicated image index")
    if topic == "news" and name == "brave":
        raise ValueError("The configured Brave provider does not expose a dedicated news source; use Tavily or Exa")
    include_domains, exclude_domains = normalize_domain_filters(include_domains, exclude_domains)
    if topic == "images":
        results = await brave_images(query, count, include_domains, exclude_domains)
    else:
        results = await fetch(query, count, include_domains, exclude_domains, topic) if topic == "news" else await fetch(query, count, include_domains, exclude_domains)
    if include_domains is not None or exclude_domains is not None:
        results = [item for item in results if _domain_allowed(item["url"], include_domains, exclude_domains)]
    return results[:count]


_DOMAIN_RE = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def normalize_domain_filters(include_domains=None, exclude_domains=None):
    """Validate hostname-only domain filters shared by native and v2 search."""
    if include_domains is not None and exclude_domains is not None:
        raise ValueError("includeDomains and excludeDomains cannot be used together")

    def normalize(value, name):
        if value is None:
            return None
        if not isinstance(value, list) or not 1 <= len(value) <= 50:
            raise ValueError(f"{name} must be a list of 1–50 hostnames")
        result = []
        for item in value:
            if not isinstance(item, str) or not item or item != item.strip():
                raise ValueError(f"{name} entries must be hostnames without protocol or path")
            try:
                domain = item.rstrip(".").encode("idna").decode("ascii").lower()
            except UnicodeError as exc:
                raise ValueError(f"{name} contains an invalid hostname") from exc
            try:
                ipaddress.ip_address(domain)
            except ValueError:
                if not _DOMAIN_RE.fullmatch(domain):
                    raise ValueError(f"{name} entries must be valid hostnames without protocol or path")
            if domain not in result:
                result.append(domain)
        return result

    return normalize(include_domains, "includeDomains"), normalize(exclude_domains, "excludeDomains")


def _brave_query(query, include_domains, exclude_domains):
    if include_domains:
        # Brave's documented site: operator is used for server-side filtering;
        # the same filter is also applied locally after normalization.
        query += " (" + " OR ".join("site:" + domain for domain in include_domains) + ")"
    if exclude_domains:
        query += " " + " ".join("-site:" + domain for domain in exclude_domains)
    return query


def _domain_allowed(url, include_domains, exclude_domains):
    if not isinstance(url, str):
        return False
    try:
        hostname = (urlsplit(url).hostname or "").lower().rstrip(".")
    except (TypeError, ValueError):
        return False
    if include_domains and not any(hostname == d or hostname.endswith("." + d) for d in include_domains):
        return False
    if exclude_domains and any(hostname == d or hostname.endswith("." + d) for d in exclude_domains):
        return False
    return True


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


async def brave(query, count, include_domains=None, exclude_domains=None, topic="general"):
    if topic != "general":
        raise ValueError("The configured Brave provider does not expose a dedicated news source; use Tavily or Exa")
    headers = {"X-Subscription-Token": os.environ["BRAVE_SEARCH_API_KEY"], "Accept": "application/json"}
    query = _brave_query(query, include_domains, exclude_domains)
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as client:
        async with client.get("https://api.search.brave.com/res/v1/web/search", params={"q": query, "count": str(count)},
                              headers=headers) as response:
            if response.status != 200:
                raise aiohttp.ClientError("search provider returned HTTP %s" % response.status)
            data = await response.json()
    return _normalize(data.get("web", {}).get("results", []), "description", count)


async def brave_images(query, count, include_domains=None, exclude_domains=None):
    """Search Brave's dedicated image index; returns image and source-page URLs."""
    headers = {"X-Subscription-Token": os.environ["BRAVE_SEARCH_API_KEY"], "Accept": "application/json"}
    query = _brave_query(query, include_domains, exclude_domains)
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as client:
        async with client.get("https://api.search.brave.com/res/v1/images/search",
                              params={"q": query, "count": str(min(count, 200)), "safesearch": "strict"},
                              headers=headers) as response:
            if response.status != 200:
                raise aiohttp.ClientError("search provider returned HTTP %s" % response.status)
            data = await response.json()
    normalized = []
    for result in data.get("results", []):
        if not isinstance(result, dict):
            continue
        properties = result.get("properties") if isinstance(result.get("properties"), dict) else {}
        thumbnail = result.get("thumbnail") if isinstance(result.get("thumbnail"), dict) else {}
        image_url = properties.get("url") or thumbnail.get("src")
        page_url = result.get("url")
        if not isinstance(image_url, str) or not isinstance(page_url, str):
            continue
        try:
            image_parsed, page_parsed = urlsplit(image_url), urlsplit(page_url)
        except ValueError:
            continue
        if image_parsed.scheme not in ("http", "https") or not image_parsed.hostname:
            continue
        if page_parsed.scheme not in ("http", "https") or not page_parsed.hostname:
            continue
        normalized.append({"title": result.get("title") or "", "imageUrl": image_url,
                           "imageWidth": properties.get("width") or thumbnail.get("width"),
                           "imageHeight": properties.get("height") or thumbnail.get("height"),
                           "url": page_url})
        if len(normalized) >= count:
            break
    return normalized


async def tavily(query, count, include_domains=None, exclude_domains=None, topic="general"):
    headers = {"Authorization": "Bearer " + os.environ["TAVILY_API_KEY"], "Content-Type": "application/json"}
    payload = {"query": query, "topic": topic, "search_depth": "basic", "max_results": count,
               "include_answer": False, "include_raw_content": False}
    if include_domains is not None:
        payload["include_domains"] = include_domains
    if exclude_domains is not None:
        payload["exclude_domains"] = exclude_domains
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as client:
        async with client.post("https://api.tavily.com/search", json=payload, headers=headers) as response:
            if response.status != 200:
                raise aiohttp.ClientError("search provider returned HTTP %s" % response.status)
            data = await response.json()
    return _normalize(data.get("results", []), "content", count)


async def exa(query, count, include_domains=None, exclude_domains=None, topic="general"):
    headers = {"x-api-key": os.environ["EXA_API_KEY"], "Content-Type": "application/json"}
    payload = {"query": query, "type": "auto", "numResults": count, "contents": {"highlights": True}}
    if topic == "news":
        payload["category"] = "news"
    if include_domains is not None:
        payload["includeDomains"] = include_domains
    if exclude_domains is not None:
        payload["excludeDomains"] = exclude_domains
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
        item = {"url": url, "title": result.get("title") or "", "snippet": snippet}
        published = result.get("publishedDate") or result.get("published_date")
        if isinstance(published, str):
            item["date"] = published
        normalized.append(item)
        if len(normalized) >= count:
            break
    return normalized


async def search(query, count, options=None):
    name = provider_name()
    fetch = {"brave": brave, "tavily": tavily, "exa": exa}.get(name)
    if fetch is None:
        raise aiohttp.ClientError("search provider is not configured unambiguously")
    options = options or {}
    topic = options.get("topic", "general")
    if topic not in ("general", "news", "images"):
        raise ValueError("topic must be 'general', 'news', or 'images'")
    if topic == "images" and name != "brave":
        raise ValueError("Image search requires the configured Brave provider's dedicated image index")
    if topic == "news" and name == "brave":
        raise ValueError("The configured Brave provider does not expose a dedicated news source; use Tavily or Exa")
    include_domains, exclude_domains = normalize_domain_filters(
        options.get("include_domains"), options.get("exclude_domains"))
    if topic == "images":
        results = await brave_images(query, count, include_domains, exclude_domains)
    elif topic == "news":
        results = await fetch(query, count, include_domains, exclude_domains, topic)
    else:
        results = await fetch(query, count, include_domains, exclude_domains)
    if include_domains is not None or exclude_domains is not None:
        results = [item for item in results if _domain_allowed(item["url"], include_domains, exclude_domains)]
    return results[:count]


def install(h, app, provider=None):
    async def search_for(account, body):
        selected_provider = provider_name() if provider is None else None
        fetch = provider or (search if selected_provider else None)
        if fetch is None:
            return web.json_response({"error": "Web search is not enabled on this service yet. Use /v1/search for pages you have already scraped.",
                                      "code": "not_configured", "retryable": False}, status=503)
        query = body.get("query") if isinstance(body, dict) else None
        allowed = {"query", "limit", "include_domains", "exclude_domains", "topic"}
        if not isinstance(query, str) or not 1 <= len(query) <= 300 or set(body) - allowed:
            raise ValueError("Use query (1–300 characters), optional limit, and optional domain filters")
        limit = body.get("limit", 10)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
            raise ValueError("limit must be 1–20")
        include_domains, exclude_domains = normalize_domain_filters(body.get("include_domains"), body.get("exclude_domains"))
        topic = body.get("topic", "general")
        if topic not in ("general", "news", "images"):
            raise ValueError("topic must be 'general', 'news', or 'images'")
        if topic == "news" and selected_provider == "brave":
            raise ValueError("The configured Brave provider does not expose a dedicated news source; use Tavily or Exa")
        if topic == "images" and selected_provider != "brave":
            raise ValueError("Image search requires the configured Brave provider's dedicated image index")
        search_options = {"include_domains": include_domains, "exclude_domains": exclude_domains}
        if topic in ("news", "images"):
            search_options["topic"] = topic
        reserved = credit_reservation(selected_provider, limit)
        try:
            h.billing.reserve(account, reserved, kind="search")
        except QuotaExceeded:
            return web.json_response({"error": "Not enough credits", "code": "insufficient_credits", "retryable": False,
                                      "credits_left": h.billing.remaining(account), "buy_url": h.site + "/#pricing"}, status=402)
        try:
            results = await fetch(query, limit, search_options)
            if include_domains is not None or exclude_domains is not None:
                results = [item for item in results if isinstance(item, dict)
                           and _domain_allowed(item.get("url", ""), include_domains, exclude_domains)]
                results = results[:limit]
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
