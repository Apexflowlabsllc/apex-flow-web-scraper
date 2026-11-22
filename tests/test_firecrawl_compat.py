import asyncio
import hashlib
import hmac
import io
import json
import time
from pathlib import Path
import pytest
from aiohttp import FormData
from aiohttp.test_utils import TestClient, TestServer
from pypdf import PdfWriter

from rainbo_scrape.api import make_app
from rainbo_scrape.billing import _hash
from rainbo_scrape.hosted_firecrawl_compat import _claim_firecrawl_batch
from test_hosted import hosted


async def _auth(hosted):
    h, client, session, _, nonce, _ = hosted
    await h.fulfill(session["id"])
    key = h.key_for(session["id"], _hash(nonce))
    return h, client, {"Authorization": f"Bearer {key}"}


async def test_v1_api_key_management_is_account_scoped_and_returns_secret_once(hosted):
    h, client, headers = await _auth(hosted)
    account = h.billing.account_for(headers["Authorization"][7:])

    initial = await client.get("/v1/keys", headers=headers)
    initial_payload = await initial.json()
    assert initial.status == 200
    assert initial_payload["keys"]
    assert all("key" not in row and "hash" not in row for row in initial_payload["keys"])
    original_id = next(row["id"] for row in initial_payload["keys"] if not row["revoked"])

    created = await client.post("/v1/keys", headers=headers, json={"label": "Claude Code"})
    created_payload = await created.json()
    assert created.status == 201
    assert created_payload["key"].startswith("rs_live_")
    assert created_payload["label"] == "Claude Code"
    new_headers = {"Authorization": f"Bearer {created_payload['key']}"}
    assert (await client.get("/v1/usage", headers=new_headers)).status == 200

    other_account = h.billing.create_account("other-keys@example.com")
    other_key = h.billing.new_key(other_account, "other")
    other_id = h.db.execute("SELECT key_id FROM keys WHERE hash=?", (_hash(other_key),)).fetchone()[0]
    cross_account = await client.delete(f"/v1/keys/{other_id}", headers=headers)
    assert cross_account.status == 404
    assert h.billing.account_for(other_key) == other_account

    revoked = await client.delete(f"/v1/keys/{created_payload['id']}", headers=headers)
    assert revoked.status == 200
    assert (await client.get("/v1/usage", headers=new_headers)).status == 401
    assert (await client.delete(f"/v1/keys/{created_payload['id']}", headers=headers)).status == 404
    last_key = await client.delete(f"/v1/keys/{original_id}", headers=headers)
    assert last_key.status == 409
    assert h.billing.account_for(headers["Authorization"][7:]) == account


async def test_owner_funnel_report_is_gateway_protected_and_only_returns_aggregates(hosted, monkeypatch):
    h, client, *_ = hosted
    hidden = await client.get("/api/internal/funnel")
    assert hidden.status == 404

    monkeypatch.setenv("SCRAPER_GATEWAY_SECRET", "fixture-gateway-secret")
    wrong = await client.get("/api/internal/funnel", headers={"X-Scraper-Gateway": "wrong"})
    assert wrong.status == 403

    from rainbo_scrape.hosted_features import event
    event(h, "page_view")
    event(h, "page_view")
    event(h, "trial_view")
    event(h, "trial_submit")
    event(h, "checkout_created", "starter")
    event(h, "activated", "starter")
    first_scrape = await client.post("/api/event", json={"event": "first_scrape_success"},
                                     headers={"Origin": h.site, "X-Scraper-Gateway": "fixture-gateway-secret"})
    assert first_scrape.status == 200
    headers = {"X-Scraper-Gateway": "fixture-gateway-secret"}
    report = await client.get("/api/internal/funnel?days=30", headers=headers)
    payload = await report.json()
    assert report.status == 200
    assert payload["totals"]["page_view"] == 2
    assert payload["totals"]["trial_view"] == payload["totals"]["trial_submit"] == 1
    assert payload["event_ratios"]["trial_submit_events_per_trial_view_event"] == 1.0
    assert payload["event_ratios"]["paid_activation_events_per_checkout_created_event"] == 1.0
    assert payload["totals"]["first_scrape_success"] == 1
    assert payload["counts_are_events_not_unique_visitors"] is True
    assert payload["ratios_are_same_window_event_counts_not_cohort_conversions"] is True
    assert "email" not in json.dumps(payload).lower()
    assert (await client.get("/api/internal/funnel?days=0", headers=headers)).status == 400


async def test_v2_scrape_uses_firecrawl_envelope_and_apex_metering(hosted):
    h, client, headers = await _auth(hosted)
    response = await client.post("/v2/scrape", headers=headers, json={
        "url": "https://example.com/compat", "formats": ["markdown", "links"],
        "actions": [{"type": "wait", "milliseconds": 10}],
    })
    payload = await response.json()
    assert response.status == 200
    assert payload["success"] is True
    assert payload["data"]["markdown"] == "# Fixture"
    assert payload["data"]["metadata"]["apexCreditsUsed"] == payload["creditsUsed"] == 1
    assert h.billing.remaining(h.billing.account_for(headers["Authorization"][7:])) == 109999
    response = await client.post("/v2/scrape", headers=headers, json={
        "url": "https://example.com/formats", "formats": ["html", "rawHtml", "screenshot"],
    })
    formatted = await response.json()
    assert response.status == 200
    assert formatted["data"]["html"] == "<main><h1>Fixture</h1></main>"
    assert "<nav>source shell" in formatted["data"]["rawHtml"]
    assert formatted["data"]["screenshot"].startswith("data:image/png;base64,")
    assert "markdown" not in formatted["data"]
    unsupported = await client.post("/v2/batch/scrape", headers=headers,
                                    json={"urls": ["https://example.com"], "maxConcurrency": 1})
    assert unsupported.status == 400
    assert (await unsupported.json())["code"] == "unsupported_option"


async def test_v2_images_format_works_for_scrape_batch_and_crawl(hosted):
    _, client, headers = await _auth(hosted)
    expected = ["https://cdn.example.com/hero.webp", "https://cdn.example.com/detail.png"]

    response = await client.post("/v2/scrape", headers=headers, json={
        "url": "https://example.com/images", "formats": ["images"],
    })
    payload = await response.json()
    assert response.status == 200 and payload["data"]["images"] == expected

    response = await client.post("/v2/batch/scrape", headers=headers, json={
        "urls": ["https://example.com/images-batch"], "formats": ["images"],
    })
    job = (await response.json())["id"]
    for _ in range(30):
        status = await client.get(f"/v2/batch/scrape/{job}", headers=headers)
        status_payload = await status.json()
        if status_payload["status"] != "scraping":
            break
        await asyncio.sleep(0.01)
    assert status_payload["status"] == "completed"
    assert status_payload["data"][0]["images"] == expected

    response = await client.post("/v2/crawl", headers=headers, json={
        "url": "https://example.com/images-crawl", "limit": 1, "maxDiscoveryDepth": 0,
        "scrapeOptions": {"formats": ["images"]},
    })
    job = (await response.json())["id"]
    for _ in range(30):
        status = await client.get(f"/v2/crawl/{job}", headers=headers)
        status_payload = await status.json()
        if status_payload["status"] != "scraping":
            break
        await asyncio.sleep(0.01)
    assert status_payload["status"] == "completed"
    assert status_payload["data"][0]["images"] == expected


async def test_v2_attributes_format_works_for_scrape_batch_and_crawl(hosted):
    _, client, headers = await _auth(hosted)
    selectors = [{"selector": "meta[name=description]", "attribute": "content"}]
    expected = [{**selectors[0], "values": ["fixture description"]}]
    formats = [{"type": "attributes", "selectors": selectors}]

    response = await client.post("/v2/scrape", headers=headers, json={
        "url": "https://example.com/attributes", "formats": formats,
    })
    payload = await response.json()
    assert response.status == 200 and payload["data"]["attributes"] == expected

    response = await client.post("/v2/scrape", headers=headers, json={
        "url": "https://example.com/attributes-invalid",
        "formats": [{"type": "attributes", "selectors": [{"selector": "a[", "attribute": "href"}]}],
    })
    assert response.status == 400

    response = await client.post("/v2/batch/scrape", headers=headers, json={
        "urls": ["https://example.com/attributes-batch"], "formats": formats,
    })
    job = (await response.json())["id"]
    for _ in range(30):
        status = await client.get(f"/v2/batch/scrape/{job}", headers=headers)
        status_payload = await status.json()
        if status_payload["status"] != "scraping":
            break
        await asyncio.sleep(0.01)
    assert status_payload["status"] == "completed"
    assert status_payload["data"][0]["attributes"] == expected

    response = await client.post("/v2/crawl", headers=headers, json={
        "url": "https://example.com/attributes-crawl", "limit": 1, "maxDiscoveryDepth": 0,
        "scrapeOptions": {"formats": formats},
    })
    job = (await response.json())["id"]
    for _ in range(30):
        status = await client.get(f"/v2/crawl/{job}", headers=headers)
        status_payload = await status.json()
        if status_payload["status"] != "scraping":
            break
        await asyncio.sleep(0.01)
    assert status_payload["status"] == "completed"
    assert status_payload["data"][0]["attributes"] == expected

