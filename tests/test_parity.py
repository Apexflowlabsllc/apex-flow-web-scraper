"""0.6.0: hosted crawl jobs, optional web search and model extraction, larger plan limits."""
import asyncio
import json
import time
import pytest
from aiohttp.test_utils import TestClient, TestServer
from rainbo_scrape.api import make_app
from rainbo_scrape.billing import Billing, PLANS
from rainbo_scrape.hosted import Hosted, SITE
from rainbo_scrape.policy import ScrapeError
from rainbo_scrape import hosted_search
from test_advantages import account

SITE_PAGES = {f"https://docs.example/p{i}": [f"https://docs.example/p{j}" for j in (i + 1, i + 2, i * 2)] for i in range(0, 60)}


@pytest.fixture
async def svc(tmp_path, monkeypatch):
    for key in ("SCRAPER_GATEWAY_SECRET", "BRAVE_SEARCH_API_KEY", "TAVILY_API_KEY", "EXA_API_KEY", "APEX_SEARCH_PROVIDER",
                "APEX_SEARCH_BRAVE_CREDITS", "APEX_SEARCH_TAVILY_CREDITS", "APEX_SEARCH_EXA_BASE_CREDITS", "APEX_SEARCH_EXA_EXTRA_RESULT_CREDITS"):
        monkeypatch.delenv(key, raising=False)
    billing = Billing(tmp_path / "billing.db")
    h = Hosted(billing, "fixture", "fixture-webhook", "fixture-delivery")
    seen = []

    async def scrape(url, **kwargs):
        seen.append(url)
        await asyncio.sleep(0.005)
        if url.endswith("/p3"):
            raise ScrapeError("http_error", "Site returned HTTP 500")
        links = SITE_PAGES.get(url, []) + ["https://other.example/away", url + "#top", "https://docs.example/logo.png"]
        return {"url": url, "title": "Page", "markdown": "# " + url, "text": url, "rendered": False, "links": links,
                "receipt": {"final_url": url}}

    async def provider(query, count, options=None):
        if query == "nothing":
            return []
        if query == "filtered":
            return [{"url": "https://docs.example.com/guide", "title": "Allowed", "snippet": "s"},
                    {"url": "https://other.example.net/guide", "title": "Excluded", "snippet": "s"}]
        return [{"url": f"https://r.example/{i}", "title": query, "snippet": "s"} for i in range(count)]

    client = TestClient(TestServer(make_app(billing, scrape, hosted=h, search_provider=provider)))
    await client.start_server()
    yield h, client, seen
    await client.close()
    billing.db.close()


async def finish(client, auth, job):
    for _ in range(400):
        body = await (await client.get("/v1/crawl/" + job, headers=auth)).json()
        if body["status"] != "running":
            return body
        await asyncio.sleep(0.02)
    raise AssertionError("crawl did not finish")


async def test_crawl_follows_one_site_charges_only_successes_and_pages_results(svc):
    h, client, seen = svc
    acct, auth = account(h, "starter", "cs_test_crawl1")
    r = await client.post("/v1/crawl", json={"url": "https://docs.example/p0#intro", "limit": 12, "max_depth": 10}, headers=auth)
    started = await r.json()
    assert r.status == 200 and started["status"] == "running" and started["id"].startswith("crawl_")
    body = await finish(client, auth, started["id"])
    assert body["status"] == "completed" and body["pages_done"] == 12 and body["pages_failed"] == 1
    assert body["credits_used"] == 12 and h.billing.remaining(acct) == 110000 - 12       # the failed page was free
    assert all(u.startswith("https://docs.example/p") for u in seen) and len(seen) == len(set(seen))
    pages, offset = [], 0
    while offset is not None:
        chunk = await (await client.get(f"/v1/crawl/{started['id']}?offset={offset}", headers=auth)).json()
        pages += chunk["results"]
        offset = chunk["next_offset"]
    assert len(pages) == 13 and sum(p["status"] == 200 for p in pages) == 12
    good = next(p for p in pages if p["status"] == 200)
    assert good["result"]["markdown"].startswith("# https://docs.example/") and "links" not in good["result"] and good["change"]["status"] == "new"
    bad = next(p for p in pages if p["status"] != 200)
    assert bad["code"] == "http_error" and bad["url"].endswith("/p3")
    # Another account cannot read it.
    _, other = account(h, "starter", "cs_test_crawl2")
    assert (await client.get("/v1/crawl/" + started["id"], headers=other)).status == 404


