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
from test_advantages import account

SITE_PAGES = {f"https://docs.example/p{i}": [f"https://docs.example/p{j}" for j in (i + 1, i + 2, i * 2)] for i in range(0, 60)}


@pytest.fixture
async def svc(tmp_path, monkeypatch):
    monkeypatch.delenv("SCRAPER_GATEWAY_SECRET", raising=False)
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
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

    async def provider(query, count):
        return [] if query == "nothing" else [{"url": f"https://r.example/{i}", "title": query, "snippet": "s"} for i in range(count)]

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
    assert body["credits_used"] == 12 and h.billing.remaining(acct) == 25000 - 12       # the failed page was free
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
    started = json.loads((await (await call("crawl_site", {"url": "https://docs.example/p0", "limit": 4})).json())["result"]["content"][0]["text"])
    await finish(client, auth, started["id"])
    status = json.loads((await (await call("crawl_status", {"job_id": started["id"]})).json())["result"]["content"][0]["text"])
    assert status["status"] == "completed" and status["pages_done"] == 4 and len(status["results"]) >= 4


async def test_web_search_charges_two_credits_and_nothing_for_no_results(svc):
    h, client, seen = svc
    acct, auth = account(h, "starter", "cs_test_search1")
    body = await (await client.post("/v1/websearch", json={"query": "apex scraper", "limit": 3}, headers=auth)).json()
    assert len(body["results"]) == 3 and body["credits_used"] == 2 and body["credits_left"] == 24998
    body = await (await client.post("/v1/websearch", json={"query": "nothing"}, headers=auth)).json()
    assert body["results"] == [] and body["credits_used"] == 0 and h.billing.remaining(acct) == 24998
    assert (await client.post("/v1/websearch", json={"query": ""}, headers=auth)).status == 400


async def test_web_search_and_model_extraction_stay_off_without_operator_keys(tmp_path, monkeypatch):
    for name in ("SCRAPER_GATEWAY_SECRET", "BRAVE_SEARCH_API_KEY", "DEEPSEEK_API_KEY", "OPENROUTER_API_KEY"):
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
        r = await client.post("/v1/scrape", json={"url": "https://example.com", "render": "never", "extract": spec}, headers=auth)
        assert r.status == 400 and (await r.json())["code"] == "not_available_hosted" and calls == []
        monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture")
        r = await client.post("/v1/scrape", json={"url": "https://example.com", "render": "never", "extract": spec, "outputs": ["markdown"]}, headers=auth)
        body = await r.json()
        assert r.status == 200 and body["credits_used"] == 26 and body["result"]["extracted"] == {"name": "Hi"} and calls[0]["extract"] == spec
        # Trials never trigger a paid model call, and the service-wide daily ceiling holds.
        _, trial = account(h, "free", "trial_" + "f" * 32)
        r = await client.post("/v1/scrape", json={"url": "https://example.com", "render": "never", "extract": spec}, headers=trial)
        body = await r.json()
        assert r.status == 402 and body["code"] == "paid_plan_required" and body["buy_url"] and len(calls) == 1
        monkeypatch.setenv("APEX_EXTRACT_DAILY_CAP", "1")
        r = await client.post("/v1/scrape", json={"url": "https://example.com", "render": "never", "extract": spec}, headers=auth)
        assert r.status == 429 and (await r.json())["code"] == "extract_daily_limit" and len(calls) == 1 and billing.remaining(acct) == 25000 - 26
    billing.db.close()


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