async def test_v2_scrape_min_age_and_lockdown_select_cache_only(hosted):
    h, client, headers = await _auth(hosted)
    response = await client.post("/v2/scrape", headers=headers, json={
        "url": "https://example.com/cached", "formats": ["markdown"], "minAge": 1, "maxAge": 5000,
    })
    assert response.status == 200
    assert h.scrape_calls[-1][1]["cache_only"] is True
    assert h.scrape_calls[-1][1]["min_age_ms"] == 1
    assert h.scrape_calls[-1][1]["cache_max_age_ms"] == 5000
    assert h.scrape_calls[-1][1]["render"] == "never"

    response = await client.post("/v2/scrape", headers=headers, json={
        "url": "https://example.com/locked", "formats": ["html"], "lockdown": True,
    })
    assert response.status == 200
    assert h.scrape_calls[-1][1]["cache_only"] is True
    assert h.scrape_calls[-1][1]["cache_max_age_ms"] == 63_072_000_000


async def test_v2_scrape_rejects_unsupported_cache_only_options(hosted):
    _, client, headers = await _auth(hosted)
    for body in (
        {"url": "https://example.com", "minAge": True},
        {"url": "https://example.com", "lockdown": "true"},
        {"url": "https://example.com", "minAge": 1, "formats": ["screenshot"]},
        {"url": "https://example.com", "lockdown": True, "waitFor": 10},
    ):
        response = await client.post("/v2/scrape", headers=headers, json=body)
        assert response.status == 400


async def test_v2_scrape_translates_cache_miss_to_firecrawl_404(hosted):
    from rainbo_scrape.policy import ScrapeError

    h, _, _, _, nonce, _ = hosted
    await h.fulfill("cs_test_Example123")
    key = h.key_for("cs_test_Example123", _hash(nonce))

    async def cache_miss(url, **kwargs):
        assert kwargs["cache_only"] is True
        raise ScrapeError("cache_miss", "No eligible cached response is available; no network request was made")

    client = TestClient(TestServer(make_app(h.billing, cache_miss, hosted=h)))
    await client.start_server()
    try:
        response = await client.post("/v2/scrape", headers={"Authorization": f"Bearer {key}"}, json={
            "url": "https://example.com/missing", "minAge": 1,
        })
        payload = await response.json()
        assert response.status == 404
        assert payload["code"] == "SCRAPE_NO_CACHED_DATA"
        assert "no network request was made" in payload["error"]

        response = await client.post("/v2/scrape", headers={"Authorization": f"Bearer {key}"}, json={
            "url": "https://example.com/missing", "lockdown": True,
        })
        payload = await response.json()
        assert response.status == 404
        assert payload["code"] == "SCRAPE_LOCKDOWN_CACHE_MISS"
    finally:
        await client.close()


async def test_v2_requires_auth_and_rejects_unsupported_formats(hosted):
    _, client, *_ = hosted
    assert (await client.post("/v2/scrape", json={"url": "https://example.com"})).status == 401
    _, client, headers = await _auth(hosted)
    response = await client.post("/v2/scrape", headers=headers,
                                json={"url": "https://example.com", "formats": ["json"]})
    assert response.status == 400
    payload = await response.json()
    assert payload["success"] is False and payload["code"] == "unsupported_option"


async def test_v2_parse_upload_formats_and_credit_metering(hosted):
    h, client, headers = await _auth(hosted)
    account = h.billing.account_for(headers["Authorization"][7:])
    form = FormData()
    document = b"A plain document for parsing." + b" " * 100_000
    form.add_field("file", document, filename="notes.txt", content_type="text/plain")
    form.add_field("options", json.dumps({"formats": ["markdown", "text", "pages"]}),
                   content_type="application/json")
    response = await client.post("/v2/parse", headers=headers, data=form)
    payload = await response.json()
    assert response.status == 200
    assert payload["success"] is True
    assert payload["data"]["markdown"] == document.decode()
    assert payload["data"]["text"] == document.decode()
    assert payload["data"]["metadata"]["filename"] == "notes.txt"
    assert payload["creditsUsed"] == 1
    assert h.billing.remaining(account) == 109999

    pdf_buffer = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.write(pdf_buffer)
    pdf_form = FormData()
    pdf_form.add_field("file", pdf_buffer.getvalue(), filename="one-page.pdf", content_type="application/pdf")
    pdf_form.add_field("options", json.dumps({"formats": ["pages", "metadata"]}),
                       content_type="application/json")
    pdf_response = await client.post("/v2/parse", headers=headers, data=pdf_form)
    pdf_payload = await pdf_response.json()
    assert pdf_response.status == 200
    assert pdf_payload["data"]["metadata"]["numPages"] == 1
    assert pdf_payload["data"]["pages"] == [{"pageNumber": 1, "markdown": ""}]
    assert h.billing.remaining(account) == 109998

    formats = await client.get("/v2/parse/formats", headers=headers)
    available = await formats.json()
    assert formats.status == 200
    assert formats.headers["Cache-Control"] == "private, max-age=3600"
    assert {item["format"] for item in available["data"]["formats"]} >= {"pdf", "docx", "html", "text"}


async def test_v2_parse_rejects_unsupported_options_without_charging(hosted):
    h, client, headers = await _auth(hosted)
    account = h.billing.account_for(headers["Authorization"][7:])
    form = FormData()
    form.add_field("file", b"text", filename="notes.txt", content_type="text/plain")
    form.add_field("options", json.dumps({"skipTlsVerification": True}), content_type="application/json")
    response = await client.post("/v2/parse", headers=headers, data=form)
    payload = await response.json()
    assert response.status == 400
    assert payload["code"] == "unsupported_option"
    assert h.billing.remaining(account) == 110000


async def test_v2_parse_refunds_credit_when_document_parser_fails(hosted, monkeypatch):
    from rainbo_scrape.policy import ScrapeError
    import rainbo_scrape.parser as parser

    h, client, headers = await _auth(hosted)
    account = h.billing.account_for(headers["Authorization"][7:])

    async def fail(*args, **kwargs):
        raise ScrapeError("pdf_invalid", "Unreadable PDF")

    monkeypatch.setattr(parser, "parse", fail)
    form = FormData()
    form.add_field("file", b"%PDF-invalid", filename="bad.pdf", content_type="application/pdf")
    response = await client.post("/v2/parse", headers=headers, data=form)
    payload = await response.json()
    assert response.status == 400
    assert payload["code"] == "pdf_invalid"
    assert h.billing.remaining(account) == 110000


async def test_v2_active_crawls_lists_only_current_accounts_running_jobs(hosted):
    h, client, headers = await _auth(hosted)
    account = h.billing.account_for(headers["Authorization"][7:])
    other = h.billing.create_account("other-crawl@example.com")
    created = time.time()
    h.db.execute("INSERT INTO crawl_jobs(id,account,url,status,page_limit,created,options,done,failed,credits) "
                 "VALUES(?,?,?,'running',?,?,?,?,?,?)",
                 ("crawl_owned", account, "https://example.com/", 25, created,
                  json.dumps({"outputs": ["markdown", "raw_html"]}), 3, 1, 4))
    h.db.execute("INSERT INTO crawl_jobs(id,account,url,status,page_limit,created,options) "
                 "VALUES(?,?,?,'running',?,?,?)",
                 ("crawl_other", other, "https://private.example/", 10, created, "{}"))
    response = await client.get("/v2/crawl/active", headers=headers)
    payload = await response.json()
    assert response.status == 200
    assert payload["success"] is True
    assert [crawl["id"] for crawl in payload["crawls"]] == ["crawl_owned"]
    assert payload["crawls"][0]["completed"] == 3
    assert payload["crawls"][0]["failed"] == 1
    assert payload["crawls"][0]["options"]["scrapeOptions"]["formats"] == [
        {"type": "markdown"}, {"type": "rawHtml"}]


