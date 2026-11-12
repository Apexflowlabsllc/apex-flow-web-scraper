"""0.4.0: parallel batches, change tracking, actionable errors, usage detail, larger paid search."""
import asyncio
import json
import time
import pytest
from aiohttp.test_utils import TestClient, TestServer
from rainbo_scrape.api import make_app
from rainbo_scrape.billing import Billing, _hash
from rainbo_scrape.hosted import Hosted, SITE


def account(h, plan, session):
    acct = h.billing.create_account(None, plan)
    digest = _hash("nonce-" + session)
    h.db.execute("INSERT INTO orders(session,account,nonce,plan,status,created,period_end) VALUES(?,?,?,?,'active',?,?)",
                 (session, acct, digest, plan, time.time(), time.time() + 86400))
    key = h.key_for(session, digest)
    h.db.execute("INSERT INTO keys(hash,account,label) VALUES(?,?,?)", (_hash(key), acct, "fixture"))
    return acct, {"Authorization": "Bearer " + key}


@pytest.fixture
async def svc(tmp_path, monkeypatch):
    monkeypatch.delenv("SCRAPER_GATEWAY_SECRET", raising=False)
    billing = Billing(tmp_path / "billing.db")
    h = Hosted(billing, "fixture", "fixture-webhook", "fixture-delivery")
    state = {"pages": {}, "running": 0, "peak": 0, "delay": 0.2}

    async def scrape(url, **kwargs):
        state["running"] += 1
        state["peak"] = max(state["peak"], state["running"])
        try:
            await asyncio.sleep(state["delay"])
            if url.endswith("/broken"):
                raise ValueError("fixture failed")
            text = state["pages"].get(url, "Line one\nLine two\nPrice: $10")
            return {"url": url, "title": "Fixture", "markdown": text, "text": text, "rendered": False,
                    "receipt": {"final_url": url}}
        finally:
            state["running"] -= 1

    client = TestClient(TestServer(make_app(billing, scrape, hosted=h)))
    await client.start_server()
    yield h, client, state
    await client.close()
    billing.db.close()


async def test_paid_batch_runs_six_at_once_and_keeps_order(svc):
    h, client, state = svc
    acct, auth = account(h, "starter", "cs_test_paid1")
    urls = [f"https://example.com/{i}" for i in range(10)]
    started = time.monotonic()
    body = await (await client.post("/v1/batch", json={"urls": urls, "render": "never"}, headers=auth)).json()
    elapsed = time.monotonic() - started
    assert [r["url"] for r in body["results"]] == urls and body["processed"] == 10 and body["unprocessed"] == []
    assert state["peak"] == 6 and body["concurrency"] == 6
    assert elapsed < 10 * state["delay"] * 0.6      # sequential would take ten delays
    assert body["credits_left"] == 25000 - 10


async def test_trial_batch_is_limited_to_two_and_failures_are_refunded(svc):
    h, client, state = svc
    acct, auth = account(h, "free", "trial_" + "a" * 32)
    urls = ["https://example.com/a", "https://example.com/broken", "https://example.com/c"]
    body = await (await client.post("/v1/batch", json={"urls": urls, "render": "never"}, headers=auth)).json()
    assert state["peak"] == 2 and body["concurrency"] == 2
    assert [r["status"] for r in body["results"]] == [200, 400, 200]
    assert body["results"][1]["code"] == "invalid_request" and body["results"][1]["retryable"] is False
    assert body["credits_left"] == 998


async def test_repeat_scrapes_report_new_unchanged_and_changed_with_diff(svc):
    h, client, state = svc
    state["delay"] = 0
    acct, auth = account(h, "starter", "cs_test_paid2")
    url = "https://example.com/pricing"
    post = lambda **extra: client.post("/v1/scrape", json={"url": url, "render": "never", **extra}, headers=auth)
    first = await (await post()).json()
    assert first["change"]["status"] == "new"
    second = await (await post()).json()
    assert second["change"]["status"] == "unchanged" and second["change"]["content_sha256"] == first["change"]["content_sha256"]
    state["pages"][url] = "Line one\nLine two\nPrice: $12"
    third = await (await post(outputs=["diff"])).json()
    change = third["change"]
    assert change["status"] == "changed" and change["previous_sha256"] == first["change"]["content_sha256"]
    assert "-Price: $10" in change["diff"] and "+Price: $12" in change["diff"]
    assert "markdown" not in third["result"] and third["result"]["receipt"]        # only the receipt and the change travel
    assert third["credits_used"] == 1
    # Another account never sees this account's history.
    _, other = account(h, "starter", "cs_test_paid3")
    fresh = await (await client.post("/v1/scrape", json={"url": url, "render": "never"}, headers=other)).json()
    assert fresh["change"]["status"] == "new"


