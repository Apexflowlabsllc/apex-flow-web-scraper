"""Browser-bound, one-time trial access on the hosted service."""
import hmac
import re
import secrets
import time

from aiohttp import web

from .billing import _hash
from .hosted_features import event

COOKIE = "scraper_trial"
SESSION = re.compile(r"trial_[a-f0-9]{32}")


def owned_trial(hosted, request, session_id):
    cookie_session, _, nonce = request.cookies.get(COOKIE, "").partition(".")
    if cookie_session != session_id or not SESSION.fullmatch(session_id) or not nonce:
        return None
    row = hosted.db.execute(
        "SELECT account, nonce FROM orders WHERE session=? AND plan='free' AND status='active'",
        (session_id,),
    ).fetchone()
    if not row or not hmac.compare_digest(row[1], _hash(nonce)):
        return None
    key = hosted.key_for(session_id, row[1])
    if hosted.billing.account_for(key) != row[0]:
        return None
    return {"key": key, "plan": "free", "credits_left": hosted.billing.remaining(row[0]),
            "api_base": hosted.site + "/v1"}


def install(hosted, app):
    async def start(request):
        ip_digest = hosted.limit(request, "trial", 12)
        body = await request.json()
        if not isinstance(body, dict) or body.get("terms") is not True:
            raise web.HTTPBadRequest(reason="Accept the terms to start a trial")
        session_id = request.cookies.get(COOKIE, "").partition(".")[0]
        if owned_trial(hosted, request, session_id):
            return web.json_response({"session_id": session_id})
        async with hosted.lock:
            db = hosted.db
            if db.execute("SELECT 1 FROM orders WHERE free_ip=?", (ip_digest,)).fetchone():
                return web.json_response({"error": "A trial already exists on this network. Open the original browser or use your saved API key."}, status=409)
            now = time.time()
            count = db.execute("SELECT COUNT(*) FROM orders WHERE plan='free' AND created>=?", (now-86400,)).fetchone()[0]
            if count >= 20:
                return web.json_response({"error": "Today's free trial capacity is full. Try again tomorrow."}, status=429)
            session_id = "trial_" + secrets.token_hex(16)
            nonce = secrets.token_urlsafe(32)
            digest = _hash(nonce)
            db.execute("BEGIN IMMEDIATE")
            try:
                account = hosted.billing.create_account(None, "free")
                db.execute("INSERT INTO orders(session,account,nonce,plan,status,created,free_ip) VALUES(?,?,?,'free','active',?,?)",
                           (session_id, account, digest, now, ip_digest))
                db.execute("INSERT INTO keys(hash,account,label) VALUES(?,?,?)",
                           (_hash(hosted.key_for(session_id, digest)), account, "Browser trial"))
                event(hosted, "activated", "free")
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
        response = web.json_response({"session_id": session_id})
        response.set_cookie(COOKIE, session_id+"."+nonce, secure=True, httponly=True, samesite="Lax",
                            max_age=2592000, path="/")
        return response

    app.router.add_post("/api/trial", start)