async def test_v2_credit_usage_and_historical_usage_are_account_scoped(hosted):
    h, client, headers = await _auth(hosted)
    account = h.billing.account_for(headers["Authorization"][7:])
    h.billing.charge(account, "page", 7)
    current = await client.get("/v2/team/credit-usage", headers=headers)
    payload = await current.json()
    assert current.status == 200
    assert payload["success"] is True
    assert payload["data"]["remainingCredits"] == 109993
    assert payload["data"]["planCredits"] == 110000
    assert payload["data"]["billingPeriodStart"].endswith("Z")
    assert payload["data"]["billingPeriodEnd"] is not None

    h.db.execute("INSERT INTO usage(ts,account,kind,credits) VALUES(?,?,?,?)",
                 (time.time(), account, "fixture", 12))
    history = await client.get("/v2/team/credit-usage/historical", headers=headers)
    history_payload = await history.json()
    assert history.status == 200
    assert history_payload["success"] is True
    assert history_payload["periods"][-1]["totalCredits"] >= 12

    first_key = headers["Authorization"][7:]
    second_key = h.billing.new_key(account, "secondary")
    for key, suffix in ((first_key, "first-key"), (second_key, "second-key")):
        result = await client.post("/v2/scrape", headers={"Authorization": f"Bearer {key}"},
                                   json={"url": f"https://example.com/{suffix}"})
        assert result.status == 200

    by_key = await client.get("/v2/team/credit-usage/historical?byApiKey=true", headers=headers)
    by_key_payload = await by_key.json()
    assert by_key.status == 200
    attributed = [period for period in by_key_payload["periods"] if period["apiKeyId"]]
    assert len(attributed) == 2
    assert "secondary" in {period["apiKeyLabel"] for period in attributed}
    assert {period["totalCredits"] for period in attributed} == {1}
    assert any(period["apiKeyId"] is None and period["totalCredits"] >= 19
               for period in by_key_payload["periods"])


async def test_v2_json_format_maps_schema_prompt_and_paid_metering(hosted, monkeypatch):
    import rainbo_scrape.llm as llm

    _, client, headers = await _auth(hosted)
    monkeypatch.setattr(llm, "providers", lambda: [("fixture", "https://fixture.invalid", "unused", "fixture")])
    response = await client.post("/v2/scrape", headers=headers, json={
        "url": "https://example.com/json",
        "formats": [{"type": "json", "schema": {
            "type": "object", "properties": {"heading": {"type": "string"}},
            "required": ["heading"], "additionalProperties": False,
        }, "prompt": "Extract the page heading."}],
    })
    payload = await response.json()
    assert response.status == 200
    assert payload["data"]["json"] == {"heading": "Fixture"}
    assert payload["creditsUsed"] == 151

    response = await client.post("/v2/scrape", headers=headers, json={
        "url": "https://example.com/json", "formats": [{"type": "json"}],
    })
    assert response.status == 400


async def test_v2_json_format_requires_a_configured_model_provider(hosted, monkeypatch):
    import rainbo_scrape.llm as llm

    _, client, headers = await _auth(hosted)
    monkeypatch.setattr(llm, "providers", lambda: [])
    response = await client.post("/v2/scrape", headers=headers, json={
        "url": "https://example.com/json", "formats": [{"type": "json", "prompt": "Extract the title."}],
    })
    assert response.status == 400
    assert (await response.json())["code"] == "not_available_hosted"


async def test_v2_map_search_and_batch_expose_exact_supported_subset(hosted, monkeypatch):
    import rainbo_scrape.hosted_features as hosted_features

    _, client, headers = await _auth(hosted)
    response = await client.post("/v2/map", headers=headers,
                                json={"url": "https://example.com", "sitemap": "skip"})
    assert response.status == 400 and (await response.json())["success"] is False

    async def fixture_map(fetcher, url, **kwargs):
        return {"urls": ["https://example.com/a", {"url": "https://example.com/b", "title": "B"}]}

    monkeypatch.setattr(hosted_features, "site_map", fixture_map)
    response = await client.post("/v2/map", headers=headers,
                                 json={"url": "https://example.com", "limit": 10})
    payload = await response.json()
    assert response.status == 200 and payload["success"] is True
    assert payload["links"] == [{"url": "https://example.com/a"}, {"url": "https://example.com/b", "title": "B"}]

    response = await client.post("/v2/search", headers=headers, json={"query": "example"})
    assert response.status == 503
    assert (await response.json())["code"] == "not_configured"

    response = await client.post("/v2/batch/scrape", headers=headers,
                                json={"urls": ["https://example.com"]})
    assert response.status == 200
    job = (await response.json())["id"]
    for _ in range(20):
        status = await client.get(f"/v2/batch/scrape/{job}", headers=headers)
        status_payload = await status.json()
        if status_payload["status"] != "scraping":
            break
        await asyncio.sleep(0.01)
    assert status_payload["status"] == "completed"
    assert status_payload["success"] is True
    assert status_payload["completed"] == status_payload["total"] == 1
    assert status_payload["data"][0]["markdown"] == "# Fixture"


async def test_v2_search_domain_filters_match_firecrawl_request_and_response(hosted, monkeypatch):
    import rainbo_scrape.hosted_search as hosted_search

    _, client, headers = await _auth(hosted)
    monkeypatch.setenv("TAVILY_API_KEY", "fixture-key")
    monkeypatch.setenv("APEX_SEARCH_PROVIDER", "tavily")
    calls = []

    async def fixture_tavily(query, count, include_domains=None, exclude_domains=None, topic="general"):
        calls.append((query, count, include_domains, exclude_domains, topic))
        return [
            {"url": "https://docs.example.com/guide", "title": "Guide", "snippet": "Useful"},
            {"url": "https://other.example.net/page", "title": "Other", "snippet": "Filtered"},
        ]

    monkeypatch.setattr(hosted_search, "tavily", fixture_tavily)
    response = await client.post("/v2/search", headers=headers, json={
        "query": "scraping API", "limit": 5, "sources": ["web"],
        "includeDomains": ["EXAMPLE.com"],
    })
    payload = await response.json()
    assert response.status == 200 and payload["success"] is True
    assert calls == [("scraping API", 5, ["example.com"], None, "general")]
    assert payload["data"]["web"] == [{
        "url": "https://docs.example.com/guide", "title": "Guide", "description": "Useful", "position": 1,
    }]

    object_source = await client.post("/v2/search", headers=headers, json={
        "query": "scraping API", "sources": [{"type": "web"}],
    })
    assert object_source.status == 200

    invalid = await client.post("/v2/search", headers=headers, json={
        "query": "scraping API", "includeDomains": ["https://example.com/path"],
    })
    invalid_payload = await invalid.json()
    assert invalid.status == 400 and invalid_payload["code"] == "invalid_search_filter"

    conflict = await client.post("/v2/search", headers=headers, json={
        "query": "scraping API", "includeDomains": ["example.com"], "excludeDomains": ["blocked.example"],
    })
    conflict_payload = await conflict.json()
    assert conflict.status == 400 and conflict_payload["code"] == "invalid_search_filter"

    news = await client.post("/v2/search", headers=headers, json={
        "query": "scraping API", "sources": ["news"],
    })
    news_payload = await news.json()
    assert news.status == 200 and news_payload["data"]["news"][0] == {
        "url": "https://docs.example.com/guide", "title": "Guide", "snippet": "Useful", "date": None, "position": 1,
    }
    assert calls[-1] == ("scraping API", 10, None, None, "news")


async def test_v2_search_maps_brave_images_source(hosted, monkeypatch):
    import rainbo_scrape.hosted_search as hosted_search

    _, client, headers = await _auth(hosted)
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "fixture-image-key")
    monkeypatch.setenv("APEX_SEARCH_PROVIDER", "brave")
    calls = []

    async def fixture_images(query, count, include_domains=None, exclude_domains=None):
        calls.append((query, count, include_domains, exclude_domains))
        return [{"title": "Apex logo", "imageUrl": "https://cdn.example.com/logo.png",
                 "imageWidth": 640, "imageHeight": 320, "url": "https://example.com/about"}]

    monkeypatch.setattr(hosted_search, "brave_images", fixture_images)
    response = await client.post("/v2/search", headers=headers, json={
        "query": "Apex logo", "limit": 3, "sources": [{"type": "images"}],
        "includeDomains": ["example.com"],
    })
    payload = await response.json()
    assert response.status == 200 and payload["success"] is True
    assert calls == [("Apex logo", 3, ["example.com"], None)]
    assert payload["data"]["images"] == [{
        "title": "Apex logo", "imageUrl": "https://cdn.example.com/logo.png",
        "imageWidth": 640, "imageHeight": 320, "url": "https://example.com/about", "position": 1,
    }]