async def test_errors_tell_agents_what_to_do_next(svc):
    h, client, state = svc
    state["delay"] = 0
    r = await client.post("/v1/scrape", json={"url": "https://example.com"}, headers={"Authorization": "Bearer rs_live_wrong"})
    body = await r.json()
    assert r.status == 401 and body["code"] == "invalid_key" and body["get_key_url"] == SITE + "/trial.html"
    acct, auth = account(h, "free", "trial_" + "b" * 32)
    h.billing.charge(acct, "page", 1000)
    r = await client.post("/v1/scrape", json={"url": "https://example.com", "render": "never"}, headers=auth)
    body = await r.json()
    assert r.status == 402 and body["code"] == "insufficient_credits" and body["retryable"] is False
    assert body["buy_url"] == SITE + "/#pricing" and body["credits_left"] == 0
    assert r.headers["X-Credits-Remaining"] == "0" and r.headers["X-Request-Id"]
    r = await client.post("/v1/scrape", json={"url": "https://example.com", "nope": 1}, headers=auth)
    assert r.status == 400 and (await r.json())["code"] == "invalid_request"
    # The same guidance reaches MCP clients.
    r = await client.post("/v1/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                           "params": {"name": "scrape_page", "arguments": {"url": "https://example.com", "render": "never"}}}, headers=auth)
    text = (await r.json())["result"]["content"][0]["text"]
    assert "insufficient_credits" in text and "buy_url" in text


async def test_rate_limit_headers_and_usage_detail(svc):
    h, client, state = svc
    state["delay"] = 0
    acct, auth = account(h, "starter", "cs_test_paid4")
    r = await client.post("/v1/scrape", json={"url": "https://example.com/1", "render": "never"}, headers=auth)
    assert r.headers["X-RateLimit-Limit"] == "60" and r.headers["X-RateLimit-Remaining"] == "59"
    assert r.headers["X-Credits-Remaining"] == "24999"
    await client.post("/v1/scrape", json={"url": "https://example.com/broken", "render": "never"}, headers=auth)
    usage = await (await client.get("/v1/usage", headers=auth)).json()
    assert usage["credits_left"] == 24999 and usage["credits_total"] == 25000 and usage["plan"] == "starter"
    assert usage["limits"] == {"requests_per_minute": 60, "concurrent_scrapes": 6, "batch_urls": 10, "saved_pages": 1000, "crawl_pages": 100}
    assert usage["saved_pages"] == 1 and usage["period_end"] > time.time()
    assert usage["daily"] == [{"day": time.strftime("%Y-%m-%d", time.gmtime()), "credits": 1, "requests": 2}]
    assert usage["buy_url"] == SITE + "/#pricing"


async def test_paid_plans_keep_more_searchable_pages_than_the_trial(svc):
    h, client, state = svc
    paid, paid_auth = account(h, "starter", "cs_test_paid5")
    free, _ = account(h, "free", "trial_" + "c" * 32)
    for acct in (paid, free):
        for i in range(105):
            h.save_page(acct, {"url": f"https://example.com/{i}", "title": "Doc", "text": f"alpha page{i}"})
    count = lambda acct: h.db.execute("SELECT COUNT(*) FROM saved_pages WHERE account=?", (acct,)).fetchone()[0]
    assert count(paid) == 105 and count(free) == 100
    assert h.db.execute("SELECT COUNT(*) FROM page_versions WHERE account=?", (free,)).fetchone()[0] == 100
    body = await (await client.post("/v1/search", json={"query": "alpha", "limit": 25}, headers=paid_auth)).json()
    assert len(body["results"]) == 25 and "1,000" in body["scope"] and body["credits_used"] == 0
    r = await client.post("/v1/search", json={"query": "alpha", "limit": 26}, headers=paid_auth)
    assert r.status == 400


async def test_mcp_check_changes_returns_only_what_moved(svc):
    h, client, state = svc
    state["delay"] = 0
    acct, auth = account(h, "starter", "cs_test_paid6")
    urls = ["https://example.com/a", "https://example.com/b"]
    call = lambda: client.post("/v1/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                                "params": {"name": "check_changes", "arguments": {"urls": urls}}}, headers=auth)
    tools = (await (await client.post("/v1/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=auth)).json())["result"]["tools"]
    assert {t["name"] for t in tools} == {"scrape_page", "scrape_batch", "check_changes", "map_site", "search_saved", "credit_balance", "crawl_site", "crawl_status"}
    first = json.loads((await (await call()).json())["result"]["content"][0]["text"])
    assert [p["change"]["status"] for p in first["pages"]] == ["new", "new"] and first["changed"] == []
    state["pages"][urls[1]] = "Line one\nLine two\nPrice: $99"
    second = json.loads((await (await call()).json())["result"]["content"][0]["text"])
    assert second["changed"] == [urls[1]] and "+Price: $99" in second["pages"][1]["change"]["diff"]
    assert "markdown" not in json.dumps(second) and second["credits_left"] == 25000 - 4
    balance = json.loads((await (await client.post("/v1/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "credit_balance", "arguments": {}}}, headers=auth)).json())["result"]["content"][0]["text"])
    assert balance["plan"] == "starter" and balance["limits"]["concurrent_scrapes"] == 6


async def test_long_documents_arrive_in_parts_that_rebuild_the_original(svc):
    h, client, state = svc
    state["delay"] = 0
    acct, auth = account(h, "starter", "cs_test_paid7")
    url = "https://example.com/spec"
    document = "\n".join(f"## Section {i}\n" + "word " * 120 for i in range(1200))     # about 750 KB
    state["pages"][url] = document
    ask = lambda page: client.post("/v1/scrape", json={"url": url, "render": "never", "outputs": ["markdown"], "page": page}, headers=auth)
    first = await (await ask(1)).json()
    info = first["pagination"]
    assert info["page"] == 1 and info["pages"] >= 3 and info["next_page"] == 2
    parts = [first["result"]["markdown"]]
    for page in range(2, info["pages"] + 1):
        r = await ask(page)
        body = await r.json()
        assert r.status == 200 and len(await r.read()) <= 262144 and body["result"]["receipt"]
        parts.append(body["result"]["markdown"])
    assert body["pagination"]["next_page"] is None
    assert "".join(parts) == document                       # nothing lost, nothing repeated
    assert all(p.startswith("## Section") for p in parts)   # cut on line breaks
    assert first["credits_used"] == 1 and h.billing.remaining(acct) == 25000 - info["pages"]
    r = await ask(info["pages"] + 1)
    assert r.status == 400 and "out of range" in (await r.json())["error"]
    # A small page is untouched and rejects a page number it does not have.
    small = await client.post("/v1/scrape", json={"url": "https://example.com/small", "render": "never"}, headers=auth)
    assert "pagination" not in await small.json()
    r = await client.post("/v1/scrape", json={"url": "https://example.com/small", "render": "never", "page": 2}, headers=auth)
    assert r.status == 400


async def test_archive_fallback_is_opt_in_labelled_and_refunds_when_no_copy_exists(tmp_path, monkeypatch):
    from rainbo_scrape.policy import ScrapeError
    monkeypatch.delenv("SCRAPER_GATEWAY_SECRET", raising=False)
    billing = Billing(tmp_path / "billing.db")
    h = Hosted(billing, "fixture", "fixture-webhook", "fixture-delivery")
    fetched = []

    async def scrape(url, **kwargs):
        fetched.append((url, kwargs.get("render")))
        if url.startswith("https://web.archive.org/web/20261110121221id_/"):
            return {"url": url, "title": "Listing", "markdown": "# Archived listing", "text": "Archived listing", "rendered": False,
                    "receipt": {"final_url": url}}
        if "walled" in url or "gone" in url:
            raise ScrapeError("access_challenge", "access challenge detected; extraction paused.")
        if "private" in url:
            raise ScrapeError("blocked_address", "Only public internet addresses are permitted")
        return {"url": url, "markdown": "# Live", "text": "Live", "rendered": False}

    async def snapshot(url):
        return None if "gone" in url else "20261110121221"

    acct, auth = account(h, "starter", "cs_test_arch1")
    async with TestClient(TestServer(make_app(billing, scrape, hosted=h, snapshot_fn=snapshot))) as client:
        post = lambda body: client.post("/v1/scrape", json={"render": "auto", **body}, headers=auth)
        # Without the option the block is reported, free, with a hint an agent can follow.
        r = await post({"url": "https://walled.example/home"})
        body = await r.json()
        assert r.status == 400 and body["code"] == "access_challenge" and "fallback" in body["hint"]
        assert billing.remaining(acct) == 25000 and len(fetched) == 1
        # With the option the archive copy is returned and plainly labelled.
        r = await post({"url": "https://walled.example/home", "fallback": "archive", "outputs": ["markdown"]})
        body = await r.json()
        archive = body["result"]["archive"]
        assert r.status == 200 and body["result"]["markdown"] == "# Archived listing" and body["result"]["url"] == "https://walled.example/home"
        assert archive["captured"] == "2026-11-10T12:12:21Z" and archive["live_page_served"] is False and archive["reason"] == "access_challenge"
        assert archive["snapshot_url"] == "https://web.archive.org/web/20261110121221/https://walled.example/home"
        assert "archived_copy" in body["result"]["warnings"] and fetched[-1][1] == "never" and body["credits_used"] == 1
        # No copy: the original error, no charge, and no hint to try what was already tried.
        r = await post({"url": "https://gone.example/", "fallback": "archive"})
        body = await r.json()
        assert r.status == 400 and body["code"] == "access_challenge" and "hint" not in body and billing.remaining(acct) == 24999
        # A live page is never replaced, and safety refusals never fall back.
        live = await (await post({"url": "https://open.example/", "fallback": "archive"})).json()
        assert "archive" not in live["result"] and live["result"]["markdown"] == "# Live"
        r = await post({"url": "https://private.example/", "fallback": "archive"})
        assert r.status == 400 and (await r.json())["code"] == "blocked_address"
        assert not any("web.archive.org" in u and "private" in u for u, _ in fetched)
        r = await post({"url": "https://open.example/", "fallback": "proxy"})
        assert r.status == 400
        batch = await (await client.post("/v1/batch", json={"urls": ["https://walled.example/a", "https://open.example/b"], "render": "never"}, headers=auth)).json()
        assert batch["results"][0]["code"] == "access_challenge" and "hint" in batch["results"][0] and batch["results"][1]["status"] == 200
    billing.db.close()


async def test_max_tokens_returns_parts_that_fit_a_context_budget(svc):
    h, client, state = svc
    state["delay"] = 0
    acct, auth = account(h, "starter", "cs_test_paid8")
    url = "https://example.com/guide"
    document = "\n".join(f"Paragraph {i} " + "word " * 60 for i in range(400))      # about 31,000 tokens
    state["pages"][url] = document
    ask = lambda **extra: client.post("/v1/scrape", json={"url": url, "render": "never", "outputs": ["markdown"], **extra}, headers=auth)
    whole = await (await ask()).json()
    assert "pagination" not in whole and whole["estimated_tokens"] == -(-len(document) // 4)
    first = await (await ask(max_tokens=4000)).json()
    pages = first["pagination"]["pages"]
    assert pages >= 8 and first["estimated_tokens"] <= 4000 * 1.1
    parts = [first["result"]["markdown"]]
    for page in range(2, pages + 1):
        body = await (await ask(max_tokens=4000, page=page)).json()
        assert body["estimated_tokens"] <= 4000 * 1.1
        parts.append(body["result"]["markdown"])
    assert "".join(parts) == document
    assert (await ask(max_tokens=100)).status == 400
    short = await (await client.post("/v1/scrape", json={"url": "https://example.com/s", "render": "never", "max_tokens": 4000}, headers=auth)).json()
    assert "pagination" not in short
