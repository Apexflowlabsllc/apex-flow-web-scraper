"""Rent, pay, mortgage-rate and home packages: answers from public data that may be resold."""
import pytest
from aiohttp.test_utils import TestClient, TestServer
from rainbo_scrape.api import make_app
from rainbo_scrape.billing import Billing
from rainbo_scrape.hosted import Hosted
from rainbo_scrape.hosted_datapacks import PMMS, mortgage, pay, rent
from test_packages import HPI, account

PMMS_CSV = ("date,pmms30,pmms30p,pmms15,pmms15p,pmms51,pmms51p,pmms51m,pmms51spread\n"
            "11/20/2025,6.26,,5.54,,,,,\n11/12/2026,7.11,,6.45,,,,,\n11/19/2026,7.28,,6.66,,,,,\n")


def test_rent_by_metro_county_town_and_borough():
    r = rent("Sacramento, CA")
    assert r["region"].startswith("Sacramento") and r["state"] == "CA" and r["year"] == "FY2026"
    assert set(r["monthly_rent"]) == {"studio", "1_bedroom", "2_bedroom", "3_bedroom", "4_bedroom"}
    assert all(isinstance(v, int) and 300 < v < 10000 for v in r["monthly_rent"].values())
    assert "public domain" in r["source"]["attribution"]
    assert rent("Folsom, CA")["region"] == r["region"]            # a secondary city resolves to its metro's rent area
    assert "Kings County" in rent("Brooklyn, NY")["counties"]
    assert rent("Atlantis, QQ")["matches"] == []
    with pytest.raises(ValueError):
        rent("x")


def test_pay_finds_the_job_in_the_metro():
    p = pay("registered nurse", "Seattle, WA", 3)
    assert p["region"].startswith("Seattle") and p["period"] == "May 2024"
    top = p["jobs"][0]
    assert top["title"] == "Registered Nurses" and top["employed"] > 1000
    assert 50000 < top["annual"]["median"] < 300000 and top["annual"]["p10"] < top["annual"]["p90"]
    assert pay("software developer", "Austin, TX")["jobs"][0]["title"] == "Software Developers"
    assert pay("plumber", "Springfield")["matches"]                 # ambiguous metro: candidates, no answer
    with pytest.raises(ValueError):
        pay("", "Seattle, WA")


def test_mortgage_rates_latest_and_a_year_ago():
    m = mortgage(PMMS_CSV)
    assert m["week_of"] == "2026-11-19" and m["rate_30y_fixed"] == 7.28 and m["rate_15y_fixed"] == 6.66
    assert m["year_ago"]["week_of"] == "2025-11-20" and m["year_ago"]["rate_30y_fixed"] == 6.26
    assert "Freddie Mac" in m["source"]["attribution"]


async def test_endpoints_charge_one_credit_for_an_answer_and_none_for_candidates(tmp_path, monkeypatch):
    monkeypatch.delenv("SCRAPER_GATEWAY_SECRET", raising=False)
    billing = Billing(tmp_path / "billing.db")
    h = Hosted(billing, "fixture", "fixture-webhook", "fixture-delivery")

    async def loader(url):
        return PMMS_CSV if url == PMMS else HPI

    async def scrape(url, **kwargs):
        return {"url": url, "markdown": "# x", "text": "x", "rendered": False}

    acct, auth = account(h, "starter", "cs_test_dp1")
    start = billing.remaining(acct)
    async with TestClient(TestServer(make_app(billing, scrape, hosted=h, package_loader=loader))) as client:
        r = await client.post("/v1/packages/home", json={"region": "Sacramento, CA"}, headers=auth)
        body = await r.json()
        assert r.status == 200 and body["credits_used"] == 1
        res = body["result"]
        assert res["house_prices"]["change_1y_pct"] == 2.0 and res["rent"]["monthly_rent"]["2_bedroom"] > 0
        assert res["mortgage_rates"]["rate_30y_fixed"] == 7.28
        r = await client.post("/v1/packages/pay", json={"job": "registered nurse", "region": "Seattle, WA", "limit": 2}, headers=auth)
        assert r.status == 200 and (await r.json())["result"]["count"] == 2
        r = await client.post("/v1/packages/rent", json={"region": "Austin, TX"}, headers=auth)
        assert r.status == 200 and (await r.json())["credits_used"] == 1
        r = await client.post("/v1/packages/mortgage-rates", json={}, headers=auth)
        assert r.status == 200 and (await r.json())["result"]["rate_15y_fixed"] == 6.66
        assert billing.remaining(acct) == start - 4
        r = await client.post("/v1/packages/pay", json={"job": "plumber", "region": "Springfield"}, headers=auth)
        assert (await r.json())["credits_used"] == 0 and billing.remaining(acct) == start - 4
        assert (await client.post("/v1/packages/rent", json={"region": "Austin, TX", "beds": 2}, headers=auth)).status == 400
        assert (await client.post("/v1/packages/rent", json={"region": "Austin, TX"})).status == 401
    billing.db.close()
