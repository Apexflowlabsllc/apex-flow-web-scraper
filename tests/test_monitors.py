import asyncio
import hashlib
import hmac
import json
import secrets
import sqlite3
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from aiohttp.test_utils import TestClient, TestServer

from rainbo_scrape.api import make_app
from rainbo_scrape.billing import Billing, _hash
from rainbo_scrape.hosted import Hosted, SITE
from rainbo_scrape.hosted_monitors import _claim_monitor_event, _secret
from rainbo_scrape.policy import PublicResolver, ScrapeError


def add_account(h, plan, session):
    account = h.billing.create_account(None, plan)
    nonce = _hash("nonce-" + session)
    h.db.execute("INSERT INTO orders(session,account,nonce,plan,status,created,period_end) VALUES(?,?,?,?,'active',?,?)",
                 (session, account, nonce, plan, time.time(), time.time() + 86400))
    key = h.key_for(session, nonce)
    h.db.execute("INSERT INTO keys(hash,account,label) VALUES(?,?,?)", (_hash(key), account, "monitor test"))
    return account, {"Authorization": "Bearer " + key}


def test_monitor_event_claim_is_exclusive_and_stale_owner_cannot_finish():
    work_dir = Path(__file__).resolve().parents[1] / "work"
    work_dir.mkdir(exist_ok=True)
    db_path = work_dir / f"monitor-claim-{secrets.token_hex(8)}.sqlite3"
    db = sqlite3.connect(db_path, isolation_level=None, timeout=10)
    try:
        db.execute("CREATE TABLE monitor_events(id TEXT PRIMARY KEY,state TEXT,attempts INTEGER,next_attempt REAL,lease_until REAL,lease_token TEXT)")
        db.execute("INSERT INTO monitor_events VALUES('event','pending',0,0,NULL,NULL)")
        now = time.time()
        barrier = Barrier(2)

        def compete():
            connection = sqlite3.connect(db_path, isolation_level=None, timeout=10)
            try:
                barrier.wait()
                return _claim_monitor_event(connection, "event", now)
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: compete(), range(2)))
        winners = [claim for claim in results if claim is not None]
        assert len(winners) == 1 and winners[0][1] == 1
        first = winners[0]

        # Once a worker lease expires, another worker can recover the event. The
        # old worker's token cannot mark the recovered event delivered.
        second = _claim_monitor_event(db, "event", now + 121)
        assert second is not None and second[0] != first[0] and second[1] == 2
        stale_update = db.execute(
            "UPDATE monitor_events SET state='delivered' WHERE id='event' AND lease_token=?",
            (first[0],),
        ).rowcount
        assert stale_update == 0
        assert db.execute("SELECT state,lease_token FROM monitor_events WHERE id='event'").fetchone() == ("pending", second[0])
    finally:
        db.close()
        db_path.unlink(missing_ok=True)


@pytest.fixture
async def service(tmp_path, monkeypatch):
    monkeypatch.delenv("SCRAPER_GATEWAY_SECRET", raising=False)
    billing = Billing(tmp_path / "billing.db")
    h = Hosted(billing, "fixture", "fixture-webhook", "fixture-delivery")
    state = {"text": "original content", "calls": [], "deliveries": [], "delivery_statuses": []}

    async def scrape(url, **options):
        state["calls"].append((url, options))
        return {"url": url, "title": "Fixture", "text": state["text"],
                "markdown": state["text"], "rendered": False,
                "receipt": {"final_url": url}}

    async def sender(url, body, headers):
        state["deliveries"].append((url, body, headers))
        return state["delivery_statuses"].pop(0) if state["delivery_statuses"] else 204

    client = TestClient(TestServer(make_app(billing, scrape, hosted=h, monitor_sender=sender)))
    await client.start_server()
    state.update(scrape=scrape, sender=sender, client=client)
    yield h, client, state
    await state["client"].close()
    billing.db.close()


