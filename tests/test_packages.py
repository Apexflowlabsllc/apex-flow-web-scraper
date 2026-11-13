"""Data packages: official public sources whose terms allow automated reading and redistribution."""
import time
import pytest
from aiohttp.test_utils import TestClient, TestServer
from rainbo_scrape.api import make_app
from rainbo_scrape.billing import Billing, _hash
from rainbo_scrape.hosted import Hosted
from rainbo_scrape.hosted_packages import FHFA_METRO, house_prices, npr_news

# FHFA all-transactions metro file: name, CBSA code, year, quarter, index, standard error. No header row.
HPI = (
    '"Sacramento-Roseville-Folsom, CA",40900,2020,4,-,-\n'
    '"Sacramento-Roseville-Folsom, CA",40900,2021,2,250.00,1.10\n'
    '"Sacramento-Roseville-Folsom, CA",40900,2025,1,340.00,1.20\n'
    '"Sacramento-Roseville-Folsom, CA",40900,2025,2,350.00,1.20\n'
    '"Sacramento-Roseville-Folsom, CA",40900,2026,1,360.00,1.30\n'
    '"Sacramento-Roseville-Folsom, CA",40900,2026,2,357.00,1.30\n'
    '"Kansas City, MO-KS",28140,2025,2,300.00,1.00\n'
    '"Kansas City, MO-KS",28140,2026,2,309.00,1.00\n'
    '"Springfield, MA",44140,2026,2,280.00,1.00\n'
    '"Springfield, MO",44180,2026,2,270.00,1.00\n'
    '"Empty Metro, TX",99999,2026,2,-,-\n'
)
RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>NPR Topics: News</title>
<item><title>First &amp; foremost story</title><link>https://www.npr.org/2026/11/13/a</link>
<description>What happened.</description><pubDate>Fri, 13 Nov 2026 12:00:00 -0500</pubDate></item>
<item><title>Second story</title><link>https://www.npr.org/2026/11/13/b</link><description>More.</description>
<pubDate>Fri, 13 Nov 2026 11:00:00 -0500</pubDate></item></channel></rss>"""


def account(h, plan, session):
    acct = h.billing.create_account(None, plan)
    digest = _hash("nonce-" + session)
    h.db.execute("INSERT INTO orders(session,account,nonce,plan,status,created,period_end) VALUES(?,?,?,?,'active',?,?)",
                 (session, acct, digest, plan, time.time(), time.time() + 86400))
    key = h.key_for(session, digest)
    h.db.execute("INSERT INTO keys(hash,account,label) VALUES(?,?,?)", (_hash(key), acct, "fixture"))
    return acct, {"Authorization": "Bearer " + key}


def test_house_prices_finds_a_metro_by_city_and_state_and_reports_changes():
    result = house_prices(HPI, "sacramento, ca", quarters=3)
    assert result["region"] == "Sacramento-Roseville-Folsom, CA" and result["cbsa"] == "40900" and result["metric"] == "house_price_index"
    assert result["latest"] == {"period": "2026-Q2", "index": 357.0}
    assert result["change_1y_pct"] == 2.0 and result["change_5y_pct"] == 42.8   # 350 a year earlier, 250 five years earlier
    assert [p["period"] for p in result["series"]] == ["2025-Q2", "2026-Q1", "2026-Q2"]
    assert "public domain" in result["source"]["attribution"] and result["source"]["url"] == FHFA_METRO
    # A metro that spans states is found by either state.
    assert house_prices(HPI, "Kansas City, KS")["region"] == "Kansas City, MO-KS"
    assert house_prices(HPI, "Kansas City, MO-KS")["change_1y_pct"] == 3.0


def test_house_prices_lists_candidates_when_the_name_is_ambiguous_or_unknown():
    result = house_prices(HPI, "springfield")
    assert "latest" not in result and result["matches"] == ["Springfield, MA", "Springfield, MO"]
    assert house_prices(HPI, "Springfield, MO")["region"] == "Springfield, MO"
    assert house_prices(HPI, "atlantis")["matches"] == []
    assert house_prices(HPI, "Sacramento, NV")["matches"] == []          # the state has to match
    assert house_prices(HPI, "Empty Metro, TX")["matches"] == []         # no values: no answer, no charge
    for bad in ({"region": "x"}, {"region": "Springfield", "quarters": 0}):
        with pytest.raises(ValueError):
            house_prices(HPI, **bad)


def test_npr_news_returns_dated_headlines_with_links():
    result = npr_news(RSS, "news", limit=1)
    assert result["topic"] == "news" and result["count"] == 1
    assert result["stories"][0] == {"title": "First & foremost story", "url": "https://www.npr.org/2026/11/13/a",
                                    "summary": "What happened.", "published": "Fri, 13 Nov 2026 12:00:00 -0500"}
    assert "NPR" in result["source"]["attribution"]


async def test_package_endpoints_charge_one_credit_only_for_an_answer(tmp_path, monkeypatch):
    monkeypatch.delenv("SCRAPER_GATEWAY_SECRET", raising=False)
    billing = Billing(tmp_path / "billing.db")
    h = Hosted(billing, "fixture", "fixture-webhook", "fixture-delivery")
    loads, fail = [], []

    async def loader(url):
        loads.append(url)
        if fail:
            raise OSError("source down")
        return RSS if "npr.org" in url else HPI

    async def scrape(url, **kwargs):
        return {"url": url, "markdown": "# x", "text": "x", "rendered": False}

    acct, auth = account(h, "starter", "cs_test_pkg1")
    start = billing.remaining(acct)
    async with TestClient(TestServer(make_app(billing, scrape, hosted=h, package_loader=loader))) as client:
        listing = await (await client.get("/v1/packages", headers=auth)).json()
        assert {p["id"] for p in listing["packages"]} == {"housing", "npr"}
        r = await client.post("/v1/packages/housing", json={"region": "Kansas City, MO"}, headers=auth)
        body = await r.json()
        assert r.status == 200 and body["credits_used"] == 1 and body["result"]["latest"]["index"] == 309.0
        assert body["result"]["change_1y_pct"] == 3.0 and billing.remaining(acct) == start - 1
        # The same file serves the next question without another download.
        await client.post("/v1/packages/housing", json={"region": "Sacramento, CA"}, headers=auth)
        assert len([u for u in loads if u == FHFA_METRO]) == 1
        # No single match: candidates come back free.
        r = await client.post("/v1/packages/housing", json={"region": "springfield"}, headers=auth)
        body = await r.json()
        assert r.status == 200 and body["credits_used"] == 0 and len(body["result"]["matches"]) == 2
        # The retired vendor-specific endpoint is gone.
        assert (await client.post("/v1/packages/zillow", json={"region": "Phoenix, AZ"}, headers=auth)).status == 404
        # Source failure: a retryable error, nothing charged.
        fail.append(True)
        r = await client.post("/v1/packages/npr", json={"topic": "news"}, headers=auth)
        body = await r.json()
        assert r.status == 502 and body["retryable"] is True and billing.remaining(acct) == start - 2
        fail.clear()
        r = await client.post("/v1/packages/npr", json={"topic": "news", "limit": 2}, headers=auth)
        body = await r.json()
        assert r.status == 200 and body["credits_used"] == 1 and body["result"]["count"] == 2
        assert (await client.post("/v1/packages/npr", json={"topic": "celebrity-gossip"}, headers=auth)).status == 400
        assert (await client.post("/v1/packages/housing", json={"region": "Kansas City, MO", "metric": "rents"}, headers=auth)).status == 400
        assert (await client.post("/v1/packages/housing", json={"region": "Kansas City, MO"})).status == 401
    billing.db.close()