async def test_crawl_limits_by_plan_one_job_on_trial_include_filter_and_cancel(svc):
    h, client, seen = svc
    free, free_auth = account(h, "free", "trial_" + "d" * 32)
    r = await client.post("/v1/crawl", json={"url": "https://docs.example/p0", "limit": 11}, headers=free_auth)
    assert r.status == 400 and "1–10" in (await r.json())["error"]
    first = await (await client.post("/v1/crawl", json={"url": "https://docs.example/p0", "limit": 10}, headers=free_auth)).json()
    r = await client.post("/v1/crawl", json={"url": "https://docs.example/p0", "limit": 2}, headers=free_auth)
    assert r.status == 429 and (await r.json())["code"] == "crawl_in_progress"
    r = await client.delete("/v1/crawl/" + first["id"], headers=free_auth)
    assert (await r.json())["status"] == "cancelled"
    body = await finish(client, free_auth, first["id"])
    assert body["status"] == "cancelled" and body["pages_done"] < 10
    await asyncio.sleep(0.05)
    seen.clear()
    job = await (await client.post("/v1/crawl", json={"url": "https://docs.example/p1", "limit": 5, "include": "/p1"}, headers=free_auth)).json()
    body = await finish(client, free_auth, job["id"])
    assert body["pages_done"] >= 1 and all("/p1" in u for u in seen)
    r = await client.post("/v1/crawl", json={"url": "http://127.0.0.1/", "limit": 2}, headers=free_auth)
    assert r.status == 400


async def test_crawl_stops_cleanly_when_credits_run_out(svc):
    h, client, seen = svc
    acct, auth = account(h, "free", "trial_" + "e" * 32)
    h.billing.charge(acct, "page", 997)
    job = await (await client.post("/v1/crawl", json={"url": "https://docs.example/p10", "limit": 10}, headers=auth)).json()
    body = await finish(client, auth, job["id"])
    assert body["status"] == "stopped" and body["pages_done"] == 3 and "credits" in body["note"] and h.billing.remaining(acct) == 0