def create_body(**overrides):
    body = {"url": "https://example.com/pricing", "webhook_url": "https://hooks.example.test/apex",
            "interval_seconds": 3600, "confirm_recurring": True}
    return {**body, **overrides}


async def wait_until(predicate, timeout=4):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        await asyncio.sleep(0.05)
    assert predicate(), "condition did not become true before timeout"


async def test_create_requires_paid_plan_recurring_consent_https_and_cadence(service):
    h, client, _ = service
    free, free_auth = add_account(h, "free", "trial_monitor_free")
    assert (await client.post("/v1/monitors", json=create_body(), headers=free_auth)).status == 402
    paid, auth = add_account(h, "starter", "cs_test_monitor_create")
    assert (await client.post("/v1/monitors", json=create_body(confirm_recurring=False), headers=auth)).status == 400
    assert (await client.post("/v1/monitors", json=create_body(webhook_url="http://hooks.example.test/x"), headers=auth)).status == 400
    assert (await client.post("/v1/monitors", json=create_body(interval_seconds=3599), headers=auth)).status == 400
    response = await client.post("/v1/monitors", json=create_body(), headers=auth)
    body = await response.json()
    assert response.status == 201
    assert body["credits_per_successful_check"] == 1
    assert body["estimated_max_credits_per_30_days"] == 720
    assert body["signing_secret"].startswith("whsec_")
    assert h.db.execute("SELECT COUNT(*) FROM monitors WHERE account=?", (paid,)).fetchone()[0] == 1


async def test_monitor_is_account_scoped_and_lifecycle_deletes_event_history(service):
    h, client, _ = service
    account, auth = add_account(h, "starter", "cs_test_monitor_owner")
    other, other_auth = add_account(h, "starter", "cs_test_monitor_other")
    created = await (await client.post("/v1/monitors", json=create_body(), headers=auth)).json()
    ident = created["monitor"]["id"]
    hidden = await (await client.get("/v1/monitors", headers=other_auth)).json()
    assert hidden["monitors"] == []
    assert (await client.post("/v1/monitors/pause", json={"id": ident}, headers=other_auth)).status == 404
    assert (await client.post("/v1/monitors/pause", json={"id": ident}, headers=auth)).status == 200
    assert (await client.post("/v1/monitors/resume", json={"id": ident}, headers=auth)).status == 200
    h.db.execute("INSERT INTO monitor_events(id,monitor_id,account,created,payload,state,attempts,next_attempt) VALUES('evt',?,?,?,'{}','delivered',1,?)",
                 (ident, account, time.time(), time.time()))
    assert (await client.delete(f"/v1/monitors/{ident}", headers=auth)).status == 200
    assert h.db.execute("SELECT COUNT(*) FROM monitor_events WHERE monitor_id=?", (ident,)).fetchone()[0] == 0
    assert h.db.execute("SELECT COUNT(*) FROM monitors WHERE id=?", (ident,)).fetchone()[0] == 0


async def test_active_monitor_limit_is_enforced_and_reported(service):
    h, client, _ = service
    _, auth = add_account(h, "pass", "cs_test_monitor_cap")
    for n in range(5):
        response = await client.post("/v1/monitors", json=create_body(url=f"https://example.com/p{n}"), headers=auth)
        assert response.status == 201
    assert (await client.post("/v1/monitors", json=create_body(url="https://example.com/extra"), headers=auth)).status == 429
    usage = await (await client.get("/v1/usage", headers=auth)).json()
    assert usage["active_monitors"] == 5 and usage["limits"]["monitors"] == 5