async def test_v2_search_supports_multiple_sources_and_aggregates_credits(hosted, monkeypatch):
    import rainbo_scrape.hosted_search as hosted_search

    _, client, headers = await _auth(hosted)
    monkeypatch.setenv("TAVILY_API_KEY", "fixture-search-key")
    monkeypatch.setenv("APEX_SEARCH_PROVIDER", "tavily")
    calls = []

    async def fixture_search(query, count, include_domains=None, exclude_domains=None, topic="general"):
        calls.append(topic)
        if topic == "news":
            return [{"url": "https://example.com/news", "title": "News", "snippet": "Current", "date": "today"}]
        return [{"url": "https://example.com/page", "title": "Page", "snippet": "Web result"}]

    monkeypatch.setattr(hosted_search, "tavily", fixture_search)
    response = await client.post("/v2/search", headers=headers, json={
        "query": "scraping API", "limit": 5, "sources": ["web", {"type": "news"}],
    })
    payload = await response.json()
    assert response.status == 200 and payload["success"] is True
    assert calls == ["general", "news"]
    assert payload["data"]["web"][0]["description"] == "Web result"
    assert payload["data"]["news"][0]["snippet"] == "Current"
    assert payload["creditsUsed"] == 420

    unsupported_mix = await client.post("/v2/search", headers=headers, json={
        "query": "scraping API", "sources": ["web", "images"],
    })
    assert unsupported_mix.status == 400
    assert calls == ["general", "news"]

    async def partial_search(query, count, include_domains=None, exclude_domains=None, topic="general"):
        if topic == "news":
            raise RuntimeError("fixture news outage")
        return [{"url": "https://example.com/page", "title": "Page", "snippet": "Web result"}]

    monkeypatch.setattr(hosted_search, "tavily", partial_search)
    partial = await client.post("/v2/search", headers=headers, json={
        "query": "scraping API", "sources": ["web", "news"],
    })
    partial_payload = await partial.json()
    assert partial.status == 200 and partial_payload["success"] is True
    assert partial_payload["data"]["web"][0]["title"] == "Page"
    assert partial_payload["sourceErrors"]["news"]["statusCode"] == 503

    duplicate = await client.post("/v2/search", headers=headers, json={
        "query": "scraping API", "sources": ["web", "web"],
    })
    assert duplicate.status == 400


async def test_v2_search_scrape_options_scrapes_results_and_counts_page_credits(hosted, monkeypatch):
    import rainbo_scrape.hosted_search as hosted_search

    h, client, headers = await _auth(hosted)
    monkeypatch.setenv("TAVILY_API_KEY", "fixture-search-key")
    monkeypatch.setenv("APEX_SEARCH_PROVIDER", "tavily")

    async def fixture_search(query, count, include_domains=None, exclude_domains=None, topic="general"):
        return [{"url": "https://example.com/compat", "title": "Search title", "snippet": "Search snippet"}]

    monkeypatch.setattr(hosted_search, "tavily", fixture_search)
    response = await client.post("/v2/search", headers=headers, json={
        "query": "fixture", "sources": ["web"],
        "scrapeOptions": {"formats": ["markdown"], "waitFor": 25},
    })
    payload = await response.json()
    assert response.status == 200 and payload["success"] is True
    result = payload["data"]["web"][0]
    assert result["url"] == "https://example.com/compat"
    assert result["markdown"] == "# Fixture"
    assert result["metadata"]["apexCreditsUsed"] == 1
    assert payload["creditsUsed"] == 211  # Search reserve (210) plus one page scrape credit.
    assert h.scrape_calls[-1][1]["actions"] == [{"type": "wait", "ms": 25}]

    rejected = await client.post("/v2/search", headers=headers, json={
        "query": "fixture", "sources": ["images"], "scrapeOptions": {"formats": ["markdown"]},
    })
    assert rejected.status == 400


async def test_v2_search_pdf_category_uses_operator_and_filters_non_pdf_results(hosted, monkeypatch):
    import rainbo_scrape.hosted_search as hosted_search

    _, client, headers = await _auth(hosted)
    monkeypatch.setenv("TAVILY_API_KEY", "fixture-search-key")
    monkeypatch.setenv("APEX_SEARCH_PROVIDER", "tavily")
    observed = []

    async def fixture_search(query, count, include_domains=None, exclude_domains=None, topic="general"):
        observed.append(query)
        return [
            {"url": "https://docs.example.com/guide.PDF?download=1", "title": "Guide", "snippet": "PDF"},
            {"url": "https://docs.example.com/page", "title": "Page", "snippet": "HTML"},
        ]

    monkeypatch.setattr(hosted_search, "tavily", fixture_search)
    response = await client.post("/v2/search", headers=headers, json={
        "query": "scraping guide", "sources": ["web"], "categories": ["pdf"], "limit": 1,
    })
    payload = await response.json()
    assert response.status == 200 and payload["success"] is True
    assert observed == ["scraping guide filetype:pdf"]
    assert payload["data"]["web"] == [{
        "url": "https://docs.example.com/guide.PDF?download=1", "title": "Guide",
        "description": "PDF", "position": 1, "category": "pdf",
    }]

    unsupported = await client.post("/v2/search", headers=headers, json={
        "query": "research", "sources": ["web"], "categories": ["developer"],
    })
    assert unsupported.status == 400


async def test_v2_search_research_category_uses_academic_domains_and_combines_pdf(hosted, monkeypatch):
    import rainbo_scrape.hosted_search as hosted_search

    _, client, headers = await _auth(hosted)
    monkeypatch.setenv("TAVILY_API_KEY", "fixture-search-key")
    monkeypatch.setenv("APEX_SEARCH_PROVIDER", "tavily")
    calls = []

    async def fixture_search(query, count, include_domains=None, exclude_domains=None, topic="general"):
        calls.append((query, include_domains, exclude_domains))
        return [
            {"url": "https://arxiv.org/pdf/2401.01234.pdf", "title": "Paper PDF", "snippet": "Abstract"},
            {"url": "https://arxiv.org/abs/2401.01234", "title": "Paper HTML", "snippet": "Abstract"},
            {"url": "https://nature.com/articles/abc.pdf", "title": "Nature PDF", "snippet": "Research"},
            {"url": "https://example.com/guide.pdf", "title": "Unrelated PDF", "snippet": "Guide"},
        ]

    monkeypatch.setattr(hosted_search, "tavily", fixture_search)
    response = await client.post("/v2/search", headers=headers, json={
        "query": "retrieval augmented generation", "sources": ["web"],
        "includeDomains": ["arxiv.org"], "categories": [{"type": "research"}, {"type": "pdf"}],
    })
    payload = await response.json()
    assert response.status == 200 and payload["success"] is True
    assert calls == [("retrieval augmented generation filetype:pdf", ["arxiv.org"], None)]
    assert payload["data"]["web"] == [{
        "url": "https://arxiv.org/pdf/2401.01234.pdf", "title": "Paper PDF",
        "description": "Abstract", "position": 1, "category": "pdf",
    }]

    research = await client.post("/v2/search", headers=headers, json={
        "query": "retrieval augmented generation", "categories": ["research"],
    })
    research_payload = await research.json()
    assert research.status == 200 and research_payload["success"] is True
    assert [item["url"] for item in research_payload["data"]["web"]] == [
        "https://arxiv.org/pdf/2401.01234.pdf", "https://arxiv.org/abs/2401.01234",
        "https://nature.com/articles/abc.pdf",
    ]
    assert all(item["category"] == "research" for item in research_payload["data"]["web"])

    excluded = await client.post("/v2/search", headers=headers, json={
        "query": "retrieval augmented generation", "categories": ["research"],
        "excludeDomains": ["arxiv.org"],
    })
    excluded_payload = await excluded.json()
    assert excluded.status == 200
    assert [item["url"] for item in excluded_payload["data"]["web"]] == ["https://nature.com/articles/abc.pdf"]


async def test_firecrawl_research_paper_search_and_read_use_cached_crossref_metadata(hosted, monkeypatch):
    import rainbo_scrape.hosted_research as hosted_research

    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    h, client, headers = await _auth(hosted)
    calls = []

    async def fixture_crossref(hosted_service, params, doi=None):
        calls.append((dict(params), doi))
        if doi:
            return {"DOI": doi, "title": ["Fixture DOI paper"],
                    "author": [{"given": "Alex", "family": "Researcher"}],
                    "created": {"date-parts": [[2024, 2, 3]]},
                    "subject": ["Computer Science"]}
        return {"items": [
            {"DOI": "10.5555/old", "title": ["Old paper"], "author": [{"given": "Jane", "family": "Doe"}],
             "created": {"date-parts": [[2020, 1, 1]]}, "subject": ["Artificial Intelligence"], "score": 100},
            {"DOI": "10.5555/current", "title": ["Current paper"], "author": [{"given": "Jane", "family": "Doe"}],
             "created": {"date-parts": [[2024, 3, 4]]}, "subject": ["Artificial Intelligence"], "score": 95},
            {"DOI": "10.5555/other", "title": ["Other paper"], "author": [{"given": "John", "family": "Smith"}],
             "created": {"date-parts": [[2024, 5, 6]]}, "subject": ["Biology"], "score": 90},
        ]}

    monkeypatch.setattr(hosted_research, "_fetch_crossref", fixture_crossref)
    url = "/v2/search/research/papers?query=machine%20learning&k=5&authors=Jane&categories=Artificial%20Intelligence&from=2023-01-01&sort=date_desc"
    response = await client.get(url, headers=headers)
    payload = await response.json()
    assert response.status == 200 and payload["success"] is True
    assert payload["creditsUsed"] == 130
    assert len(payload["results"]) == 1
    paper = payload["results"][0]
    assert paper["paperId"] == "10.5555/current"
    assert paper["primaryId"] == "doi:10.5555/current"
    assert paper["authors"] == ["Jane Doe"]
    assert paper["categories"] == ["Artificial Intelligence"]
    assert "abstract" not in paper
    params = calls[0][0]
    assert params["query.bibliographic"] == "machine learning"
    assert params["sort"] == "created" and params["order"] == "desc"
    assert params["filter"] == "from-created-date:2023-01-01"

    cached = await client.get(url, headers=headers)
    cached_payload = await cached.json()
    assert cached.status == 200 and cached_payload["results"] == payload["results"]
    assert len(calls) == 1

    lookup = await client.get("/v2/search/research/papers/10.5555%2Flookup", headers=headers)
    lookup_payload = await lookup.json()
    assert lookup.status == 200 and lookup_payload["success"] is True
    assert lookup_payload["paper"]["paperId"] == "10.5555/lookup"
    assert calls[-1][1] == "10.5555/lookup"

    invalid = await client.get("/v2/search/research/papers/not-a-doi", headers=headers)
    assert invalid.status == 400
    account = h.billing.account_for(headers["Authorization"][7:])
    usage = h.billing.db.execute("SELECT kind,credits FROM usage WHERE account=? AND kind='search'", (account,)).fetchall()
    assert sum(row[1] for row in usage) == 390


