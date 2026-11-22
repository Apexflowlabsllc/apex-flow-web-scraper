import pytest
import sqlite3
from rainbo_scrape.billing import Billing, QuotaExceeded, PLANS, cost_of, price_per_1000


def test_key_is_hashed_and_resolves(tmp_path):
    b = Billing(tmp_path / "b.db")
    acct = b.create_account("a@example.com")
    key = b.new_key(acct)
    assert b.account_for(key) == acct
    stored = b.db.execute("SELECT hash FROM keys").fetchone()[0]
    assert key not in stored and len(stored) == 64
    assert b.account_for("rs_live_wrong") is None and b.account_for("nope") is None


def test_revoked_key_stops_working(tmp_path):
    b = Billing(tmp_path / "b.db")
    key = b.new_key(b.create_account("a@example.com"))
    assert b.revoke(key) and b.account_for(key) is None


def test_charge_reduces_credits_and_enforces_quota(tmp_path):
    b = Billing(tmp_path / "b.db")
    acct = b.create_account("a@example.com")
    assert b.charge(acct, "page", 10) == 990
    assert b.charge(acct, "render", 2) == 984
    with pytest.raises(QuotaExceeded):
        b.charge(acct, "page", 985)
    assert b.remaining(acct) == 984  # a refused charge changes nothing


def test_reserve_records_exact_provider_credits_under_operation_kind(tmp_path):
    b = Billing(tmp_path / "b.db")
    acct = b.create_account("a@example.com")
    b.reserve(acct, 210, kind="search")
    assert b.remaining(acct) == 790
    assert b.db.execute("SELECT kind,credits FROM usage WHERE account=?", (acct,)).fetchone() == ("search", 210)


def test_idempotent_reservation_survives_retry_settlement_and_refund(tmp_path):
    b = Billing(tmp_path / "b.db")
    acct = b.create_account("a@example.com")

    assert b.reserve_idempotent(acct, "job:0", 3) == "new"
    assert b.reserve_idempotent(acct, "job:0", 3) == "reserved"
    assert b.remaining(acct) == 997
    b.settle_idempotent(acct, "job:0", 1)
    b.settle_idempotent(acct, "job:0", 1)
    assert b.remaining(acct) == 999

    assert b.reserve_idempotent(acct, "job:1", 3) == "new"
    b.refund_idempotent(acct, "job:1")
    b.refund_idempotent(acct, "job:1")
    assert b.remaining(acct) == 999
    assert b.reserve_idempotent(acct, "job:1", 3) == "reserved"
    assert b.remaining(acct) == 996
    with pytest.raises(ValueError, match="different reservation"):
        b.reserve_idempotent(acct, "job:0", 1)


def test_plan_change_resets_period(tmp_path):
    b = Billing(tmp_path / "b.db")
    acct = b.create_account("a@example.com")
    b.charge(acct, "page", 1000)
    b.set_plan(acct, "starter")
    assert b.remaining(acct) == PLANS["starter"]["credits"]


def test_prices_undercut_firecrawl_standard():
    firecrawl_per_1000 = 83 / 100  # $83 for 100k credits, from COMPETITOR-TEARDOWN-2026-11-05.md
    for plan in ("starter", "growth", "scale"):
        assert price_per_1000(plan) < firecrawl_per_1000


def test_cost_validation():
    assert cost_of("proxy_page", 2) == 16
    assert cost_of("search") == 130
    with pytest.raises(ValueError):
        cost_of("bogus")
    with pytest.raises(ValueError):
        cost_of("page", -1)


def test_key_attribution_migration_preserves_existing_usage(tmp_path):
    path = tmp_path / "old-billing.db"
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE accounts (id TEXT PRIMARY KEY, email TEXT, plan TEXT, period_start REAL, used INTEGER);
        CREATE TABLE keys (hash TEXT PRIMARY KEY, account TEXT, label TEXT, revoked INTEGER DEFAULT 0);
        CREATE TABLE usage (ts REAL, account TEXT, kind TEXT, credits INTEGER);
        INSERT INTO accounts VALUES ('acct', 'a@example.com', 'free', 1, 4);
        INSERT INTO keys VALUES ('oldhash', 'acct', 'legacy', 0);
        INSERT INTO usage VALUES (2, 'acct', 'page', 4);
    """)
    db.close()

    billing = Billing(path)
    columns = {row[1] for row in billing.db.execute("PRAGMA table_info(usage)")}
    assert "api_key_hash" in columns
    assert billing.db.execute("SELECT credits,api_key_hash FROM usage").fetchone() == (4, None)
    legacy_key = billing.db.execute("SELECT key_id,created FROM keys WHERE hash='oldhash'").fetchone()
    assert legacy_key[0].startswith("key_") and legacy_key[1] is not None
    assert billing.remaining("acct") == PLANS["free"]["credits"] - 4
