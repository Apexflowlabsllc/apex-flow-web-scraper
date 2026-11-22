"""Hosted-product accounting: plans, hashed API keys, credit metering. No payment processing here.

Nothing in this module talks to a payment provider or the network. A deployed service would
call `set_plan` from a verified billing webhook and `charge` from the request path.
"""
import hashlib
import secrets
import sqlite3
import time
from contextvars import ContextVar
from pathlib import Path

# Price in USD per month, included credits. One credit = one plain (non-rendered) page.
PLANS = {
    "free": {"price": 0, "credits": 1000, "rpm": 30, "concurrency": 2, "saved_pages": 100, "monitors": 0},
    "pass": {"price": 5, "credits": 35000, "rpm": 60, "concurrency": 6, "saved_pages": 1000, "monitors": 5},
    "starter": {"price": 15, "credits": 110000, "rpm": 60, "concurrency": 6, "saved_pages": 1000, "monitors": 10},
    "growth": {"price": 79, "credits": 850000, "rpm": 120, "concurrency": 12, "saved_pages": 1000, "monitors": 100},
    "scale": {"price": 299, "credits": 3750000, "rpm": 240, "concurrency": 20, "saved_pages": 1000, "monitors": 500},
}
# Credits per request kind. Rendered pages cost our browser time; proxied pages cost real bandwidth.
COSTS = {"page": 1, "render": 3, "proxy_page": 8, "proxy_render": 10, "extract": 150, "search": 70}
PREFIX = "rs_live_"
_usage_key_hash = ContextVar("apex_usage_key_hash", default=None)


def set_usage_key_hash(value):
    return _usage_key_hash.set(value)


def reset_usage_key_hash(token):
    _usage_key_hash.reset(token)


def public_usage_key_id(value):
    """Stable non-secret identifier for reporting usage by API key."""
    return "key_" + hashlib.sha256(("usage-id:" + value).encode()).hexdigest()[:12] if value else None


class QuotaExceeded(Exception):
    def __init__(self, needed, left):
        super().__init__(f"needs {needed} credits, {left} left this period")
        self.needed, self.left = needed, left


def price_per_1000(plan):
    p = PLANS[plan]
    return round(p["price"] / p["credits"] * 1000, 3)


def cost_of(kind, count=1):
    if kind not in COSTS or count < 0:
        raise ValueError("unknown request kind or negative count")
    return COSTS[kind] * count


def _hash(key):
    return hashlib.sha256(key.encode()).hexdigest()


class Billing:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), isolation_level=None, timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS accounts (id TEXT PRIMARY KEY, email TEXT, plan TEXT, period_start REAL, used INTEGER);
            CREATE TABLE IF NOT EXISTS keys (hash TEXT PRIMARY KEY, account TEXT, label TEXT, revoked INTEGER DEFAULT 0,
                key_id TEXT, created REAL);
            CREATE TABLE IF NOT EXISTS usage (ts REAL, account TEXT, kind TEXT, credits INTEGER);
            CREATE INDEX IF NOT EXISTS usage_account_ts ON usage(account, ts);
        """)
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(usage)")}
        if "api_key_hash" not in columns:
            self.db.execute("ALTER TABLE usage ADD COLUMN api_key_hash TEXT")
        self.db.execute("CREATE INDEX IF NOT EXISTS usage_account_key_ts ON usage(account,api_key_hash,ts)")
        key_columns = {row[1] for row in self.db.execute("PRAGMA table_info(keys)")}
        if "key_id" not in key_columns:
            self.db.execute("ALTER TABLE keys ADD COLUMN key_id TEXT")
        if "created" not in key_columns:
            self.db.execute("ALTER TABLE keys ADD COLUMN created REAL")
        for key_hash, in self.db.execute("SELECT hash FROM keys WHERE key_id IS NULL OR created IS NULL").fetchall():
            self.db.execute("UPDATE keys SET key_id=COALESCE(key_id,?),created=COALESCE(created,?) WHERE hash=?",
                             ("key_" + secrets.token_hex(8), time.time(), key_hash))
        self.db.execute("CREATE UNIQUE INDEX IF NOT EXISTS keys_account_id ON keys(account,key_id) WHERE key_id IS NOT NULL")

    def create_account(self, email, plan="free"):
        if plan not in PLANS:
            raise ValueError("unknown plan")
        ident = secrets.token_hex(8)
        self.db.execute("INSERT INTO accounts VALUES (?,?,?,?,0)", (ident, email, plan, time.time()))
        return ident

    def new_key(self, account, label=""):
        key = PREFIX + secrets.token_urlsafe(32)
        self.register_key(account, key, label)
        return key  # shown once; only the hash is stored

    def register_key(self, account, key, label=""):
        ident = "key_" + secrets.token_hex(8)
        self.db.execute("INSERT INTO keys (hash,account,label,key_id,created) VALUES (?,?,?,?,?)",
                        (_hash(key), account, label, ident, time.time()))
        return ident

    def list_keys(self, account):
        rows = self.db.execute("SELECT key_id,label,created,revoked FROM keys WHERE account=? ORDER BY created DESC,key_id",
                               (account,)).fetchall()
        return [{"id": ident, "label": label or "", "created_at": created, "revoked": bool(revoked)}
                for ident, label, created, revoked in rows]

    def revoke_key_id(self, account, ident):
        return self.db.execute("UPDATE keys SET revoked=1 WHERE account=? AND key_id=? AND revoked=0",
                               (account, ident)).rowcount == 1

    def revoke(self, key):
        return self.db.execute("UPDATE keys SET revoked=1 WHERE hash=?", (_hash(key),)).rowcount == 1

    def account_for(self, key):
        if not key.startswith(PREFIX):
            return None
        row = self.db.execute("SELECT account FROM keys WHERE hash=? AND revoked=0", (_hash(key),)).fetchone()
        return row[0] if row else None

    def set_plan(self, account, plan):
        if plan not in PLANS:
            raise ValueError("unknown plan")
        self.db.execute("UPDATE accounts SET plan=?, period_start=?, used=0 WHERE id=?", (plan, time.time(), account))

    def remaining(self, account):
        plan, used = self.db.execute("SELECT plan, used FROM accounts WHERE id=?", (account,)).fetchone()
        return PLANS[plan]["credits"] - used

    def limits(self, account):
        plan = self.db.execute("SELECT plan FROM accounts WHERE id=?", (account,)).fetchone()[0]
        return PLANS[plan]

    def charge(self, account, kind, count=1):
        needed = cost_of(kind, count)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            plan, used = self.db.execute("SELECT plan, used FROM accounts WHERE id=?", (account,)).fetchone()
            left = PLANS[plan]["credits"] - used
            if needed > left:
                raise QuotaExceeded(needed, left)
            self.db.execute("UPDATE accounts SET used=used+? WHERE id=?", (needed, account))
            self.db.execute("INSERT INTO usage(ts,account,kind,credits,api_key_hash) VALUES (?,?,?,?,?)",
                            (time.time(), account, kind, needed, _usage_key_hash.get()))
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        return left - needed

    def reserve(self, account, credits):
        """Reserve before I/O so concurrent requests cannot overspend."""
        return self.charge(account, "page", credits)

    def refund(self, account, credits):
        if credits <= 0:
            return
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.db.execute("UPDATE accounts SET used=MAX(0,used-?) WHERE id=?", (credits, account))
            self.db.execute("INSERT INTO usage(ts,account,kind,credits,api_key_hash) VALUES (?,?,?,?,?)",
                            (time.time(), account, "refund", -credits, _usage_key_hash.get()))
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
