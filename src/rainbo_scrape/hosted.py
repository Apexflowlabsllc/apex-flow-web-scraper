"""Stripe-backed hosted service. Secrets stay in the service environment."""
import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
from pathlib import Path

import aiohttp
from aiohttp import web
from .billing import Billing, PLANS, _hash
from . import trials

PRODUCT = "apex-flow-web-scraper"
SITE = "https://webscraper.apexflowlabs.com"
ORIGINS = {SITE, "https://apex-flow-web-scraper.vercel.app"}


class Hosted:
    def __init__(self, billing, stripe_key, webhook_secret, delivery_secret, site=SITE):
        self.billing = billing
        self.db = billing.db
        self.stripe_key = stripe_key
        self.webhook_secret = webhook_secret
        self.delivery_secret = delivery_secret
        self.site = site
        self.lock = asyncio.Lock()
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS orders (
                session TEXT PRIMARY KEY, account TEXT UNIQUE, subscription TEXT UNIQUE,
                customer TEXT, nonce TEXT, plan TEXT, period_start REAL DEFAULT 0,
                period_end REAL DEFAULT 0, status TEXT, email TEXT, created REAL,
                free_ip TEXT);
            CREATE TABLE IF NOT EXISTS request_limits (bucket TEXT, ts REAL);
            CREATE INDEX IF NOT EXISTS limits_bucket ON request_limits(bucket,ts);
            CREATE UNIQUE INDEX IF NOT EXISTS free_email ON orders(email) WHERE plan='free';
            CREATE UNIQUE INDEX IF NOT EXISTS free_ip ON orders(free_ip) WHERE plan='free';
        """)
        from .hosted_features import prepare
        prepare(self)

    def save_page(self, account, result, diff=False):
        from .hosted_features import save_page
        return save_page(self, account, result, diff)

    async def stripe(self, method, path, form=None, idempotency=None):
        headers = {"Authorization": "Bearer " + self.stripe_key,
                   "Stripe-Version": "2025-06-30.basil"}
        if idempotency:
            headers["Idempotency-Key"] = idempotency
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as client:
            async with client.request(method, "https://api.stripe.com/v1/" + path,
                                      data=form, headers=headers) as response:
                data = await response.json()
                if response.status >= 400:
                    logging.warning("stripe operation failed status=%s code=%s", response.status,
                                    data.get("error", {}).get("code", "unknown"))
                    raise web.HTTPBadGateway(text=json.dumps({"error": "Billing is temporarily unavailable. Please retry."}),
                                             content_type="application/json")
                return data

    def limit(self, request, scope, count=10, period=3600):
        # Caddy overwrites this header; the service only listens on loopback.
        ip = request.headers.get("X-Real-IP", request.remote or "unknown")
        digest = hmac.new(self.delivery_secret.encode(), ip.encode(), hashlib.sha256).hexdigest()
        bucket = scope + ":" + digest
        now = time.time()
        self.db.execute("DELETE FROM request_limits WHERE ts<?", (now-86400,))
        n = self.db.execute("SELECT COUNT(*) FROM request_limits WHERE bucket=? AND ts>?",
                            (bucket, now-period)).fetchone()[0]
        if n >= count:
            raise web.HTTPTooManyRequests(text=json.dumps({"error": "Please wait before trying again."}),
                                          content_type="application/json", headers={"Retry-After": "60"})
        self.db.execute("INSERT INTO request_limits VALUES (?,?)", (bucket, now))
        return digest

    def entitled(self, account):
        row = self.db.execute("SELECT status,period_end,plan FROM orders WHERE account=?", (account,)).fetchone()
        return bool(row and row[0] == "active" and (row[2] == "free" or row[1] > time.time()))

    def key_for(self, session, nonce):
        return "rs_live_" + hmac.new(self.delivery_secret.encode(), (session+":"+nonce).encode(), hashlib.sha256).hexdigest()

    async def sync_subscription(self, sub_id):
        if not re.fullmatch(r"sub_[A-Za-z0-9]+", sub_id or ""):
            raise ValueError("Invalid subscription")
        async with self.lock:
            sub = await self.stripe("GET", f"subscriptions/{sub_id}?expand[]=latest_invoice")
            if sub.get("metadata", {}).get("product") != PRODUCT:
                return
            row = self.db.execute("SELECT account,period_start FROM orders WHERE subscription=?", (sub_id,)).fetchone()
            if not row:
                return
            item = sub.get("items", {}).get("data", [{}])[0]
            start = item.get("current_period_start", sub.get("current_period_start", 0))
            end = item.get("current_period_end", sub.get("current_period_end", 0))
            invoice = sub.get("latest_invoice") or {}
            plan = sub.get("metadata", {}).get("plan")
            if plan not in PLANS or plan in {"free", "pass"}:
                return
            status = "active" if sub.get("status") == "active" and isinstance(invoice, dict) and invoice.get("status") == "paid" else "inactive"
            self.db.execute("BEGIN IMMEDIATE")
            try:
                # Only a new paid billing period resets usage; duplicate events do not.
                if status == "active" and start > row[1]:
                    self.db.execute("UPDATE accounts SET plan=?,period_start=?,used=0 WHERE id=?", (plan, start, row[0]))
                self.db.execute("UPDATE orders SET status=?,period_start=?,period_end=? WHERE subscription=?",
                                (status, max(start, row[1]), end, sub_id))
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    async def fulfill(self, session_id):
        if not re.fullmatch(r"cs_(test|live)_[A-Za-z0-9]+", session_id or ""):
            raise ValueError("Invalid checkout session")
        session = await self.stripe("GET", "checkout/sessions/" + session_id)
        meta = session.get("metadata", {})
        if meta.get("product") != PRODUCT or meta.get("plan") not in PLANS:
            raise ValueError("This checkout belongs to another product")
        if session.get("status") != "complete" or session.get("payment_status") not in {"paid", "no_payment_required"}:
            return None
        plan = meta["plan"]
        if plan == "pass" and (session.get("mode") != "payment" or session.get("payment_status") != "paid"
                               or session.get("currency") != "usd" or session.get("amount_total") != PLANS["pass"]["price"]*100):
            raise ValueError("A paid project pass is required")
        if plan not in {"free", "pass"} and (session.get("mode") != "subscription" or not session.get("subscription")):
            raise ValueError("Subscription is required")
        if not meta.get("nonce"):
            raise ValueError("Checkout has no access credential")
        email = (session.get("customer_details", {}).get("email") or "").strip().lower()
        if not email:
            raise ValueError("Checkout email is missing")
        async with self.lock:
            row = self.db.execute("SELECT account FROM orders WHERE session=?", (session_id,)).fetchone()
            if not row:
                if plan == "free" and self.db.execute("SELECT 1 FROM orders WHERE plan='free' AND (email=? OR free_ip=?)", (email, meta.get("ip"))).fetchone():
                    raise ValueError("A free account already exists. Use its saved key or contact support.")
                self.db.execute("BEGIN IMMEDIATE")
                try:
                    account = self.billing.create_account(email, plan)
                    self.db.execute("INSERT INTO orders(session,account,subscription,customer,nonce,plan,status,email,created,free_ip) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (session_id, account, session.get("subscription"), session.get("customer"), meta["nonce"], plan,
                         "active" if plan in {"free", "pass"} else "pending", email, time.time(), meta.get("ip") if plan == "free" else None))
                    if plan == "pass":
                        db_end = time.time() + 30*86400
                        self.db.execute("UPDATE orders SET period_end=? WHERE session=?", (db_end, session_id))
                    # nonce is already a SHA256 digest, the browser secret is never stored.
                    key = self.key_for(session_id, meta["nonce"])
                    self.db.execute("INSERT INTO keys(hash,account,label) VALUES (?,?,?)", (_hash(key), account, "Checkout"))
                    from .hosted_features import event
                    event(self, "activated", plan)
                    self.db.execute("COMMIT")
                except BaseException:
                    self.db.execute("ROLLBACK")
                    raise
        if session.get("subscription"):
            await self.sync_subscription(session["subscription"])
        return session

    def install(self, app):
        @web.middleware
        async def errors(request, handler):
            try:
                gateway = os.environ.get("SCRAPER_GATEWAY_SECRET", "")
                if gateway and not request.path.startswith("/v1/") and request.path not in {"/api/status", "/api/stripe/webhook"} and not hmac.compare_digest(request.headers.get("X-Scraper-Gateway", ""), gateway):
                    raise web.HTTPForbidden()
                if request.method == "POST" and request.path != "/api/stripe/webhook" and request.headers.get("Origin") not in ORIGINS | {self.site}:
                    if not request.path.startswith("/v1/") and request.path != "/api/portal":
                        raise web.HTTPForbidden(text='{"error":"Open this action from the website."}', content_type="application/json")
                response = await handler(request)
            except (ValueError, KeyError, TypeError):
                response = web.json_response({"error": "This request could not be completed. Check your details or contact support."}, status=400)
            except web.HTTPException as exc:
                response = exc
            except (TimeoutError, aiohttp.ClientError):
                response = web.json_response({"error": "The service timed out. Please retry; failed scrapes are not charged."}, status=504)
            except Exception:
                logging.exception("request failed path=%s", request.path)
                response = web.json_response({"error": "Service temporarily unavailable. Please retry."}, status=503)
            response.headers.update({"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"})
            return response
        app.middlewares.insert(0, errors)

        async def health(request):
            self.db.execute("SELECT 1").fetchone()
            return web.json_response({"ok": True, "checkout": bool(self.stripe_key and self.webhook_secret), "version": "0.10.0"})

        async def checkout(request):
            ip = self.limit(request, "checkout", 12)
            if not self.stripe_key or not self.webhook_secret:
                raise web.HTTPServiceUnavailable()
            body = await request.json()
            plan = body.get("plan")
            if plan not in PLANS:
                raise ValueError("Unknown plan")
            if plan == "free":
                return web.json_response({"error": "Start your free trial at /trial.html.", "url": self.site + "/trial.html"}, status=409)
            nonce = secrets.token_urlsafe(32)
            digest = _hash(nonce)
            form = {"mode": "payment" if plan == "pass" else "subscription",
                    "line_items[0][quantity]": "1", "line_items[0][price_data][currency]": "usd",
                    "line_items[0][price_data][unit_amount]": str(PLANS[plan]["price"]*100),
                    "line_items[0][price_data][product_data][name]": f"Apex Flow 3D Scraper {plan.title()}",
                    "line_items[0][price_data][product_data][description]": f"{PLANS[plan]['credits']:,} credits. Plain pages: 1 credit; rendered pages: 3.",
                    "success_url": self.site + "/thanks.html?session_id={CHECKOUT_SESSION_ID}",
                    "cancel_url": self.site + "/#pricing", "metadata[product]": PRODUCT,
                    "metadata[plan]": plan, "metadata[nonce]": digest, "metadata[ip]": ip,
                    "consent_collection[terms_of_service]": "required",
                    "custom_text[terms_of_service_acceptance][message]": "I agree to the [Terms](" + self.site + "/terms.html) and [Privacy Policy](" + self.site + "/privacy.html)."}
            if plan != "pass":
                form.update({"line_items[0][price_data][recurring][interval]": "month",
                             "subscription_data[metadata][product]": PRODUCT, "subscription_data[metadata][plan]": plan})
            else:
                form["customer_creation"] = "always"
                form["line_items[0][price_data][product_data][name]"] = "Apex Flow 3D Scraper Project Pass"
                form["line_items[0][price_data][product_data][description]"] = "35,000 credits valid for 30 days from activation. One-time purchase."
            data = await self.stripe("POST", "checkout/sessions", form, "webscraper-" + digest)
            from .hosted_features import event
            event(self, "checkout_created", plan)
            response = web.json_response({"url": data["url"]})
            response.set_cookie("scraper_claim_" + data["id"], nonce, secure=True, httponly=True, samesite="Lax", max_age=2592000, path="/")
            return response

        async def claim(request):
            self.limit(request, "claim", 60)
            body = await request.json()
            session_id = body.get("session_id", "")
            if isinstance(session_id, str) and trials.SESSION.fullmatch(session_id):
                access = trials.owned_trial(self, request, session_id)
                if not access:
                    return web.json_response({"error": "Open your trial in the browser where you started it."}, status=403)
                return web.json_response(access)
            nonce = request.cookies.get("scraper_claim_" + session_id, "")
            if not nonce:
                return web.json_response({"error": "Return using the browser where you started checkout. Contact support if you need help."}, status=403)
            # Verify possession before provisioning or releasing any key.
            if not re.fullmatch(r"cs_(test|live)_[A-Za-z0-9]+", session_id):
                raise ValueError("Invalid session")
            session = await self.stripe("GET", "checkout/sessions/" + session_id)
            if not hmac.compare_digest(session.get("metadata", {}).get("nonce", ""), _hash(nonce)):
                raise web.HTTPForbidden()
            session = await self.fulfill(session_id)
            if session is None:
                return web.json_response({"pending": True}, status=202)
            row = self.db.execute("SELECT account,plan FROM orders WHERE session=?", (session_id,)).fetchone()
            if not self.entitled(row[0]):
                return web.json_response({"pending": True}, status=202)
            return web.json_response({"key": self.key_for(session_id, _hash(nonce)), "plan": row[1],
                                      "credits_left": self.billing.remaining(row[0]), "api_base": self.site + "/v1"})

        async def webhook(request):
            raw = await request.read()
            parts = request.headers.get("Stripe-Signature", "").split(",")
            values = [p.split("=", 1) for p in parts if "=" in p]
            timestamp = next((v for k, v in values if k == "t"), "0")
            if not timestamp.isdigit() or abs(time.time()-int(timestamp)) > 300 or not self.webhook_secret:
                raise web.HTTPBadRequest()
            expected = hmac.new(self.webhook_secret.encode(), timestamp.encode()+b"."+raw, hashlib.sha256).hexdigest()
            if not any(k == "v1" and hmac.compare_digest(v, expected) for k, v in values):
                raise web.HTTPBadRequest()
            event = json.loads(raw)
            obj = event.get("data", {}).get("object", {})
            kind = event.get("type", "")
            if kind in {"checkout.session.completed", "checkout.session.async_payment_succeeded"} and obj.get("metadata", {}).get("product") == PRODUCT:
                await self.fulfill(obj["id"])
            elif kind.startswith("customer.subscription.") and obj.get("metadata", {}).get("product") == PRODUCT:
                await self.sync_subscription(obj["id"])
            elif kind.startswith("invoice."):
                sub = obj.get("subscription") or obj.get("parent", {}).get("subscription_details", {}).get("subscription")
                if sub and self.db.execute("SELECT 1 FROM orders WHERE subscription=?", (sub,)).fetchone():
                    await self.sync_subscription(sub)
            return web.json_response({"received": True})

        async def account(request):
            self.limit(request, "account", 120)
            key = request.headers.get("Authorization", "").removeprefix("Bearer ")
            acct = self.billing.account_for(key)
            if not acct:
                raise web.HTTPUnauthorized()
            row = self.db.execute("SELECT plan,status,period_end,customer,subscription FROM orders WHERE account=?", (acct,)).fetchone()
            if not row:
                raise web.HTTPNotFound()
            if request.path == "/api/portal":
                if row[0] in {"free", "pass"}:
                    return web.json_response({"url": self.site + "/#pricing"})
                form = {"customer": row[3], "return_url": self.site + "/dashboard.html"}
                if os.environ.get("STRIPE_PORTAL_CONFIG"):
                    form["configuration"] = os.environ["STRIPE_PORTAL_CONFIG"]
                data = await self.stripe("POST", "billing_portal/sessions", form)
                return web.json_response({"url": data["url"]})
            return web.json_response({"plan": row[0], "active": self.entitled(acct), "period_end": row[2],
                                      "credits_left": self.billing.remaining(acct), "credits_total": PLANS[row[0]]["credits"]})

        app.router.add_get("/api/status", health)
        app.router.add_post("/api/checkout", checkout)
        app.router.add_post("/api/claim", claim)
        app.router.add_post("/api/stripe/webhook", webhook)
        app.router.add_get("/api/account", account)
        app.router.add_post("/api/portal", account)
        trials.install(self, app)
        from .hosted_features import install
        return install(self, app)


def main():
    from .api import make_app
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    data = Path(os.environ["RAINBO_SCRAPE_DATA"])
    billing = Billing(data / "billing.db")
    hosted = Hosted(billing, os.environ["STRIPE_SECRET_KEY"], os.environ["STRIPE_WEBHOOK_SECRET"],
                    os.environ["DELIVERY_SECRET"])
    web.run_app(make_app(billing, hosted=hosted), host="127.0.0.1", port=8850, access_log=None)


if __name__ == "__main__":
    main()
