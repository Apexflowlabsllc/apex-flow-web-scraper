import pytest
from aiohttp.test_utils import TestClient, TestServer
from rainbo_scrape.api import make_app, credits_for
from rainbo_scrape.billing import Billing

calls = []


async def fake_scrape(url, **options):
    calls.append((url, options))
    return {"markdown": "# Hi", "rendered": options.get("render") == "always"}


@pytest.fixture
async def ctx(tmp_path):
    billing = Billing(tmp_path / "b.db")
    account = billing.create_account("a@example.com")
    key = billing.new_key(account)
    client = TestClient(TestServer(make_app(billing, fake_scrape)))
    await client.start_server()
    calls.clear()
    yield billing, account, key, client
    await client.close()


def auth(key):
    return {"Authorization": "Bearer " + key}


async def test_requires_a_valid_key(ctx):
    _, _, _, client = ctx
    assert (await client.post("/v1/scrape", json={"url": "https://e.com"})).status == 401
    assert (await client.post("/v1/scrape", json={"url": "https://e.com"}, headers=auth("rs_live_bad"))).status == 401
    assert calls == []


async def test_scrape_charges_the_credits_it_used(ctx):
    billing, account, key, client = ctx
    r = await client.post("/v1/scrape", json={"url": "https://e.com", "render": "never"}, headers=auth(key))
    body = await r.json()
    assert r.status == 200 and body["credits_used"] == 1 and body["credits_left"] == 999 and body["result"]["markdown"] == "# Hi"
    r = await client.post("/v1/scrape", json={"url": "https://e.com", "render": "always"}, headers=auth(key))
    assert (await r.json())["credits_left"] == 996  # a rendered page costs 3
    assert (await (await client.get("/v1/usage", headers=auth(key))).json())["credits_left"] == 996


async def test_out_of_credits_is_402_and_nothing_runs(ctx):
    billing, account, key, client = ctx
    billing.charge(account, "page", 999)
    r = await client.post("/v1/scrape", json={"url": "https://e.com"}, headers=auth(key))  # auto may render: worst case 3
    assert r.status == 402 and calls == []


async def test_bad_bodies_are_400_and_unknown_options_rejected(ctx):
    _, _, key, client = ctx
    for body in ({}, {"url": 5}, {"url": "https://e.com", "proxy": "x"}):
        assert (await client.post("/v1/scrape", json=body, headers=auth(key))).status == 400
    assert calls == []


def test_credit_math():
    assert credits_for({"extract": {"x": 1}}, rendered=True) == 5
    assert credits_for({}, rendered=False) == 1


async def test_selected_outputs_preserve_receipt_metering_and_full_index(tmp_path):
    from types import SimpleNamespace
    receipt = {"final_url": "https://example.com/", "content_sha256": "abc"}
    full = {"url": receipt["final_url"], "receipt": receipt, "markdown": "# Hello",
            "text": "x" * 270000, "validation_errors": [], "chunks": [{"text": "# Hello"}]}
    calls = []
    async def scrape(url, **options):
        calls.append(options)
        return full
    saved = []
    hosted = SimpleNamespace(entitled=lambda account: True, install=lambda app: {},
                             site="https://example.com", save_page=lambda account, result, diff=False: saved.append(result))
    billing = Billing(tmp_path / "select.db")
    hosted.billing = billing
    hosted.db = billing.db
    from rainbo_scrape.hosted_features import prepare
    prepare(hosted)
    account = billing.create_account(None)
    headers = auth(billing.new_key(account))
    async with TestClient(TestServer(make_app(billing, scrape, hosted))) as client:
        request = {"url": receipt["final_url"], "render": "never"}
        for invalid in ([], ["private"], [["markdown"]]):
            resp = await client.post("/v1/scrape", json={**request, "outputs": invalid}, headers=headers)
            assert resp.status == 400
        assert calls == []
        resp = await client.post("/v1/scrape", json=request, headers=headers)
        body = await resp.json()   # a document over the response budget now arrives in parts instead of failing
        assert resp.status == 200 and body["pagination"]["pages"] == 2 and billing.remaining(account) == 999
        resp = await client.post("/v1/scrape", json={**request, "outputs": ["markdown"]}, headers=headers)
        body = await resp.json()
        assert resp.status == 200 and body["credits_left"] == 998 and "pagination" not in body
        assert body["result"] == {k: full[k] for k in ("url", "markdown", "receipt", "validation_errors")}
        assert saved == [full, full]
        resp = await client.post("/v1/batch", json={"urls": [request["url"]], "outputs": ["chunks"]}, headers=headers)
        batch = await resp.json()
        assert batch["results"][0]["result"]["chunks"] == full["chunks"]
        assert calls[-1]["chunks"] == 1500
