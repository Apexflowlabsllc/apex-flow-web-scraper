"""Hosted store watch: per-account tracking, one credit per catalog page, free ranking."""
import json
from aiohttp.test_utils import TestClient, TestServer
from rainbo_scrape import hosted_stores
from rainbo_scrape.api import make_app
from rainbo_scrape.billing import Billing
from rainbo_scrape.hosted import Hosted
from rainbo_scrape.storewatch import parse_products
from test_packages import account
from test_storewatch import page, product


async def test_store_endpoints_charge_per_catalog_page_and_keep_accounts_apart(tmp_path, monkeypatch):
    monkeypatch.delenv("SCRAPER_GATEWAY_SECRET", raising=False)
    billing = Billing(tmp_path / "billing.db")
    h = Hosted(billing, "fixture", "fixture-webhook", "fixture-delivery")
    catalog = {"products": [product(1, "Rope"), product(2, "Ball")], "pages": 1, "fail": None}
    asked = []

    async def reader(origin, max_pages):
        asked.append((origin, max_pages))
        if catalog["fail"]:
            raise catalog["fail"]
        return parse_products(page(*catalog["products"])), catalog["pages"]

    async def scrape(url, **kwargs):
        return {"url": url, "markdown": "# x", "text": "x", "rendered": False}

    acct, auth = account(h, "starter", "cs_test_sw1")
    other, other_auth = account(h, "free", "cs_test_sw2")
    start = billing.remaining(acct)
    async with TestClient(TestServer(make_app(billing, scrape, hosted=h, store_reader=reader))) as client:
        def post(path, body, headers=auth):
            return client.post("/v1/stores/" + path, json=body, headers=headers)

        r = await post("track", {"store": "Shop.Example.com/collections/all"})
        body = await r.json()
        assert r.status == 200 and body["credits_used"] == 1 and body["result"]["first_snapshot"] and body["result"]["products"] == 2
        assert body["result"]["store"] == "https://shop.example.com" and asked == [("https://shop.example.com", hosted_stores.MAX_PAGES)]
        assert billing.remaining(acct) == start - 1
        # A second read inside the hour is refused for free.
        r = await post("track", {"store": "shop.example.com"})
        body = await r.json()
        assert r.status == 429 and body["code"] == "too_soon" and body["retry_after"] > 0 and billing.remaining(acct) == start - 1
        # An hour later: three catalog pages cost three credits, and the changes come back.
        h.db.execute("UPDATE sw_snapshots SET ts=ts-7200")
        catalog.update(products=[product(1, "Rope", stamp="later"), product(2, "Ball", available=False), product(3, "Collar")], pages=3)
        r = await post("track", {"store": "shop.example.com"})
        body = await r.json()
        assert r.status == 200 and body["credits_used"] == 3 and body["result"]["changes"] == {"activity": 1, "sold_out": 1, "new": 1}
        assert billing.remaining(acct) == start - 4
        r = await post("rising", {"store": "shop.example.com", "days": 7, "limit": 5})
        body = await r.json()
        assert r.status == 200 and body["credits_used"] == 0
        assert [p["title"] for p in body["result"]["products"]] == ["Ball", "Collar", "Rope"]
        assert "not a sales count" in body["result"]["note"] and billing.remaining(acct) == start - 4
        listing = await (await client.get("/v1/stores", headers=auth)).json()
        assert listing["limit"] == 50
        assert [(s["origin"], s["snapshots"], s["products"]) for s in listing["stores"]] == [("https://shop.example.com", 2, 3)]
        # Another account sees none of it.
        assert (await (await client.get("/v1/stores", headers=other_auth)).json())["stores"] == []
        assert (await (await post("rising", {"store": "shop.example.com"}, other_auth)).json())["result"]["snapshots"] == 0
        # A store that does not answer: retryable, nothing charged.
        catalog["fail"] = OSError("down")
        r = await post("track", {"store": "second.example.com"})
        assert r.status == 502 and (await r.json())["retryable"] is True and billing.remaining(acct) == start - 4
        # Not a Shopify store: a plain 400, nothing charged, nothing tracked.
        catalog["fail"] = ValueError("This store does not publish a catalog file (HTTP 404).")
        r = await post("track", {"store": "second.example.com"})
        assert r.status == 400 and "catalog file" in (await r.json())["error"] and billing.remaining(acct) == start - 4
        assert len((await (await client.get("/v1/stores", headers=auth)).json())["stores"]) == 1
        catalog["fail"] = None
        # The free plan tracks three stores.
        free_start = billing.remaining(other)
        for n in range(3):
            assert (await post("track", {"store": "s%d.example.com" % n}, other_auth)).status == 200
        r = await post("track", {"store": "s3.example.com"}, other_auth)
        assert r.status == 402 and (await r.json())["code"] == "store_limit" and billing.remaining(other) == free_start - 9
        r = await post("forget", {"store": "s0.example.com"}, other_auth)
        assert (await r.json()) == {"store": "https://s0.example.com", "forgotten": True}
        assert (await post("track", {"store": "s3.example.com"}, other_auth)).status == 200
        # Input checks and authentication.
        assert (await post("track", {"store": "shop.example.com", "force": True})).status == 400
        assert (await post("track", {"store": "http://127.0.0.1/"})).status == 400
        assert (await post("rising", {"store": "shop.example.com", "days": 0})).status == 400
        assert (await client.post("/v1/stores/track", json={"store": "shop.example.com"})).status == 401
        # The same tools are reachable over MCP and meter the same account.
        r = await client.post("/v1/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                               "params": {"name": "rising_products", "arguments": {"store": "shop.example.com"}}}, headers=auth)
        text = (await r.json())["result"]["content"][0]["text"]
        assert json.loads(text)["result"]["store"] == "https://shop.example.com"
    billing.db.close()