async def test_inactive_accounts_can_manage_or_delete_monitors_but_cannot_resume(service):
    h, client, _ = service
    account, auth = add_account(h, "starter", "cs_test_monitor_expired")
    created = await (await client.post("/v1/monitors", json=create_body(), headers=auth)).json()
    ident = created["monitor"]["id"]
    h.db.execute("UPDATE orders SET period_end=0 WHERE account=?", (account,))
    usage = await client.get("/v1/usage", headers=auth)
    assert usage.status == 200
    paused = await client.post("/v1/monitors/pause", json={"id": ident}, headers=auth)
    assert paused.status == 200
    assert (await client.post("/v1/monitors/resume", json={"id": ident}, headers=auth)).status == 402
    assert (await client.delete(f"/v1/monitors/{ident}", headers=auth)).status == 200


async def test_scheduler_baselines_then_sends_signed_change_event_and_charges_successes(service):
    h, client, state = service
    account, auth = add_account(h, "starter", "cs_test_monitor_schedule")
    response = await client.post("/v1/monitors", json=create_body(), headers=auth)
    created = await response.json()
    ident = created["monitor"]["id"]
    h.db.execute("UPDATE monitors SET next_check=0 WHERE id=?", (ident,))
    await wait_until(lambda: h.db.execute("SELECT last_sha FROM monitors WHERE id=?", (ident,)).fetchone()[0] is not None)
    assert state["deliveries"] == []
    assert state["calls"][-1][1].get("robots") != "requested"
    state["text"] = "updated content"
    h.db.execute("UPDATE monitors SET next_check=0 WHERE id=?", (ident,))
    await wait_until(lambda: bool(state["deliveries"]))
    url, raw, headers = state["deliveries"][0]
    event = json.loads(raw)
    assert url == "https://hooks.example.test/apex"
    assert event["type"] == "page.changed" and event["monitor_id"] == ident
    assert "updated content" in event["diff"]
    ts, sig = headers["X-Apex-Signature"].removeprefix("t=").split(",v1=")
    expected = hmac.new(_secret(h, account, ident).encode(), ts.encode() + b"." + raw, hashlib.sha256).hexdigest()
    assert hmac.compare_digest(sig, expected)
    stored = h.db.execute("SELECT state,attempts,http_status FROM monitor_events WHERE id=?", (event["id"],)).fetchone()
    assert stored == ("delivered", 1, 204)
    assert h.billing.remaining(account) == 110000 - 2


async def test_monitor_pauses_when_successful_checks_cannot_be_paid(service):
    h, client, state = service
    account, auth = add_account(h, "pass", "cs_test_monitor_empty")
    created = await (await client.post("/v1/monitors", json=create_body(), headers=auth)).json()
    ident = created["monitor"]["id"]
    h.billing.charge(account, "page", 35000)
    h.db.execute("UPDATE monitors SET next_check=0 WHERE id=?", (ident,))
    await wait_until(lambda: h.db.execute("SELECT state FROM monitors WHERE id=?", (ident,)).fetchone()[0] == "paused")
    assert h.billing.remaining(account) == 0
    assert state["calls"] == []


async def test_monitor_schedule_recovers_after_service_restart_with_expired_lease(service):
    h, client, state = service
    _, auth = add_account(h, "starter", "cs_test_monitor_restart")
    created = await (await client.post("/v1/monitors", json=create_body(), headers=auth)).json()
    ident = created["monitor"]["id"]
    h.db.execute("UPDATE monitors SET next_check=0 WHERE id=?", (ident,))
    await wait_until(lambda: h.db.execute("SELECT last_check FROM monitors WHERE id=?", (ident,)).fetchone()[0] is not None)
    previous = h.db.execute("SELECT last_check FROM monitors WHERE id=?", (ident,)).fetchone()[0]
    await client.close()
    # Simulate a process stopping after claim but before the work finishes.
    h.db.execute("UPDATE monitors SET next_check=0,lease_until=? WHERE id=?", (time.time() - 1, ident))
    client = TestClient(TestServer(make_app(h.billing, state["scrape"], hosted=h, monitor_sender=state["sender"])))
    await client.start_server()
    state["client"] = client
    await wait_until(lambda: h.db.execute("SELECT last_check FROM monitors WHERE id=?", (ident,)).fetchone()[0] > previous)
    assert h.db.execute("SELECT lease_until FROM monitors WHERE id=?", (ident,)).fetchone()[0] is None