async def test_firecrawl_research_provider_failures_refund_reserved_credits(hosted, monkeypatch):
    import rainbo_scrape.hosted_research as hosted_research

    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    h, client, headers = await _auth(hosted)
    account = h.billing.account_for(headers["Authorization"][7:])
    before = h.billing.remaining(account)

    async def failed_crossref(*args, **kwargs):
        raise hosted_research.ResearchProviderError(429)

    monkeypatch.setattr(hosted_research, "_fetch_crossref", failed_crossref)
    response = await client.get("/v2/search/research/papers?query=rate%20limit", headers=headers)
    payload = await response.json()
    assert response.status == 503
    assert payload["code"] == "research_provider_unavailable"
    assert response.headers["Retry-After"] == "2"
    assert h.billing.remaining(account) == before


async def test_openalex_research_rate_limit_refunds_reserved_credits(hosted, monkeypatch):
    import rainbo_scrape.hosted_research as hosted_research

    monkeypatch.setenv("OPENALEX_API_KEY", "fixture-key")
    h, client, headers = await _auth(hosted)
    account = h.billing.account_for(headers["Authorization"][7:])
    before = h.billing.remaining(account)

    async def limited_openalex(*args, **kwargs):
        raise hosted_research.ResearchProviderError(429)

    async def limited_crossref(*args, **kwargs):
        raise hosted_research.ResearchProviderError(429)

    monkeypatch.setattr(hosted_research, "_fetch_openalex", limited_openalex)
    monkeypatch.setattr(hosted_research, "_fetch_crossref", limited_crossref)
    response = await client.get("/v2/search/research/papers?query=rate%20limit", headers=headers)
    payload = await response.json()
    assert response.status == 503
    assert payload["code"] == "research_provider_unavailable"
    assert response.headers["Retry-After"] == "2"
    assert h.billing.remaining(account) == before


async def test_openalex_research_falls_back_to_crossref(hosted, monkeypatch):
    import rainbo_scrape.hosted_research as hosted_research

    monkeypatch.setenv("OPENALEX_API_KEY", "fixture-key")
    h, client, headers = await _auth(hosted)

    async def unavailable_openalex(*args, **kwargs):
        raise hosted_research.ResearchProviderError(429)

    async def fallback_crossref(hosted_service, params, doi=None):
        assert params["query.bibliographic"] == "web crawling"
        return {"items": [{
            "DOI": "10.5555/fallback", "title": ["Fallback result"],
            "author": [{"given": "Alex", "family": "Author"}],
            "created": {"date-parts": [[2025, 1, 1]]}, "subject": ["Web Science"],
        }]}

    monkeypatch.setattr(hosted_research, "_fetch_openalex", unavailable_openalex)
    monkeypatch.setattr(hosted_research, "_fetch_crossref", fallback_crossref)
    response = await client.get("/v2/search/research/papers?query=web%20crawling", headers=headers)
    payload = await response.json()
    assert response.status == 200
    assert payload["results"][0]["provider"] == "Crossref"
    assert payload["results"][0]["paperId"] == "10.5555/fallback"


async def test_openalex_related_and_citing_paper_routes_are_cached_and_metered(hosted, monkeypatch):
    import rainbo_scrape.hosted_research as hosted_research

    monkeypatch.setenv("OPENALEX_API_KEY", "fixture-key")
    h, client, headers = await _auth(hosted)
    calls = []

    def work(work_id, doi, title):
        return {
            "id": f"https://openalex.org/{work_id}", "doi": f"https://doi.org/{doi}",
            "title": title, "authorships": [{"author": {"display_name": "Research Author"}}],
            "publication_date": "2025-02-03", "topics": [{"display_name": "Web Science"}],
            "cited_by_count": 3, "referenced_works": [], "related_works": [],
            "is_retracted": False,
        }

    base = work("W100", "10.5555/target", "Target paper")
    base["related_works"] = ["https://openalex.org/W200", "https://openalex.org/W300"]

    async def fixture_openalex(hosted_service, params):
        calls.append(dict(params))
        if params.get("filter") == "doi:https://doi.org/10.5555/target":
            return {"results": [base], "meta": {}}
        if params.get("filter") == "openalex:W200|W300":
            return {"results": [work("W300", "10.5555/third", "Third paper"),
                                work("W200", "10.5555/second", "Second paper")], "meta": {}}
        if params.get("filter") == "cites:W100":
            return {"results": [work("W400", "10.5555/citing", "Citing paper")], "meta": {}}
        raise AssertionError(params)

    monkeypatch.setattr(hosted_research, "_fetch_openalex", fixture_openalex)
    related_url = "/v2/search/research/papers/10.5555%2Ftarget/related?k=2"
    related = await client.get(related_url, headers=headers)
    related_payload = await related.json()
    assert related.status == 200 and related_payload["creditsUsed"] == 130
    assert [paper["paperId"] for paper in related_payload["results"]] == ["10.5555/second", "10.5555/third"]
    cached = await client.get(related_url, headers=headers)
    assert (await cached.json())["results"] == related_payload["results"]
    assert len(calls) == 2

    citations = await client.get("/v2/search/research/papers/W100/citations?k=5", headers=headers)
    citations_payload = await citations.json()
    assert citations.status == 200 and citations_payload["relation"] == "citations"
    assert citations_payload["results"][0]["paperId"] == "10.5555/citing"
    assert calls[-1]["filter"] == "cites:W100"
    account = h.billing.account_for(headers["Authorization"][7:])
    rows = h.billing.db.execute("SELECT credits FROM usage WHERE account=? AND kind='search'", (account,)).fetchall()
    assert sum(row[0] for row in rows) == 390


async def test_openalex_paper_relationships_require_provider_configuration(hosted, monkeypatch):
    import rainbo_scrape.hosted_research as hosted_research

    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    h, client, headers = await _auth(hosted)
    account = h.billing.account_for(headers["Authorization"][7:])
    before = h.billing.remaining(account)
    response = await client.get("/v2/search/research/papers/W100/citations", headers=headers)
    assert response.status == 503
    assert h.billing.remaining(account) == before


async def test_openalex_research_search_and_read_include_citation_graph_metadata(hosted, monkeypatch):
    import rainbo_scrape.hosted_research as hosted_research

    monkeypatch.setenv("OPENALEX_API_KEY", "fixture-key")
    h, client, headers = await _auth(hosted)
    calls = []
    work = {
        "id": "https://openalex.org/W1234567890",
        "doi": "https://doi.org/10.5555/openalex",
        "title": "Open research paper",
        "authorships": [{"author": {"display_name": "Alex Researcher"}}],
        "created_date": "2024-01-02T00:00:00Z",
        "updated_date": "2024-04-05T00:00:00Z",
        "publication_date": "2023-12-01",
        "topics": [{"display_name": "Web Science"}],
        "cited_by_count": 42,
        "referenced_works": ["https://openalex.org/W1", "https://openalex.org/W2"],
        "related_works": ["https://openalex.org/W3"],
        "relevance_score": 0.91,
        "is_retracted": False,
    }

    async def fixture_openalex(hosted_service, params):
        calls.append(dict(params))
        return {"results": [work], "meta": {"next_cursor": None}}

    monkeypatch.setattr(hosted_research, "_fetch_openalex", fixture_openalex)
    url = "/v2/search/research/papers?query=web%20science&k=5&authors=Alex&categories=Web%20Science&from=2023-01-01&sort=date_desc"
    response = await client.get(url, headers=headers)
    payload = await response.json()
    assert response.status == 200 and payload["success"] is True
    assert payload["creditsUsed"] == 130 and len(payload["results"]) == 1
    paper = payload["results"][0]
    assert paper["provider"] == "OpenAlex"
    assert paper["paperId"] == "10.5555/openalex"
    assert paper["openAlexId"] == "https://openalex.org/W1234567890"
    assert paper["citedByCount"] == 42 and paper["referenceCount"] == 2
    assert paper["referenceIds"] == ["https://openalex.org/W1", "https://openalex.org/W2"]
    assert paper["relatedWorks"] == ["https://openalex.org/W3"]
    assert paper["isRetracted"] is False and "abstract" not in paper
    assert calls[0]["search"] == "web science"
    assert calls[0]["per_page"] == 100
    assert calls[0]["filter"] == "from_publication_date:2023-01-01"
    assert calls[0]["sort"] == "publication_date:desc"

    cached = await client.get(url, headers=headers)
    assert (await cached.json())["results"] == payload["results"]
    assert len(calls) == 1
    lookup = await client.get("/v2/search/research/papers/10.5555%2Fopenalex", headers=headers)
    lookup_payload = await lookup.json()
    assert lookup.status == 200 and lookup_payload["paper"]["provider"] == "OpenAlex"
    assert calls[-1]["filter"] == "doi:https://doi.org/10.5555/openalex"


