"""Paid, durable change monitors with bounded and signed webhook delivery."""
import asyncio
import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from urllib.parse import urlsplit

import aiohttp
from aiohttp import web

from .policy import PublicResolver, check_url

MIN_INTERVAL = 3600
MAX_INTERVAL = 30 * 86400
MAX_MONITORS_PER_TICK = 20
MAX_EVENTS_PER_TICK = 20


def prepare(h):
    h.db.executescript("""
        CREATE TABLE IF NOT EXISTS monitors(
            id TEXT PRIMARY KEY, account TEXT NOT NULL, url TEXT NOT NULL,
            webhook_url TEXT NOT NULL, interval_seconds INTEGER NOT NULL,
            state TEXT NOT NULL, created REAL NOT NULL, next_check REAL NOT NULL,
            lease_until REAL, lease_token TEXT,
            last_check REAL, last_sha TEXT, failures INTEGER NOT NULL DEFAULT 0,
            last_error TEXT);
        CREATE INDEX IF NOT EXISTS monitors_due ON monitors(state,next_check);
        CREATE INDEX IF NOT EXISTS monitors_account ON monitors(account,state);
        CREATE TABLE IF NOT EXISTS monitor_events(
            id TEXT PRIMARY KEY, monitor_id TEXT NOT NULL, account TEXT NOT NULL,
            created REAL NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL,
            http_status INTEGER, last_error TEXT, lease_until REAL, lease_token TEXT);
        CREATE INDEX IF NOT EXISTS monitor_events_due ON monitor_events(state,next_attempt);
        CREATE INDEX IF NOT EXISTS monitor_events_account ON monitor_events(account,monitor_id,created);
    """)
    columns = {row[1] for row in h.db.execute("PRAGMA table_info(monitors)")}
    if "lease_until" not in columns:
        h.db.execute("ALTER TABLE monitors ADD COLUMN lease_until REAL")
    if "lease_token" not in columns:
        h.db.execute("ALTER TABLE monitors ADD COLUMN lease_token TEXT")
    event_columns = {row[1] for row in h.db.execute("PRAGMA table_info(monitor_events)")}
    if "lease_until" not in event_columns:
        h.db.execute("ALTER TABLE monitor_events ADD COLUMN lease_until REAL")
    if "lease_token" not in event_columns:
        h.db.execute("ALTER TABLE monitor_events ADD COLUMN lease_token TEXT")
    h.db.execute("CREATE INDEX IF NOT EXISTS monitor_events_lease_due ON monitor_events(state,next_attempt,lease_until)")


def _claim_monitor_check(db, monitor_id, now, lease_seconds=180):
    """Atomically claim one due monitor check across workers sharing the database."""
    token = secrets.token_urlsafe(18)
    claimed = db.execute(
        "UPDATE monitors SET lease_until=?,lease_token=? WHERE id=? AND state='active' "
        "AND next_check<=? AND (lease_until IS NULL OR lease_until<=?)",
        (now + lease_seconds, token, monitor_id, now, now),
    ).rowcount
    return token if claimed else None


def _claim_monitor_event(db, event_id, now, lease_seconds=120):
    """Atomically claim a due webhook attempt; event IDs remain stable across retries."""
    token = secrets.token_urlsafe(18)
    claimed = db.execute(
        "UPDATE monitor_events SET lease_until=?,lease_token=?,attempts=attempts+1 "
        "WHERE id=? AND state='pending' AND next_attempt<=? "
        "AND (lease_until IS NULL OR lease_until<=?)",
        (now + lease_seconds, token, event_id, now, now),
    ).rowcount
    if not claimed:
        return None
    row = db.execute("SELECT attempts FROM monitor_events WHERE id=? AND lease_token=?", (event_id, token)).fetchone()
    return (token, row[0]) if row else None


def _secret(h, account, monitor_id):
    """Derive a monitor-specific delivery secret without storing another secret at rest."""
    digest = hmac.new(h.delivery_secret.encode(),
                      ("apex-monitor-v1:" + account + ":" + monitor_id).encode(),
                      hashlib.sha256).digest()
    return "whsec_" + base64.urlsafe_b64encode(digest).decode().rstrip("=")


def _public(monitor):
    return {"id": monitor[0], "url": monitor[1], "webhook_url": monitor[2],
            "interval_seconds": monitor[3], "state": monitor[4], "created_at": monitor[5],
            "next_check_at": monitor[6], "last_check_at": monitor[7],
            "last_error": monitor[8]}


