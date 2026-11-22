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


async def test_official_firecrawl_python_sdk_v2_client_against_apex_routes(hosted):
    Firecrawl = pytest.importorskip("firecrawl").Firecrawl

    h, client, headers = await _auth(hosted)
    api_key = headers["Authorization"][7:]
    app = Firecrawl(api_key=api_key, api_url=str(client.make_url("/")).rstrip("/"), max_retries=1)
    scraped = await asyncio.to_thread(app.v2.scrape, "https://example.com", formats=["markdown"])
    assert scraped.markdown == "# Fixture"
    assert "ad blocking is not implemented by Apex" in scraped.warning
    assert "origin attribution is not recorded by Apex" in scraped.warning

    started = await asyncio.to_thread(app.v2.start_batch_scrape, ["https://example.com"])
    status = None
    for _ in range(30):
        status = await asyncio.to_thread(app.v2.get_batch_scrape_status, started.id)
        if status.status != "scraping":
            break
        await asyncio.sleep(0.01)
    assert status is not None and status.status == "completed"
    assert status.completed == status.total == 1
    assert status.data[0].markdown == "# Fixture"
    assert "origin attribution is not recorded by Apex" in status.data[0].warning

    unsupported = await client.post("/v2/scrape", headers=headers,
                                    json={"url": "https://example.com", "mobile": True})
    assert unsupported.status == 400
    assert (await unsupported.json())["code"] == "unsupported_option"


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


async def test_v2_crawl_lifecycle_reuses_durable_apex_job(hosted, monkeypatch):
    import rainbo_scrape.llm as llm

    _, client, headers = await _auth(hosted)
    response = await client.post("/v2/crawl", headers=headers,
                                json={"url": "https://example.com/start", "limit": 1,
                                      "maxDiscoveryDepth": 0,
                                      "scrapeOptions": {"formats": [{"type": "markdown"}]}})
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