async def test_current_langchain_mcp_client_discovers_and_calls_hosted_scraper(hosted):
    import os

    client_python = os.environ.get("APEX_LANGCHAIN_CLIENT_PYTHON")
    if not client_python:
        pytest.skip("set APEX_LANGCHAIN_CLIENT_PYTHON to test the current LangChain MCP client")

    h, client, headers = await _auth(hosted)
    child_env = {name: os.environ[name] for name in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP")
                 if name in os.environ}
    child_env["APEX_SCRAPER_API_KEY"] = headers["Authorization"][7:]
    child_env["APEX_LANGCHAIN_MCP_URL"] = str(client.make_url("/v1/mcp"))
    child_env["APEX_LANGCHAIN_TEST_URL"] = "https://example.com/langchain"
    script = """
import asyncio, os
from fastmcp import Client
from langchain.mcp import MCPAdapter

async def main():
    async with MCPAdapter(Client(os.environ['APEX_LANGCHAIN_MCP_URL'], auth=os.environ['APEX_SCRAPER_API_KEY'])) as adapter:
        tools = await adapter.list_tools()
        scrape = next(tool for tool in tools if tool.name == 'scrape_page')
        result = await scrape.ainvoke({'url': os.environ['APEX_LANGCHAIN_TEST_URL']})
        assert '# Fixture' in str(result), str(result)

asyncio.run(main())
"""
    process = await asyncio.create_subprocess_exec(
        client_python, "-c", script, env=child_env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=45)
    assert process.returncode == 0, (stdout + stderr).decode("utf-8", errors="replace")
    assert h.scrape_calls[-1][0] == "https://example.com/langchain"


async def test_current_llamaindex_mcp_client_discovers_and_calls_hosted_scraper(hosted):
    import os

    client_python = os.environ.get("APEX_LLAMA_CLIENT_PYTHON")
    if not client_python:
        pytest.skip("set APEX_LLAMA_CLIENT_PYTHON to test the current LlamaIndex MCP client")

    h, client, headers = await _auth(hosted)
    child_env = {name: os.environ[name] for name in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP")
                 if name in os.environ}
    child_env["APEX_SCRAPER_API_KEY"] = headers["Authorization"][7:]
    child_env["APEX_LLAMA_MCP_URL"] = str(client.make_url("/v1/mcp"))
    child_env["APEX_LLAMA_TEST_URL"] = "https://example.com/llamaindex"
    script = """
import asyncio, os
from llama_index.tools.mcp import BasicMCPClient, McpToolSpec

async def main():
    client = BasicMCPClient(
        os.environ['APEX_LLAMA_MCP_URL'],
        headers={'Authorization': f\"Bearer {os.environ['APEX_SCRAPER_API_KEY']}\"},
    )
    spec = McpToolSpec(client=client, allowed_tools=['scrape_page'])
    tools = await spec.to_tool_list_async()
    assert len(tools) == 1 and tools[0].metadata.name == 'scrape_page'
    result = await tools[0].acall(url=os.environ['APEX_LLAMA_TEST_URL'])
    assert '# Fixture' in str(result), str(result)

asyncio.run(main())
"""
    process = await asyncio.create_subprocess_exec(
        client_python, "-c", script, env=child_env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=60)
    assert process.returncode == 0, (stdout + stderr).decode("utf-8", errors="replace")
    assert h.scrape_calls[-1][0] == "https://example.com/llamaindex"


async def test_official_firecrawl_python_sdk_v2_client_against_apex_routes(hosted, monkeypatch):
    import rainbo_scrape.hosted_search as hosted_search
    Firecrawl = pytest.importorskip("firecrawl").Firecrawl

    h, client, headers = await _auth(hosted)
    api_key = headers["Authorization"][7:]
    app = Firecrawl(api_key=api_key, api_url=str(client.make_url("/")).rstrip("/"), max_retries=1)
    monkeypatch.setenv("TAVILY_API_KEY", "fixture-search-key")
    monkeypatch.setenv("APEX_SEARCH_PROVIDER", "tavily")

    async def fixture_search(query, count, include_domains=None, exclude_domains=None, topic="general"):
        assert (query, count, include_domains, exclude_domains) == ("fixture search", 3, ["example.com"], None)
        assert topic in ("general", "news")
        return [{"url": "https://docs.example.com/start", "title": "Start", "snippet": "Search result",
                 **({"date": "2026-11-22T12:00:00Z"} if topic == "news" else {})}]

    monkeypatch.setattr(hosted_search, "tavily", fixture_search)
    searched = await asyncio.to_thread(app.v2.search, "fixture search", sources=["web"], limit=3,
                                       include_domains=["example.com"])
    assert searched.web[0].url == "https://docs.example.com/start"
    assert searched.web[0].position == 1
    news = await asyncio.to_thread(app.v2.search, "fixture search", sources=[{"type": "news"}], limit=3,
                                   include_domains=["example.com"])
    assert news.news[0].url == "https://docs.example.com/start"
    assert news.news[0].date == "2026-11-22T12:00:00Z"

    scraped = await asyncio.to_thread(app.v2.scrape, "https://example.com", formats=["markdown"], wait_for=1200)
    assert scraped.markdown == "# Fixture"
    assert h.scrape_calls[-1][1]["actions"] == [{"type": "wait", "ms": 1200}]
    assert "ad blocking is not implemented by Apex" in scraped.warning
    assert "origin attribution is not recorded by Apex" in scraped.warning

    locked = await asyncio.to_thread(app.v2.scrape, "https://example.com/locked", formats=["markdown"], lockdown=True)
    assert locked.markdown == "# Fixture"
    assert h.scrape_calls[-1][1]["cache_only"] is True
    assert h.scrape_calls[-1][1]["cache_max_age_ms"] > 0

    started = await asyncio.to_thread(app.v2.start_batch_scrape, ["https://example.com"], wait_for=800)
    status = None
    for _ in range(30):
        status = await asyncio.to_thread(app.v2.get_batch_scrape_status, started.id)
        if status.status != "scraping":
            break
        await asyncio.sleep(0.01)
    assert status is not None and status.status == "completed"
    assert status.completed == status.total == 1
    assert status.data[0].markdown == "# Fixture"
    assert h.scrape_calls[-1][1]["actions"] == [{"type": "wait", "ms": 800}]
    assert "origin attribution is not recorded by Apex" in status.data[0].warning

    unsupported = await client.post("/v2/scrape", headers=headers,
                                    json={"url": "https://example.com", "mobile": True})
    assert unsupported.status == 400
    assert (await unsupported.json())["code"] == "unsupported_option"

    invalid_wait = await client.post("/v2/scrape", headers=headers,
                                     json={"url": "https://example.com", "waitFor": 5001})
    assert invalid_wait.status == 400
    assert "waitFor must be an integer from 0 to 5000" in (await invalid_wait.json())["error"]


def test_firecrawl_batch_claim_is_atomic_and_stale_owners_are_fenced():
    import secrets
    import sqlite3
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path
    from threading import Barrier

    work_dir = Path(__file__).resolve().parents[1] / "work"
    work_dir.mkdir(exist_ok=True)
    db_path = work_dir / f"firecrawl-batch-claim-{secrets.token_hex(8)}.sqlite3"
    db = sqlite3.connect(db_path, isolation_level=None, timeout=10)
    try:
        db.execute("CREATE TABLE firecrawl_batch_jobs(id TEXT PRIMARY KEY,status TEXT,lease_until REAL,lease_token TEXT)")
        db.execute("INSERT INTO firecrawl_batch_jobs VALUES('batch','scraping',NULL,NULL)")
        now = time.time()
        barrier = Barrier(2)

        def compete():
            connection = sqlite3.connect(db_path, isolation_level=None, timeout=10)
            try:
                barrier.wait()
                return _claim_firecrawl_batch(connection, "batch", now)
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(pool.map(lambda _: compete(), range(2)))
        winners = [token for token in claims if token]
        assert len(winners) == 1
        first = winners[0]
        second = _claim_firecrawl_batch(db, "batch", now + 31)
        assert second and second != first
        assert db.execute(
            "UPDATE firecrawl_batch_jobs SET status='completed' WHERE id='batch' AND lease_token=?",
            (first,),
        ).rowcount == 0
        assert db.execute("SELECT status,lease_token FROM firecrawl_batch_jobs WHERE id='batch'").fetchone() == (
            "scraping", second
        )
    finally:
        db.close()
        db_path.unlink(missing_ok=True)


