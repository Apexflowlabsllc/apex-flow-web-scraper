"""Jobs package: a company's open roles from the public job board it publishes (Greenhouse, Lever, Ashby)."""
import json
from aiohttp.test_utils import TestClient, TestServer
from rainbo_scrape.api import make_app
from rainbo_scrape.billing import Billing
from rainbo_scrape.hosted import Hosted
from rainbo_scrape.hosted_jobs import parse, pick
from test_packages import account

GH = json.dumps({"jobs": [
    {"title": "Backend Engineer", "location": {"name": "New York"}, "departments": [{"name": "Payments"}], "absolute_url": "https://stripe.com/jobs/1", "updated_at": "2026-11-20"},
    {"title": "Account Executive", "location": {"name": "London"}, "departments": [], "absolute_url": "https://stripe.com/jobs/2", "updated_at": "2026-11-19"}]})
LEVER = json.dumps([{"text": "Data Engineer", "categories": {"location": "Denver", "team": "Data"}, "hostedUrl": "https://jobs.lever.co/acme/1",
                     "createdAt": 1795000000000, "salaryRange": {"min": 150000, "max": 190000, "currency": "USD", "interval": "per-year-salary"}}])
ASHBY = json.dumps({"jobs": [{"title": "Research Engineer", "location": "San Francisco", "department": "Research", "jobUrl": "https://jobs.ashbyhq.com/x/1",
                              "publishedAt": "2026-11-18", "isRemote": False, "compensation": {"compensationTierSummary": "$300K - $450K"}}]})


def test_each_board_format_parses_to_the_same_shape():
    gh = parse("greenhouse", GH)
    assert gh[0] == {"title": "Backend Engineer", "location": "New York", "team": "Payments", "url": "https://stripe.com/jobs/1", "updated": "2026-11-20"}
    assert parse("lever", LEVER)[0]["pay"] == {"min": 150000, "max": 190000, "currency": "USD", "interval": "per-year-salary"}
    assert parse("ashby", ASHBY)[0]["pay"] == "$300K - $450K"
    chosen, total = pick(gh, "engineer", "new york", 5)
    assert total == 1 and chosen[0]["title"] == "Backend Engineer"


async def test_endpoint_finds_the_board_and_charges_only_for_jobs(tmp_path, monkeypatch):
    monkeypatch.delenv("SCRAPER_GATEWAY_SECRET", raising=False)
    billing = Billing(tmp_path / "billing.db")
    h = Hosted(billing, "fixture", "fixture-webhook", "fixture-delivery")
    calls = []

    async def loader(url):
        calls.append(url)
        if "greenhouse" in url and "/stripe/" in url:
            return GH
        if "lever.co" in url and "/acme" in url:
            return LEVER
        raise OSError("404")

    async def scrape(url, **kwargs):
        return {"url": url, "markdown": "# x", "text": "x", "rendered": False}

    acct, auth = account(h, "starter", "cs_test_jobs1")
    start = billing.remaining(acct)
    async with TestClient(TestServer(make_app(billing, scrape, hosted=h, package_loader=loader))) as client:
        body = await (await client.post("/v1/packages/jobs", json={"company": "Stripe", "query": "engineer"}, headers=auth)).json()
        assert body["credits_used"] == 1 and body["result"]["board"] == "greenhouse" and body["result"]["count"] == 1
        body = await (await client.post("/v1/packages/jobs", json={"company": "acme"}, headers=auth)).json()
        assert body["result"]["board"] == "lever" and body["result"]["jobs"][0]["pay"]["max"] == 190000
        body = await (await client.post("/v1/packages/jobs", json={"company": "nobody-here"}, headers=auth)).json()
        assert body["credits_used"] == 0 and body["result"]["count"] == 0
        body = await (await client.post("/v1/packages/jobs", json={"company": "stripe", "query": "astronaut"}, headers=auth)).json()
        assert body["credits_used"] == 0 and body["result"]["matching"] == 0
        assert billing.remaining(acct) == start - 2
        assert (await client.post("/v1/packages/jobs", json={"company": "stripe", "salary": 1}, headers=auth)).status == 400
    billing.db.close()
