"""Data packages: official public sources for sites that are hard to read page by page."""
import time
import pytest
from aiohttp.test_utils import TestClient, TestServer
from rainbo_scrape.api import make_app
from rainbo_scrape.billing import Billing, _hash
from rainbo_scrape.hosted import Hosted
from rainbo_scrape.hosted_packages import npr_news, zillow_market

ZHVI = (
    "RegionID,SizeRank,RegionName,RegionType,StateName,2025-09-30,2025-10-31,2026-09-30,2026-10-31\n"
    "102001,0,United States,country,,350000.0,351000.0,360000.4,361530.6\n"
    "395045,26,\"Sacramento, CA\",msa,CA,560000.0,562000.0,570000.0,573240.0\n"
    "395056,40,\"Sacramento Valley, CA\",msa,CA,300000.0,,310000.0,\n"
    "394913,12,\"Phoenix, AZ\",msa,AZ,450000.0,452000.0,455000.0,447480.0\n"
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


def test_zillow_market_finds_a_metro_and_reports_latest_value_and_change():
    result = zillow_market(ZHVI, "sacramento, ca", "home_values", months=3)
    assert result["region"] == "Sacramento, CA" and result["state"] == "CA" and result["metric"] == "home_values"
    assert result["latest"] == {"date": "2026-10-31", "value": 573240.0}
    assert result["change_1y_pct"] == 2.0  # 562,000 a year earlier
    assert [p["date"] for p in result["series"]] == ["2025-10-31", "2026-09-30", "2026-10-31"]
    assert "Zillow" in result["source"]["attribution"] and result["source"]["url"].startswith("https://files.zillowstatic.com/")


def test_zillow_market_lists_candidates_when_the_name_is_ambiguous_or_unknown():
    result = zillow_market(ZHVI, "sacramento", "home_values")
    assert "latest" not in result and result["matches"] == ["Sacramento, CA", "Sacramento Valley, CA"]
    assert zillow_market(ZHVI, "atlantis", "home_values")["matches"] == []
    # A metro whose newest month is blank reports the newest month that has a value.
    assert zillow_market(ZHVI, "Sacramento Valley, CA", "home_values")["latest"] == {"date": "2026-09-30", "value": 310000.0}
    with pytest.raises(ValueError):
        zillow_market(ZHVI, "Phoenix, AZ", "mortgages")


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
    loads = []

    async def loader(url):
        loads.append(url)
        if "zori" in url:
            raise OSError("source down")
        return RSS if "npr.org" in url else ZHVI

    async def scrape(url, **kwargs):
        return {"url": url, "markdown": "# x", "text": "x", "rendered": False}

    acct, auth = account(h, "starter", "cs_test_pkg1")
    async with TestClient(TestServer(make_app(billing, scrape, hosted=h, package_loader=loader))) as client:
        listing = await (await client.get("/v1/packages", headers=auth)).json()
        assert {p["id"] for p in listing["packages"]} == {"zillow", "npr"}
        r = await client.post("/v1/packages/zillow", json={"region": "Phoenix, AZ"}, headers=auth)
        body = await r.json()
        assert r.status == 200 and body["credits_used"] == 1 and body["result"]["latest"]["value"] == 447480.0
        assert body["result"]["change_1y_pct"] == -1.0 and billing.remaining(acct) == 109999
        # The same file serves the next question without another download.
        await client.post("/v1/packages/zillow", json={"region": "Sacramento, CA"}, headers=auth)
        assert len([u for u in loads if "zhvi" in u]) == 1
        # No single match: candidates come back free.
        r = await client.post("/v1/packages/zillow", json={"region": "sacramento"}, headers=auth)
        body = await r.json()
        assert r.status == 200 and body["credits_used"] == 0 and len(body["result"]["matches"]) == 2
        # Source failure: a retryable error, nothing charged.
        r = await client.post("/v1/packages/zillow", json={"region": "Phoenix, AZ", "metric": "rents"}, headers=auth)
        body = await r.json()
        assert r.status == 502 and body["retryable"] is True and billing.remaining(acct) == 109998
        r = await client.post("/v1/packages/npr", json={"topic": "news", "limit": 2}, headers=auth)
        body = await r.json()
        assert r.status == 200 and body["credits_used"] == 1 and body["result"]["count"] == 2
        r = await client.post("/v1/packages/npr", json={"topic": "celebrity-gossip"}, headers=auth)
        assert r.status == 400
        r = await client.post("/v1/packages/zillow", json={"region": "Phoenix, AZ"})
        assert r.status == 401
    billing.db.close()