async def test_firecrawl_batch_resumes_saved_pages_after_service_restart(hosted):
    h, old_client, _, _, _, _ = hosted
    _, old_client, headers = await _auth(hosted)
    account = h.billing.account_for(headers["Authorization"][7:])
    job = "batch_restart_fixture"
    urls = ["https://example.com/saved", "https://example.com/pending"]
    now = time.time()
    await old_client.close()
    pending_key = f"firecrawl-batch:{job}:1"
    h.billing.reserve_idempotent(account, pending_key, 3)
    h.billing.settle_idempotent(account, pending_key, 1)
    h.billing.charge(account, "page", 1)
    h.db.execute(
        "INSERT INTO firecrawl_batch_jobs(id,account,status,urls,options,total,completed,credits,created,finished,lease_until,lease_token) "
        "VALUES(?,?,'scraping',?,?,2,1,1,?,NULL,?,?)",
        (job, account, json.dumps(urls), json.dumps({"outputs": ["markdown"], "_compat_formats": ["markdown"]}),
         now - 20, now - 1, "stale-worker"),
    )
    h.db.execute("INSERT INTO firecrawl_batch_pages(job,seq,url,status,payload,credits) VALUES(?,?,?,200,?,1)",
                 (job, 0, urls[0], json.dumps({"metadata": {"url": urls[0], "sourceURL": urls[0]}, "markdown": "# Saved"})))
    calls = []

    async def scrape(url, **options):
        calls.append(url)
        return {"markdown": "# Recovered", "rendered": False, "url": url,
                "receipt": {"final_url": url, "status": 200}, "kind": "text"}

    client = TestClient(TestServer(make_app(h.billing, scrape, hosted=h)))
    await client.start_server()
    try:
        payload = None
        for _ in range(200):
            response = await client.get(f"/v2/batch/scrape/{job}", headers=headers)
            payload = await response.json()
            if payload.get("status") == "completed":
                break
            await asyncio.sleep(0.01)
        assert response.status == 200
        assert payload["status"] == "completed"
        assert payload["completed"] == payload["total"] == 2
        assert payload["creditsUsed"] == 2
        assert h.billing.remaining(account) == 109998
        assert h.billing.db.execute("SELECT SUM(credits) FROM usage WHERE account=?", (account,)).fetchone()[0] == 2
        assert [row["markdown"] for row in payload["data"]] == ["# Saved", "# Recovered"]
        assert calls == [urls[1]]
    finally:
        await client.close()


async def test_v2_batch_reports_per_url_errors_and_supports_cancel(hosted, monkeypatch):
    import rainbo_scrape.llm as llm

    _, client, headers = await _auth(hosted)
    response = await client.post("/v2/batch/scrape", headers=headers, json={
        "urls": ["https://example.com/ok", "https://example.com/failure"],
        "formats": ["markdown"],
    })
    job = (await response.json())["id"]
    for _ in range(30):
        status = await client.get(f"/v2/batch/scrape/{job}", headers=headers)
        status_payload = await status.json()
        if status_payload["status"] != "scraping":
            break
        await asyncio.sleep(0.01)
    assert status_payload["status"] == "completed"
    assert status_payload["completed"] == 2
    assert "links" not in status_payload["data"][0]
    response = await client.get(f"/v2/batch/scrape/{job}/errors", headers=headers)
    errors = await response.json()
    assert len(errors["errors"]) == 1
    assert errors["errors"][0]["url"] == "https://example.com/failure"

    response = await client.post("/v2/batch/scrape", headers=headers, json={
        "urls": ["https://example.com/html"], "formats": ["html", "rawHtml"],
    })
    job = (await response.json())["id"]
    for _ in range(20):
        status = await client.get(f"/v2/batch/scrape/{job}", headers=headers)
        status_payload = await status.json()
        if status_payload["status"] != "scraping":
            break
        await asyncio.sleep(0.01)
    assert status_payload["data"][0]["html"] == "<main><h1>Fixture</h1></main>"
    assert "rawHtml" in status_payload["data"][0]

    monkeypatch.setattr(llm, "providers", lambda: [("fixture", "https://fixture.invalid", "unused", "fixture")])
    response = await client.post("/v2/batch/scrape", headers=headers, json={
        "urls": ["https://example.com/json"],
        "formats": [{"type": "json", "prompt": "Extract the heading."}],
    })
    job = (await response.json())["id"]
    for _ in range(20):
        status = await client.get(f"/v2/batch/scrape/{job}", headers=headers)
        status_payload = await status.json()
        if status_payload["status"] != "scraping":
            break
        await asyncio.sleep(0.01)
    assert status_payload["status"] == "completed"
    assert status_payload["data"][0]["json"] == {"heading": "Fixture"}

    response = await client.post("/v2/batch/scrape", headers=headers, json={
        "urls": ["https://example.com/slow"] * 5,
    })
    job = (await response.json())["id"]
    response = await client.delete(f"/v2/batch/scrape/{job}", headers=headers)
    assert response.status == 200 and (await response.json())["status"] == "cancelled"


async def test_v2_batch_signed_webhooks_retry_and_filter_events(hosted):
    h, old_client, _, _, _, _ = hosted
    _, _, headers = await _auth(hosted)
    account = h.billing.account_for(headers["Authorization"][7:])
    await old_client.close()
    allow_delivery = False
    attempts = []
    delivered = []

    async def sender(url, body, request_headers):
        assert url == "https://hooks.example.com/apex"
        attempts.append((body, dict(request_headers)))
        if not allow_delivery:
            return 503
        delivered.append((body, dict(request_headers)))
        return 204

    async def scrape(url, **options):
        return {"markdown": "# Webhook fixture", "rendered": False, "url": url,
                "receipt": {"final_url": url, "status": 200}, "kind": "text"}

    client = TestClient(TestServer(make_app(h.billing, scrape, hosted=h, batch_webhook_sender=sender)))
    await client.start_server()
    try:
        invalid = await client.post("/v2/batch/scrape", headers=headers, json={
            "urls": ["https://example.com"], "webhook": {"url": "http://hooks.example.com/apex"},
        })
        assert invalid.status == 400
        assert (await invalid.json())["code"] == "invalid_webhook"
        private = await client.post("/v2/batch/scrape", headers=headers, json={
            "urls": ["https://example.com"], "webhook": {"url": "https://127.0.0.1/apex"},
        })
        assert private.status == 400

        response = await client.post("/v2/batch/scrape", headers=headers, json={
            "urls": ["https://example.com/webhook"], "formats": ["markdown"],
            "webhook": {"url": "https://hooks.example.com/apex", "events": ["page", "completed"],
                        "metadata": {"source": "fixture"}},
        })
        created = await response.json()
        assert response.status == 200 and created["signingSecret"].startswith("whsec_")
        job = created["id"]
        for _ in range(100):
            status = await client.get(f"/v2/batch/scrape/{job}", headers=headers)
            if (await status.json())["status"] == "completed":
                break
            await asyncio.sleep(0.01)
        assert (await status.json())["status"] == "completed"
        for _ in range(100):
            pending = h.db.execute("SELECT COUNT(*) FROM firecrawl_batch_webhook_events WHERE job=? AND state='pending'",
                                   (job,)).fetchone()[0]
            if pending == 2 and len(attempts) >= 2:
                break
            await asyncio.sleep(0.01)
        rows = h.db.execute("SELECT id,event_type,attempts,state FROM firecrawl_batch_webhook_events WHERE job=? ORDER BY event_type",
                            (job,)).fetchall()
        assert [row[1] for row in rows] == ["batch_scrape.completed", "batch_scrape.page"]
        assert all(row[2:] == (1, "pending") for row in rows)

        allow_delivery = True
        h.db.execute("UPDATE firecrawl_batch_webhook_events SET next_attempt=0 WHERE job=? AND state='pending'", (job,))
        for _ in range(100):
            if len(delivered) >= 2:
                break
            await asyncio.sleep(0.02)
        assert len(delivered) == 2
        event_types = set()
        for body, request_headers in delivered:
            payload = json.loads(body)
            event_types.add(payload["type"])
            assert payload["id"] == job
            assert payload["metadata"] == {"source": "fixture"}
            assert request_headers["X-Apex-Event-Id"] == payload["webhookId"]
            timestamp, digest = request_headers["X-Apex-Signature"].split(",v1=")
            timestamp = timestamp.removeprefix("t=")
            expected = hmac.new(created["signingSecret"].encode(), timestamp.encode() + b"." + body,
                                hashlib.sha256).hexdigest()
            assert hmac.compare_digest(digest, expected)
        assert event_types == {"batch_scrape.page", "batch_scrape.completed"}
        assert h.db.execute("SELECT COUNT(*) FROM firecrawl_batch_webhook_events WHERE job=? AND state='delivered'",
                            (job,)).fetchone()[0] == 2
        assert h.db.execute("SELECT MIN(attempts) FROM firecrawl_batch_webhook_events WHERE job=?",
                            (job,)).fetchone()[0] == 2
        assert h.billing.account_for(headers["Authorization"][7:]) == account
    finally:
        await client.close()