async def test_mcp_crawl_tools_and_web_search_visibility(svc):
    h, client, seen = svc
    acct, auth = account(h, "starter", "cs_test_crawl3")
    call = lambda name, args: client.post("/v1/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                                           "params": {"name": name, "arguments": args}}, headers=auth)
    tools = (await (await client.post("/v1/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=auth)).json())["result"]["tools"]
    names = {t["name"] for t in tools}
    assert {"crawl_site", "crawl_status"} <= names and "web_search" not in names        # hidden until a provider key is set
    schemas = {tool["name"]: tool["inputSchema"] for tool in tools}
    assert "actions" in schemas["scrape_page"]["properties"]
    assert "actions" in schemas["scrape_batch"]["properties"]
    started = json.loads((await (await call("crawl_site", {"url": "https://docs.example/p0", "limit": 4})).json())["result"]["content"][0]["text"])
    await finish(client, auth, started["id"])
    status = json.loads((await (await call("crawl_status", {"job_id": started["id"]})).json())["result"]["content"][0]["text"])
    assert status["status"] == "completed" and status["pages_done"] == 4 and len(status["results"]) >= 4


async def test_web_search_charges_for_successful_searches_including_empty_results(svc):
    h, client, seen = svc
    acct, auth = account(h, "starter", "cs_test_search1")
    body = await (await client.post("/v1/websearch", json={"query": "apex scraper", "limit": 3}, headers=auth)).json()
    assert len(body["results"]) == 3 and body["credits_used"] == 130 and body["credits_left"] == 109870
    assert h.billing.db.execute("SELECT kind,credits FROM usage WHERE account=? ORDER BY ts DESC LIMIT 1", (acct,)).fetchone() == ("search", 130)
    body = await (await client.post("/v1/websearch", json={"query": "nothing"}, headers=auth)).json()
    assert body["results"] == [] and body["credits_used"] == 130 and h.billing.remaining(acct) == 109740
    response = await client.post("/v1/websearch", json={"query": "filtered", "include_domains": ["EXAMPLE.com"]}, headers=auth)
    filtered = await response.json()
    assert response.status == 200 and [item["url"] for item in filtered["results"]] == ["https://docs.example.com/guide"]
    invalid = await client.post("/v1/websearch", json={"query": "filtered", "include_domains": ["https://example.com/path"]}, headers=auth)
    assert invalid.status == 400
    assert (await client.post("/v1/websearch", json={"query": ""}, headers=auth)).status == 400


async def test_web_search_and_model_extraction_stay_off_without_operator_keys(tmp_path, monkeypatch):
    for name in ("SCRAPER_GATEWAY_SECRET", "BRAVE_SEARCH_API_KEY", "TAVILY_API_KEY", "EXA_API_KEY", "APEX_SEARCH_PROVIDER",
                 "APEX_SEARCH_BRAVE_CREDITS", "APEX_SEARCH_TAVILY_CREDITS", "APEX_SEARCH_EXA_BASE_CREDITS", "APEX_SEARCH_EXA_EXTRA_RESULT_CREDITS",
                 "DEEPSEEK_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    billing = Billing(tmp_path / "billing.db")
    h = Hosted(billing, "fixture", "fixture-webhook", "fixture-delivery")
    calls = []

    async def scrape(url, **kwargs):
        calls.append(kwargs)
        return {"url": url, "markdown": "# Hi", "text": "Hi", "rendered": False, "extracted": {"name": "Hi"}}

    acct, auth = account(h, "starter", "cs_test_keys1")
    spec = {"schema": {"type": "object", "properties": {"name": {"type": "string"}}}}
    async with TestClient(TestServer(make_app(billing, scrape, hosted=h))) as client:
        r = await client.post("/v1/websearch", json={"query": "x"}, headers=auth)
        assert r.status == 503 and (await r.json())["code"] == "not_configured"
        monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "fixture-brave")
        monkeypatch.setenv("TAVILY_API_KEY", "fixture-tavily")
        tool_list = await (await client.post("/v1/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=auth)).json()
        assert "web_search" not in {tool["name"] for tool in tool_list["result"]["tools"]}
        r = await client.post("/v1/websearch", json={"query": "x"}, headers=auth)
        assert r.status == 503 and (await r.json())["code"] == "not_configured"
        monkeypatch.setenv("APEX_SEARCH_PROVIDER", "tavily")
        tool_list = await (await client.post("/v1/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=auth)).json()
        assert "web_search" in {tool["name"] for tool in tool_list["result"]["tools"]}
        web_search_tool = next(tool for tool in tool_list["result"]["tools"] if tool["name"] == "web_search")
        assert {"include_domains", "exclude_domains"} <= set(web_search_tool["inputSchema"]["properties"])
        r = await client.post("/v1/scrape", json={"url": "https://example.com", "render": "never", "extract": spec}, headers=auth)
        assert r.status == 400 and (await r.json())["code"] == "not_available_hosted" and calls == []
        monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture")
        r = await client.post("/v1/scrape", json={"url": "https://example.com", "render": "never", "extract": spec, "outputs": ["markdown"]}, headers=auth)
        body = await r.json()
        assert r.status == 200 and body["credits_used"] == 151 and body["result"]["extracted"] == {"name": "Hi"} and calls[0]["extract"] == spec
        # Trials never trigger a paid model call, and the service-wide daily ceiling holds.
        _, trial = account(h, "free", "trial_" + "f" * 32)
        r = await client.post("/v1/scrape", json={"url": "https://example.com", "render": "never", "extract": spec}, headers=trial)
        body = await r.json()
        assert r.status == 402 and body["code"] == "paid_plan_required" and body["buy_url"] and len(calls) == 1
        monkeypatch.setenv("APEX_EXTRACT_DAILY_CAP", "1")
        r = await client.post("/v1/scrape", json={"url": "https://example.com", "render": "never", "extract": spec}, headers=auth)
        assert r.status == 429 and (await r.json())["code"] == "extract_daily_limit" and len(calls) == 1 and billing.remaining(acct) == 110000 - 151
    billing.db.close()


def test_search_provider_selection_requires_one_unambiguous_configured_provider(monkeypatch):
    for key in ("BRAVE_SEARCH_API_KEY", "TAVILY_API_KEY", "EXA_API_KEY", "APEX_SEARCH_PROVIDER",
                "APEX_SEARCH_BRAVE_CREDITS", "APEX_SEARCH_TAVILY_CREDITS", "APEX_SEARCH_EXA_BASE_CREDITS", "APEX_SEARCH_EXA_EXTRA_RESULT_CREDITS"):
        monkeypatch.delenv(key, raising=False)
    assert hosted_search.provider_name() is None and not hosted_search.configured()
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "fixture")
    assert hosted_search.provider_name() == "brave" and hosted_search.configured()
    monkeypatch.setenv("EXA_API_KEY", "fixture")
    assert hosted_search.provider_name() is None and not hosted_search.configured()
    monkeypatch.setenv("APEX_SEARCH_PROVIDER", "exa")
    assert hosted_search.provider_name() == "exa" and hosted_search.configured()
    monkeypatch.setenv("APEX_SEARCH_PROVIDER", "unknown")
    assert hosted_search.provider_name() is None and not hosted_search.configured()
    monkeypatch.setenv("APEX_SEARCH_PROVIDER", "brave")
    monkeypatch.setenv("APEX_SEARCH_BRAVE_CREDITS", "0")
    assert hosted_search.provider_name() is None and not hosted_search.configured()


def test_search_credit_rates_exa_reserves_and_charges_requested_result_count(monkeypatch):
    for key in ("APEX_SEARCH_BRAVE_CREDITS", "APEX_SEARCH_TAVILY_CREDITS", "APEX_SEARCH_EXA_BASE_CREDITS", "APEX_SEARCH_EXA_EXTRA_RESULT_CREDITS"):
        monkeypatch.delenv(key, raising=False)
    assert hosted_search.credit_reservation("brave", 20) == 130
    assert hosted_search.credit_reservation("tavily", 20) == 210
    assert hosted_search.credit_reservation("exa", 10) == 180
    assert hosted_search.credit_reservation("exa", 20) == 430
    assert hosted_search.credit_reservation("exa", 12) == 230
    assert hosted_search.credit_reservation("exa", 1) == 180  # the provider bills an empty search request too


async def test_exa_search_reserves_and_charges_requested_result_cap(tmp_path, monkeypatch):
    for key in ("BRAVE_SEARCH_API_KEY", "TAVILY_API_KEY", "EXA_API_KEY", "APEX_SEARCH_PROVIDER",
                "APEX_SEARCH_BRAVE_CREDITS", "APEX_SEARCH_TAVILY_CREDITS", "APEX_SEARCH_EXA_BASE_CREDITS", "APEX_SEARCH_EXA_EXTRA_RESULT_CREDITS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("EXA_API_KEY", "fixture")
    billing = Billing(tmp_path / "search-cost.db")
    h = Hosted(billing, "fixture", "fixture-webhook", "fixture-delivery")

    async def exa_results(query, count, include_domains=None, exclude_domains=None):
        if query == "failure":
            raise hosted_search.aiohttp.ClientError("upstream unavailable")
        return [] if query == "empty" else [{"url": f"https://r.example/{i}", "title": "Result", "snippet": "Text"} for i in range(12)]

    monkeypatch.setattr(hosted_search, "exa", exa_results)
    acct, auth = account(h, "scale", "cs_test_exa_cost")
    async with TestClient(TestServer(make_app(billing, hosted=h))) as client:
        response = await client.post("/v1/websearch", json={"query": "many", "limit": 20}, headers=auth)
        body = await response.json()
        assert response.status == 200, body
        assert len(body["results"]) == 12 and body["credits_used"] == 430
        assert body["credits_left"] == PLANS["scale"]["credits"] - 430
        body = await (await client.post("/v1/websearch", json={"query": "empty", "limit": 20}, headers=auth)).json()
        assert body["results"] == [] and body["credits_used"] == 430
        assert h.billing.remaining(acct) == PLANS["scale"]["credits"] - 860
        response = await client.post("/v1/websearch", json={"query": "failure", "limit": 20}, headers=auth)
        assert response.status != 200 and h.billing.remaining(acct) == PLANS["scale"]["credits"] - 860
    billing.db.close()


@pytest.mark.parametrize("name,key,payload,expected", [
    ("brave", "BRAVE_SEARCH_API_KEY", {"web": {"results": [
        {"url": "https://one.example", "title": "One", "description": "First"},
        {"url": "javascript:alert(1)", "title": "Bad", "description": "Skip"},
    ]}}, [{"url": "https://one.example", "title": "One", "snippet": "First"}]),
    ("tavily", "TAVILY_API_KEY", {"results": [
        {"url": "https://one.example", "title": "One", "content": "First"},
        {"url": "//bad.example", "title": "Bad", "content": "Skip"},
    ]}, [{"url": "https://one.example", "title": "One", "snippet": "First"}]),
    ("exa", "EXA_API_KEY", {"results": [
        {"url": "https://one.example", "title": "One", "highlights": ["First", "detail"]},
        {"url": "https://two.example", "title": "Two", "text": "Second"},
    ]}, [{"url": "https://one.example", "title": "One", "snippet": "First detail"},
         {"url": "https://two.example", "title": "Two", "snippet": "Second"}]),
])
async def test_search_adapters_use_vendor_contract_and_normalize_results(monkeypatch, name, key, payload, expected):
    for provider_key in ("BRAVE_SEARCH_API_KEY", "TAVILY_API_KEY", "EXA_API_KEY"):
        monkeypatch.delenv(provider_key, raising=False)
    monkeypatch.setenv(key, "fixture-secret")
    monkeypatch.setenv("APEX_SEARCH_PROVIDER", name)
    calls = []

    class Response:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def json(self):
            return payload

    class Session:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def get(self, *args, **kwargs):
            calls.append(("get", args, kwargs))
            return Response()

        def post(self, *args, **kwargs):
            calls.append(("post", args, kwargs))
            return Response()

    monkeypatch.setattr(hosted_search.aiohttp, "ClientSession", Session)
    assert await hosted_search.search("query", 3) == expected
    method, args, kwargs = calls[0]
    if name == "brave":
        assert method == "get" and args[0] == "https://api.search.brave.com/res/v1/web/search"
        assert kwargs["headers"]["X-Subscription-Token"] == "fixture-secret"
    elif name == "tavily":
        assert method == "post" and args[0] == "https://api.tavily.com/search"
        assert kwargs["headers"]["Authorization"] == "Bearer fixture-secret"
        assert kwargs["json"]["search_depth"] == "basic" and kwargs["json"]["max_results"] == 3
    else:
        assert method == "post" and args[0] == "https://api.exa.ai/search"
        assert kwargs["headers"]["x-api-key"] == "fixture-secret"
        assert kwargs["json"]["numResults"] == 3


@pytest.mark.parametrize("name,key", [
    ("brave", "BRAVE_SEARCH_API_KEY"),
    ("tavily", "TAVILY_API_KEY"),
    ("exa", "EXA_API_KEY"),
])
async def test_search_adapters_forward_domain_filters_to_provider(monkeypatch, name, key):
    for provider_key in ("BRAVE_SEARCH_API_KEY", "TAVILY_API_KEY", "EXA_API_KEY"):
        monkeypatch.delenv(provider_key, raising=False)
    monkeypatch.setenv(key, "fixture-secret")
    monkeypatch.setenv("APEX_SEARCH_PROVIDER", name)
    calls = []

    class Response:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def json(self):
            return {"web": {"results": [{"url": "https://docs.example.com/a", "title": "A", "description": "S"}]},
                    "results": [{"url": "https://docs.example.com/a", "title": "A", "content": "S",
                                 "highlights": ["S"]}]}

    class Session:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def get(self, *args, **kwargs):
            calls.append(("get", args, kwargs))
            return Response()

        def post(self, *args, **kwargs):
            calls.append(("post", args, kwargs))
            return Response()

    monkeypatch.setattr(hosted_search.aiohttp, "ClientSession", Session)
    result = await hosted_search.search("query", 4, {"include_domains": ["docs.example.com"]})
    assert result == [{"url": "https://docs.example.com/a", "title": "A", "snippet": "S"}]
    method, _, kwargs = calls[0]
    if name == "brave":
        assert method == "get" and "site:docs.example.com" in kwargs["params"]["q"]
    elif name == "tavily":
        assert method == "post" and kwargs["json"]["include_domains"] == ["docs.example.com"]
    else:
        assert method == "post" and kwargs["json"]["includeDomains"] == ["docs.example.com"]


def test_local_search_provider_selection_does_not_depend_on_hosted_prices():
    assert hosted_search.local_provider_name({}) is None
    assert hosted_search.local_provider_name({"BRAVE_SEARCH_API_KEY": "local"}) == "brave"
    assert hosted_search.local_provider_name({"BRAVE_SEARCH_API_KEY": "local", "EXA_API_KEY": "local"}) is None
    assert hosted_search.local_provider_name({"BRAVE_SEARCH_API_KEY": "local", "EXA_API_KEY": "local",
                                               "APEX_SEARCH_PROVIDER": "exa"}) == "exa"
    assert hosted_search.local_provider_name({"BRAVE_SEARCH_API_KEY": "local", "APEX_SEARCH_PROVIDER": "tavily"}) is None


async def test_python_mcp_byok_search_uses_local_provider_without_hosted_dispatch(monkeypatch):
    from rainbo_scrape import hosted_mcp
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "local-test-key")
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    calls = []

    async def local_search(query, limit, include_domains=None, exclude_domains=None):
        calls.append((query, limit, include_domains, exclude_domains))
        return [{"url": "https://example.com/", "title": "Example", "snippet": "A result"}]

    async def forbidden_dispatch(*args):
        raise AssertionError("BYOK search must not call the Apex hosted API")

    monkeypatch.setattr(hosted_search, "local_search", local_search)
    token = hosted_mcp.local_dispatch.set(forbidden_dispatch)
    try:
        result = await hosted_mcp.web_search_byok("example", 4, include_domains=["EXAMPLE.com"])
    finally:
        hosted_mcp.local_dispatch.reset(token)
    assert calls == [("example", 4, ["example.com"], None)] and result["provider"] == "brave"
    assert "never received" not in result["note"]
    assert "Apex did not receive your provider key" in result["note"]
    with pytest.raises(ValueError, match="limit must be"):
        await hosted_mcp.web_search_byok("example", 0)


async def test_python_hosted_mcp_search_forwards_domain_filters(monkeypatch):
    from rainbo_scrape import hosted_mcp
    calls = []

    async def dispatch(path, body):
        calls.append((path, body))
        return {"results": []}

    token = hosted_mcp.local_dispatch.set(dispatch)
    try:
        await hosted_mcp.web_search("official docs", 5, include_domains=["docs.example.com"])
    finally:
        hosted_mcp.local_dispatch.reset(token)
    assert calls == [("/websearch", {
        "query": "official docs", "limit": 5, "include_domains": ["docs.example.com"],
    })]


def test_bigger_plans_get_more_pages_at_once():
    assert [PLANS[p]["concurrency"] for p in ("free", "pass", "starter", "growth", "scale")] == [2, 6, 6, 12, 20]


async def test_crawl_treats_www_and_bare_host_as_one_site(tmp_path, monkeypatch):
    monkeypatch.delenv("SCRAPER_GATEWAY_SECRET", raising=False)
    billing = Billing(tmp_path / "billing.db")
    h = Hosted(billing, "fixture", "fixture-webhook", "fixture-delivery")

    async def scrape(url, **kwargs):      # the bare host redirects to www, and every link uses www
        final = url.replace("https://shop.example", "https://www.shop.example")
        return {"url": final, "markdown": "# " + final, "text": final, "rendered": False,
                "links": [{"url": f"https://www.shop.example/p{i}", "text": "p"} for i in range(6)] + [{"url": "https://other.example/"}]}

    acct, auth = account(h, "starter", "cs_test_www1")
    async with TestClient(TestServer(make_app(billing, scrape, hosted=h))) as client:
        job = await (await client.post("/v1/crawl", json={"url": "https://shop.example/", "limit": 5}, headers=auth)).json()
        body = await finish(client, auth, job["id"])
        assert body["pages_done"] == 5 and all("shop.example" in r["url"] and "other" not in r["url"] for r in body["results"])
    billing.db.close()