async def test_failed_webhook_retries_same_event_id(service):
    h, client, state = service
    _, auth = add_account(h, "starter", "cs_test_monitor_retry")
    state["delivery_statuses"] = [503, 204]
    created = await (await client.post("/v1/monitors", json=create_body(), headers=auth)).json()
    ident = created["monitor"]["id"]
    h.db.execute("UPDATE monitors SET next_check=0 WHERE id=?", (ident,))
    await wait_until(lambda: h.db.execute("SELECT last_sha FROM monitors WHERE id=?", (ident,)).fetchone()[0] is not None)
    state["text"] = "changed for retry"
    h.db.execute("UPDATE monitors SET next_check=0 WHERE id=?", (ident,))
    await wait_until(lambda: h.db.execute("SELECT attempts FROM monitor_events WHERE monitor_id=?", (ident,)).fetchone() == (1,))
    event = h.db.execute("SELECT id,attempts,state FROM monitor_events WHERE monitor_id=?", (ident,)).fetchone()
    assert event[1:] == (1, "pending")
    h.db.execute("UPDATE monitor_events SET next_attempt=0 WHERE id=?", (event[0],))
    await wait_until(lambda: h.db.execute("SELECT state FROM monitor_events WHERE id=?", (event[0],)).fetchone()[0] == "delivered")
    ids = [json.loads(item[1])["id"] for item in state["deliveries"]]
    assert ids == [event[0], event[0]]
    assert h.db.execute("SELECT attempts,http_status FROM monitor_events WHERE id=?", (event[0],)).fetchone() == (2, 204)


async def test_mcp_can_create_list_pause_and_delete_monitor(service):
    _, client, _ = service
    account, auth = add_account(service[0], "starter", "cs_test_monitor_mcp")

    async def call(name, arguments):
        response = await client.post("/v1/mcp", headers=auth, json={"jsonrpc": "2.0", "id": 1,
                               "method": "tools/call", "params": {"name": name, "arguments": arguments}})
        return json.loads((await response.json())["result"]["content"][0]["text"])

    tools_response = await (await client.post("/v1/mcp", headers=auth, json={"jsonrpc": "2.0", "id": 1,
                               "method": "tools/list", "params": {}})).json()
    names = {item["name"] for item in tools_response["result"]["tools"]}
    assert {"create_monitor", "list_monitors", "pause_monitor", "resume_monitor", "delete_monitor", "monitor_events"} <= names
    created = await call("create_monitor", {**create_body(), "confirm_recurring": True})
    ident = created["monitor"]["id"]
    assert (await call("pause_monitor", {"monitor_id": ident}))["monitor"]["state"] == "paused"
    assert (await call("list_monitors", {}))["monitors"][0]["id"] == ident
    assert (await call("delete_monitor", {"monitor_id": ident}))["deleted"] == ident


async def test_webhook_posts_never_follow_redirects():
    from rainbo_scrape.hosted_monitors import _post_webhook

    class Content:
        async def read(self, size):
            return b""

    class Response:
        status = 307
        content = Content()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    class Client:
        call = None

        def post(self, url, **options):
            self.call = (url, options)
            return Response()

    client = Client()
    status = await _post_webhook(client, "https://hooks.example.test/x", b"{}", {})
    assert status == 307
    assert client.call[1]["allow_redirects"] is False


async def test_webhook_resolver_blocks_private_dns_answers(monkeypatch):
    class Loop:
        async def getaddrinfo(self, host, port, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", port))]

    monkeypatch.setattr("asyncio.get_running_loop", lambda: Loop())
    with pytest.raises(ScrapeError, match="Only public internet addresses"):
        await PublicResolver().resolve("hooks.example.test", 443)