async def test_v2_batch_webhook_summarizes_oversized_page_payload(hosted):
    h, old_client, _, _, _, _ = hosted
    _, _, headers = await _auth(hosted)
    await old_client.close()
    delivered = []

    async def sender(url, body, request_headers):
        delivered.append(json.loads(body))
        return 204

    async def scrape(url, **options):
        return {"markdown": "# Large screenshot", "screenshot_png_base64": "A" * 150_000,
                "rendered": False, "url": url,
                "receipt": {"final_url": url, "status": 200}, "kind": "text"}

    client = TestClient(TestServer(make_app(h.billing, scrape, hosted=h, batch_webhook_sender=sender)))
    await client.start_server()
    try:
        response = await client.post("/v2/batch/scrape", headers=headers, json={
            "urls": ["https://example.com/large"], "formats": ["screenshot"],
            "webhook": {"url": "https://hooks.example.com/apex", "events": ["page"]},
        })
        created = await response.json()
        job = created["id"]
        for _ in range(200):
            if h.db.execute("SELECT status FROM firecrawl_batch_jobs WHERE id=?", (job,)).fetchone() == ("completed",):
                break
            await asyncio.sleep(0.01)
        for _ in range(200):
            if delivered:
                break
            await asyncio.sleep(0.01)
        assert len(delivered) == 1
        event = delivered[0]
        assert event["type"] == "batch_scrape.page"
        assert event["webhookTruncated"] is True
        assert event["data"][0]["webhookTruncated"] is True
        assert "retrieve this page from batch status" in event["data"][0]["message"]
        assert event["data"][0]["metadata"]["url"] == "https://example.com/large"
        assert h.db.execute("SELECT status FROM firecrawl_batch_jobs WHERE id=?", (job,)).fetchone() == ("completed",)
    finally:
        await client.close()


def test_openapi_keeps_compatibility_routes_at_top_level_and_documents_batch_webhooks():
    spec = json.loads((Path(__file__).parents[1] / "site" / "openapi.json").read_text(encoding="utf-8"))
    paths = spec["paths"]
    assert "post" in paths["/keys"] and "/v2/batch/scrape" in paths
    assert not any(any(key.startswith("/") for key in item) for item in paths.values())
    webhook = paths["/v2/batch/scrape"]["post"]["requestBody"]["content"]["application/json"]["schema"]["properties"]["webhook"]
    assert webhook["required"] == ["url"]
    assert webhook["properties"]["events"]["items"]["enum"] == ["started", "page", "completed"]
    scrape_options = paths["/v2/scrape"]["post"]["requestBody"]["content"]["application/json"]["schema"]["properties"]
    batch_options = paths["/v2/batch/scrape"]["post"]["requestBody"]["content"]["application/json"]["schema"]["properties"]
    crawl_options = paths["/v2/crawl"]["post"]["requestBody"]["content"]["application/json"]["schema"]["properties"]["scrapeOptions"]["properties"]
    assert {scrape_options["waitFor"]["maximum"], batch_options["waitFor"]["maximum"], crawl_options["waitFor"]["maximum"]} == {5000}


async def test_v2_crawl_lifecycle_reuses_durable_apex_job(hosted, monkeypatch):
    import rainbo_scrape.llm as llm

    h, client, headers = await _auth(hosted)
    response = await client.post("/v2/crawl", headers=headers,
                                json={"url": "https://example.com/start", "limit": 1,
                                      "maxDiscoveryDepth": 0,
                                      "scrapeOptions": {"formats": [{"type": "markdown"}], "waitFor": 500}})
    payload = await response.json()
    assert response.status == 200 and payload["success"] is True
    job = payload["id"]
    for _ in range(20):
        status = await client.get(f"/v2/crawl/{job}", headers=headers)
        status_payload = await status.json()
        if status_payload["status"] != "scraping":
            break
        await asyncio.sleep(0.01)
    assert status.status == 200
    assert status_payload["status"] == "completed"
    assert status_payload["completed"] == status_payload["total"] == 1
    assert status_payload["data"][0]["markdown"] == "# Fixture"
    assert h.scrape_calls[-1][1]["actions"] == [{"type": "wait", "ms": 500}]

    monkeypatch.setattr(llm, "providers", lambda: [("fixture", "https://fixture.invalid", "unused", "fixture")])
    response = await client.post("/v2/crawl", headers=headers, json={
        "url": "https://example.com/json-crawl", "limit": 1, "maxDiscoveryDepth": 0,
        "scrapeOptions": {"formats": [{"type": "json", "prompt": "Extract the heading."}]},
    })
    payload = await response.json()
    for _ in range(20):
        status = await client.get(f"/v2/crawl/{payload['id']}", headers=headers)
        status_payload = await status.json()
        if status_payload["status"] != "scraping":
            break
        await asyncio.sleep(0.01)
    assert status_payload["status"] == "completed"
    assert status_payload["data"][0]["json"] == {"heading": "Fixture"}


async def test_v2_crawl_does_not_expand_access_scope_or_ignore_robots(hosted):
    h, client, headers = await _auth(hosted)
    for options in ({"ignoreRobotsTxt": True}, {"allowExternalLinks": True}):
        response = await client.post("/v2/crawl", headers=headers,
                                    json={"url": "https://example.com", **options})
        payload = await response.json()
        assert response.status == 400 and payload["success"] is False
    invalid = await client.post("/v2/crawl", headers=headers,
                                json={"url": "https://example.com", "includePaths": ["("]})
    assert invalid.status == 400
    response = await client.post("/v2/crawl", headers=headers, json={
        "url": "https://example.com/blog", "limit": 1, "maxDiscoveryDepth": 0,
        "includePaths": ["^/blog/"], "excludePaths": ["/blog/private"],
        "regexOnFullURL": False, "ignoreQueryParameters": True,
    })
    assert response.status == 200
    job = (await response.json())["id"]
    options = json.loads(h.db.execute("SELECT options FROM crawl_jobs WHERE id=?", (job,)).fetchone()[0])
    assert options["include_paths"] == ["^/blog/"]
    assert options["exclude_paths"] == ["/blog/private"]
    assert options["ignore_query_parameters"] is True


async def test_v2_crawl_errors_are_account_scoped_and_report_failed_pages(hosted):
    _, client, headers = await _auth(hosted)
    response = await client.post("/v2/crawl", headers=headers, json={
        "url": "https://example.com/failure", "limit": 1, "maxDiscoveryDepth": 0,
    })
    job = (await response.json())["id"]
    for _ in range(20):
        state = await client.get(f"/v2/crawl/{job}", headers=headers)
        if (await state.json())["status"] != "scraping":
            break
        await asyncio.sleep(0.01)
    errors = await client.get(f"/v2/crawl/{job}/errors", headers=headers)
    error_payload = await errors.json()
    assert errors.status == 200
    assert error_payload["errors"][0]["url"].endswith("/failure")
    assert error_payload["errors"][0]["code"] == "invalid_request"
    assert error_payload["errors"][0]["timestamp"].endswith("Z")
    assert error_payload["robotsBlocked"] == []
    missing = await client.get("/v2/crawl/not-a-job/errors", headers=headers)
    assert missing.status == 404


def test_crawl_path_filters_regex_scope_and_query_deduplication():
    from rainbo_scrape.hosted_crawl import _compile_path_patterns, _crawl_identity, _matches_crawl_url

    include = _compile_path_patterns(["^/docs/(api|guide)/.*$"], "includePaths")
    exclude = _compile_path_patterns(["/private(/|$)"], "excludePaths")
    assert _matches_crawl_url("https://docs.example/docs/api/v1?format=json", include, exclude)
    assert not _matches_crawl_url("https://docs.example/docs/marketing", include, exclude)
    assert not _matches_crawl_url("https://docs.example/docs/private/page", include, exclude)
    assert _matches_crawl_url("https://docs.example/docs/private/page", [], exclude, regex_on_full_url=True) is False
    assert _crawl_identity("https://docs.example/page?a=1#top", True) == "https://docs.example/page"
    assert _crawl_identity("https://docs.example/page?a=1#top") == "https://docs.example/page?a=1"
