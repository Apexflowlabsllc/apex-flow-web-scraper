"""Hosted-product accounting: plans, hashed API keys, credit metering. No payment processing here.

Nothing in this module talks to a payment provider or the network. A deployed service would
call `set_plan` from a verified billing webhook and `charge` from the request path.
"""
import hashlib
import secrets
import sqlite3
import time
from pathlib import Path

# Price in USD per month, included credits. One credit = one plain (non-rendered) page.
PLANS = {
    "free": {"price": 0, "credits": 1000, "rpm": 30, "concurrency": 2, "saved_pages": 100},
    "pass": {"price": 5, "credits": 35000, "rpm": 60, "concurrency": 6, "saved_pages": 1000},
    "starter": {"price": 15, "credits": 110000, "rpm": 60, "concurrency": 6, "saved_pages": 1000},
    "growth": {"price": 79, "credits": 850000, "rpm": 120, "concurrency": 12, "saved_pages": 1000},
    "scale": {"price": 299, "credits": 3750000, "rpm": 240, "concurrency": 20, "saved_pages": 1000},
}
# Credits per request kind. Rendered pages cost our browser time; proxied pages cost real bandwidth.
COSTS = {"page": 1, "render": 3, "proxy_page": 8, "proxy_render": 10, "extract": 150, "search": 1}
PREFIX = "rs_live_"


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
            CREATE TABLE IF NOT EXISTS keys (hash TEXT PRIMARY KEY, account TEXT, label TEXT, revoked INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS usage (ts REAL, account TEXT, kind TEXT, credits INTEGER);
            CREATE INDEX IF NOT EXISTS usage_account_ts ON usage(account, ts);
        """)

    def create_account(self, email, plan="free"):
        if plan not in PLANS:
            raise ValueError("unknown plan")
        ident = secrets.token_hex(8)
        self.db.execute("INSERT INTO accounts VALUES (?,?,?,?,0)", (ident, email, plan, time.time()))
        return ident

    def new_key(self, account, label=""):
        key = PREFIX + secrets.token_urlsafe(32)
        self.db.execute("INSERT INTO keys (hash, account, label) VALUES (?,?,?)", (_hash(key), account, label))
        return key  # shown once; only the hash is stored

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
            self.db.execute("INSERT INTO usage VALUES (?,?,?,?)", (time.time(), account, kind, needed))
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
            self.db.execute("INSERT INTO usage VALUES (?,?,?,?)", (time.time(), account, "refund", -credits))
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
