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
