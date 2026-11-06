import pytest
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
    with pytest.raises(ValueError):
        cost_of("bogus")
    with pytest.raises(ValueError):
        cost_of("page", -1)