async def _post_webhook(client, url, body, headers):
    """Never follow a webhook redirect; the connector validates DNS at connect time."""
    async with client.post(url, data=body, headers=headers, allow_redirects=False) as response:
        await response.content.read(1024)
        return response.status


def install(h, app, perform, webhook_sender=None):
    prepare(h)

    async def create(request):
        return await create_for(request["account"], await request.json())

    async def create_for(account, body):
        allowed = {"url", "webhook_url", "interval_seconds", "confirm_recurring"}
        if not isinstance(body, dict) or set(body) - allowed:
            raise ValueError("Use url, webhook_url, interval_seconds and confirm_recurring")
        if body.get("confirm_recurring") is not True:
            raise ValueError("Set confirm_recurring=true to authorize recurring credit use")
        limits = h.billing.limits(account)
        if limits.get("monitors", 0) <= 0:
            return web.json_response({"error": "Scheduled monitors require a paid plan.", "code": "paid_plan_required"}, status=402)
        if not h.entitled(account):
            return web.json_response({"error": "The paid period is inactive; renew before creating monitors.",
                                      "code": "inactive_plan", "buy_url": h.site + "/#pricing"}, status=402)
        if h.billing.remaining(account) < 1:
            return web.json_response({"error": "At least one credit is required to start a monitor.",
                                      "code": "insufficient_credits", "credits_left": 0,
                                      "buy_url": h.site + "/#pricing"}, status=402)
        url = check_url(body.get("url"))
        webhook = check_url(body.get("webhook_url"))
        if urlsplit(webhook).scheme != "https" or urlsplit(webhook).port not in (None, 443):
            raise ValueError("webhook_url must use HTTPS on port 443")
        interval = body.get("interval_seconds", 86400)
        if isinstance(interval, bool) or not isinstance(interval, int) or not MIN_INTERVAL <= interval <= MAX_INTERVAL:
            raise ValueError(f"interval_seconds must be an integer from {MIN_INTERVAL} to {MAX_INTERVAL}")
        total = h.db.execute("SELECT COUNT(*) FROM monitors WHERE account=?", (account,)).fetchone()[0]
        if total >= limits["monitors"]:
            return web.json_response({"error": "This plan's monitor limit has been reached. Delete a monitor before creating another.",
                                      "limit": limits["monitors"], "code": "monitor_limit"}, status=429)
        ident = secrets.token_urlsafe(18)
        now = time.time()
        jitter = secrets.randbelow(max(1, min(121, interval // 10 + 1)))
        next_check = now + interval + jitter
        h.db.execute("INSERT INTO monitors(id,account,url,webhook_url,interval_seconds,state,created,next_check) VALUES(?,?,?,?,?,'active',?,?)",
                     (ident, account, url, webhook, interval, now, next_check))
        # One credit per successful plain-page check. This is a worst-case estimate; failed reads are refunded.
        monthly_checks = (30 * 86400 + interval - 1) // interval
        return web.json_response({"monitor": {"id": ident, "url": url, "webhook_url": webhook,
                                 "interval_seconds": interval, "state": "active", "created_at": now,
                                 "next_check_at": next_check},
                                 "credits_per_successful_check": 1,
                                 "estimated_max_credits_per_30_days": monthly_checks,
                                 "credits_remaining": h.billing.remaining(account),
                                 "signing_secret": _secret(h, account, ident),
                                 "signing_secret_notice": "Shown once. Store it securely; verify X-Apex-Signature against the exact request body."}, status=201)

    async def list_for(account, _body=None):
        rows = h.db.execute("SELECT id,url,webhook_url,interval_seconds,state,created,next_check,last_check,last_error FROM monitors WHERE account=? ORDER BY created DESC LIMIT 500",
                            (account,)).fetchall()
        return web.json_response({"monitors": [_public(row) for row in rows], "limit": h.billing.limits(account).get("monitors", 0)})

    async def list_route(request):
        return await list_for(request["account"])

    async def change_state(request, state):
        return _state_for(h, request["account"], await request.json(), state)

    async def pause(request):
        return await change_state(request, "paused")

    async def resume(request):
        return _resume_for(h, request["account"], await request.json())

    async def delete(request):
        account, ident = request["account"], request.match_info["id"]
        h.db.execute("DELETE FROM monitor_events WHERE monitor_id=? AND account=?", (ident, account))
        deleted = h.db.execute("DELETE FROM monitors WHERE id=? AND account=?", (ident, account)).rowcount
        if not deleted:
            raise web.HTTPNotFound(text=json.dumps({"error": "Monitor not found"}), content_type="application/json")
        return web.json_response({"deleted": ident})

    async def events_for(account, body):
        ident = body.get("id") if isinstance(body, dict) else None
        limit = body.get("limit", 20) if isinstance(body, dict) else 20
        if not isinstance(ident, str) or not ident:
            raise ValueError("id is required")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be 1–100")
        if not h.db.execute("SELECT 1 FROM monitors WHERE id=? AND account=?", (ident, account)).fetchone():
            return web.json_response({"error": "Monitor not found", "code": "not_found"}, status=404)
        rows = h.db.execute("SELECT id,created,payload,state,attempts,http_status,last_error FROM monitor_events WHERE monitor_id=? AND account=? ORDER BY created DESC LIMIT ?",
                            (ident, account, limit)).fetchall()
        return web.json_response({"events": [{"id": r[0], "created_at": r[1], "event": json.loads(r[2]),
                                               "delivery_state": r[3], "attempts": r[4],
                                               "last_http_status": r[5], "last_error": r[6]} for r in rows]})

    async def events_route(request):
        try:
            limit = int(request.query.get("limit", "20"))
        except ValueError:
            raise ValueError("limit must be 1–100")
        return await events_for(request["account"], {"id": request.match_info["id"], "limit": limit})

    async def pause_for(account, body):
        return _state_for(h, account, body, "paused")

    async def resume_for(account, body):
        return _resume_for(h, account, body)

    async def delete_for(account, body):
        ident = body.get("id") if isinstance(body, dict) else None
        if not isinstance(ident, str) or not ident:
            raise ValueError("id is required")
        h.db.execute("DELETE FROM monitor_events WHERE monitor_id=? AND account=?", (ident, account))
        deleted = h.db.execute("DELETE FROM monitors WHERE id=? AND account=?", (ident, account)).rowcount
        if not deleted:
            return web.json_response({"error": "Monitor not found", "code": "not_found"}, status=404)
        return web.json_response({"deleted": ident})

    async def delete_post(request):
        return await delete_for(request["account"], await request.json())

    async def events_post(request):
        return await events_for(request["account"], await request.json())

    async def check_one(row):
        ident, account, url, interval, last_sha = row
        now = time.time()
        limit = h.billing.limits(account).get("monitors", 0)
        active = h.db.execute("SELECT id FROM monitors WHERE account=? AND state='active' ORDER BY created,id",
                              (account,)).fetchall()
        if not h.entitled(account):
            h.db.execute("UPDATE monitors SET state='paused',lease_until=NULL,lease_token=NULL,last_error='Paid period inactive.' WHERE account=? AND state='active'",
                         (account,))
            return
        for stale in active[limit:]:
            h.db.execute("UPDATE monitors SET state='paused',lease_until=NULL,lease_token=NULL,last_error='Plan monitor limit changed; choose monitors to resume.' WHERE id=? AND state='active'",
                         (stale[0],))
        if limit == 0:
            return
        # Claim the check before awaiting network work so another worker/process cannot duplicate it.
        lease_token = _claim_monitor_check(h.db, ident, now)
        if not lease_token:
            return
        try:
            # Scheduled automation obeys robots.txt, even though an explicitly named one-off
            # scrape may be requested by a customer when a site disallows automated access.
            response = await perform(account, {"url": url, "render": "never", "fallback": "none", "outputs": ["diff"]}, named=False)
            data = json.loads(response.body)
            if response.status != 200:
                failures = h.db.execute("SELECT failures FROM monitors WHERE id=?", (ident,)).fetchone()
                count = (failures[0] if failures else 0) + 1
                if response.status == 402:
                    h.db.execute("UPDATE monitors SET state='paused',lease_until=NULL,lease_token=NULL,last_check=?,failures=?,last_error=? WHERE id=? AND lease_token=?",
                                 (now, count, "Insufficient credits; monitor paused.", ident, lease_token))
                else:
                    delay = min(900 * (2 ** min(count - 1, 4)), 21600)
                    h.db.execute("UPDATE monitors SET next_check=?,lease_until=NULL,lease_token=NULL,last_check=?,failures=?,last_error=? WHERE id=? AND state='active' AND lease_token=?",
                                 (now + delay, now, count, str(data.get("error") or "Check failed")[:300], ident, lease_token))
                return
            change = data.get("change") or {}
            sha = change.get("content_sha256")
            if not isinstance(sha, str) or not sha:
                raise ValueError("Successful scrape did not return a content hash")
            jitter = secrets.randbelow(max(1, min(121, interval // 10 + 1)))
            updated = h.db.execute("UPDATE monitors SET next_check=?,lease_until=NULL,lease_token=NULL,last_check=?,last_sha=?,failures=0,last_error=NULL WHERE id=? AND state='active' AND lease_token=?",
                                   (now + interval + jitter, now, sha, ident, lease_token)).rowcount
            if not updated:
                return
            if last_sha and last_sha != sha:
                event_id = secrets.token_urlsafe(18)
                title = (data.get("result") or {}).get("title")
                payload = {"id": event_id, "type": "page.changed", "created_at": now,
                           "monitor_id": ident, "url": url, "title": title,
                           "previous_sha256": last_sha, "content_sha256": sha,
                           "diff": (change.get("diff") or "")[:12000]}
                h.db.execute("INSERT INTO monitor_events(id,monitor_id,account,created,payload,state,attempts,next_attempt) VALUES(?,?,?,?,?,'pending',0,?)",
                             (event_id, ident, account, now, json.dumps(payload, separators=(",", ":")), now))
                h.db.execute("DELETE FROM monitor_events WHERE account=? AND id NOT IN (SELECT id FROM monitor_events WHERE account=? ORDER BY created DESC LIMIT 1000)",
                             (account, account))
        except Exception as exc:
            logging.warning("scheduled monitor check failed id=%s type=%s", ident, type(exc).__name__)
            record = h.db.execute("SELECT failures FROM monitors WHERE id=?", (ident,)).fetchone()
            if record:
                count = record[0] + 1
                delay = min(900 * (2 ** min(count - 1, 4)), 21600)
                h.db.execute("UPDATE monitors SET next_check=?,lease_until=NULL,lease_token=NULL,last_check=?,failures=?,last_error=? WHERE id=? AND state='active' AND lease_token=?",
                             (now + delay, now, count, "Check failed; retry scheduled.", ident, lease_token))

    async def deliver_one(row, client):
        event_id, monitor_id, account, payload = row
        claimed = _claim_monitor_event(h.db, event_id, time.time())
        if not claimed:
            return
        lease_token, attempts = claimed
        now = int(time.time())
        secret = _secret(h, account, monitor_id).encode()
        body = payload.encode()
        signature = hmac.new(secret, str(now).encode() + b"." + body, hashlib.sha256).hexdigest()
        target = h.db.execute("SELECT webhook_url FROM monitors WHERE id=? AND account=? AND state!='deleted'",
                              (monitor_id, account)).fetchone()
        if not target:
            h.db.execute("UPDATE monitor_events SET state='cancelled',lease_until=NULL,lease_token=NULL,last_error='Monitor deleted' WHERE id=? AND lease_token=?", (event_id, lease_token))
            return
        response_status = None
        try:
            headers = {"Content-Type": "application/json", "X-Apex-Event-Id": event_id,
                       "X-Apex-Signature": f"t={now},v1={signature}"}
            if webhook_sender:
                response_status = await webhook_sender(target[0], body, headers)
            else:
                response_status = await _post_webhook(client, target[0], body, headers)
            if 200 <= response_status < 300:
                h.db.execute("UPDATE monitor_events SET state='delivered',lease_until=NULL,lease_token=NULL,http_status=?,last_error=NULL WHERE id=? AND state='pending' AND lease_token=?",
                             (response_status, event_id, lease_token))
            else:
                raise aiohttp.ClientResponseError(None, (), status=response_status)
        except Exception as exc:
            if attempts >= 6:
                h.db.execute("UPDATE monitor_events SET state='failed',lease_until=NULL,lease_token=NULL,http_status=?,last_error=? WHERE id=? AND state='pending' AND lease_token=?",
                             (response_status, type(exc).__name__, event_id, lease_token))
            else:
                delay = min(30 * (2 ** (attempts - 1)), 1800)
                h.db.execute("UPDATE monitor_events SET lease_until=NULL,lease_token=NULL,next_attempt=?,http_status=?,last_error=? WHERE id=? AND state='pending' AND lease_token=?",
                             (time.time() + delay, response_status, type(exc).__name__, event_id, lease_token))

    async def scheduler(app):
        timeout = aiohttp.ClientTimeout(total=10, connect=4, sock_read=5)
        connector = aiohttp.TCPConnector(resolver=PublicResolver(), ttl_dns_cache=0)
        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as client:
            while True:
                try:
                    now = time.time()
                    due = h.db.execute("SELECT id,account,url,interval_seconds,last_sha FROM monitors WHERE state='active' AND next_check<=? AND (lease_until IS NULL OR lease_until<=?) ORDER BY next_check LIMIT ?",
                                       (now, now, MAX_MONITORS_PER_TICK)).fetchall()
                    # Small concurrent groups avoid an unbounded scrape fan-out.
                    for offset in range(0, len(due), 4):
                        await asyncio.gather(*(check_one(row) for row in due[offset:offset + 4]))
                    pending = h.db.execute("SELECT id,monitor_id,account,payload FROM monitor_events WHERE state='pending' AND next_attempt<=? AND (lease_until IS NULL OR lease_until<=?) ORDER BY created LIMIT ?",
                                           (time.time(), time.time(), MAX_EVENTS_PER_TICK)).fetchall()
                    for offset in range(0, len(pending), 4):
                        await asyncio.gather(*(deliver_one(row, client) for row in pending[offset:offset + 4]))
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logging.exception("scheduled monitor worker iteration failed")
                await asyncio.sleep(1)

    async def monitor_context(app):
        task = asyncio.create_task(scheduler(app), name="apex-webscraper-monitors")
        yield
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    app.cleanup_ctx.append(monitor_context)
    app.router.add_post("/v1/monitors", create)
    app.router.add_post("/v1/monitors/create", create)
    app.router.add_get("/v1/monitors", list_route)
    app.router.add_get("/v1/monitors/list", list_route)
    app.router.add_post("/v1/monitors/pause", pause)
    app.router.add_post("/v1/monitors/resume", resume)
    app.router.add_post("/v1/monitors/delete", delete_post)
    app.router.add_post("/v1/monitors/events", events_post)
    app.router.add_delete("/v1/monitors/{id}", delete)
    app.router.add_get("/v1/monitors/{id}/events", events_route)
    return {"/monitors/create": create_for, "/monitors/list": list_for,
            "/monitors/pause": pause_for, "/monitors/resume": resume_for,
            "/monitors/delete": delete_for, "/monitors/events": events_for}


def _state_for(h, account, body, state):
    ident = body.get("id") if isinstance(body, dict) else None
    if not isinstance(ident, str) or not ident:
        raise ValueError("id is required")
    row = h.db.execute("SELECT interval_seconds FROM monitors WHERE id=? AND account=?", (ident, account)).fetchone()
    if not row:
        return web.json_response({"error": "Monitor not found", "code": "not_found"}, status=404)
    interval = row[0]
    jitter = secrets.randbelow(max(1, min(121, interval // 10 + 1))) if state == "active" else 0
    next_check = time.time() + interval + jitter if state == "active" else 0
    updated = h.db.execute("UPDATE monitors SET state=?,next_check=?,lease_until=NULL,lease_token=NULL,last_error=NULL WHERE id=? AND account=?",
                           (state, next_check, ident, account)).rowcount
    if not updated:
        return web.json_response({"error": "Monitor not found", "code": "not_found"}, status=404)
    row = h.db.execute("SELECT id,url,webhook_url,interval_seconds,state,created,next_check,last_check,last_error FROM monitors WHERE id=? AND account=?",
                       (ident, account)).fetchone()
    return web.json_response({"monitor": _public(row)})


def _resume_for(h, account, body):
    ident = body.get("id") if isinstance(body, dict) else None
    if not isinstance(ident, str) or not ident:
        raise ValueError("id is required")
    row = h.db.execute("SELECT state FROM monitors WHERE id=? AND account=?", (ident, account)).fetchone()
    if not row:
        return web.json_response({"error": "Monitor not found", "code": "not_found"}, status=404)
    if not h.entitled(account):
        return web.json_response({"error": "The paid period is inactive; renew before resuming monitors.",
                                  "code": "inactive_plan", "buy_url": h.site + "/#pricing"}, status=402)
    if h.billing.remaining(account) < 1:
        return web.json_response({"error": "At least one credit is required to resume a monitor.",
                                  "code": "insufficient_credits", "credits_left": 0,
                                  "buy_url": h.site + "/#pricing"}, status=402)
    if row[0] == "active":
        return _state_for(h, account, body, "active")
    active = h.db.execute("SELECT COUNT(*) FROM monitors WHERE account=? AND state='active'", (account,)).fetchone()[0]
    if active >= h.billing.limits(account).get("monitors", 0):
        return web.json_response({"error": "This plan's active-monitor limit has been reached.", "code": "monitor_limit"}, status=429)
    return _state_for(h, account, body, "active")
