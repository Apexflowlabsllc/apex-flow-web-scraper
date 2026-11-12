import asyncio
import copy
import hashlib
import hmac
import json
import time
import pytest
from aiohttp.test_utils import TestClient, TestServer
from rainbo_scrape.api import make_app
from rainbo_scrape.billing import Billing, _hash
from rainbo_scrape.hosted import Hosted, SITE, PRODUCT


@pytest.fixture
async def hosted(tmp_path, monkeypatch):
    monkeypatch.delenv("SCRAPER_GATEWAY_SECRET", raising=False)
    billing = Billing(tmp_path / "billing.db")
    h = Hosted(billing, "fixture", "fixture-webhook", "fixture-delivery")
    start = int(time.time())-10
    nonce = "browser-fixture-nonce"
    session = {"id":"cs_test_Example123", "status":"complete", "payment_status":"paid", "mode":"subscription",
               "metadata":{"product":PRODUCT,"plan":"starter","nonce":_hash(nonce),"ip":"fixture-ip"},
               "customer_details":{"email":"fixture@example.com"},"customer":"cus_Example123","subscription":"sub_Example123"}
    sub = {"id":"sub_Example123","metadata":{"product":PRODUCT,"plan":"starter"},"status":"active",
           "latest_invoice":{"status":"paid"},"items":{"data":[{"current_period_start":start,"current_period_end":start+2592000}]}}
    calls=[]
    async def stripe(method,path,form=None,idempotency=None):
        calls.append((method,path,form))
        if path.startswith("subscriptions/"): return copy.deepcopy(sub)
        if method == "POST" and path == "checkout/sessions": return {"id":session["id"],"url":"https://checkout.stripe.com/c/pay/fixture"}
        if path.startswith("checkout/sessions/"): return copy.deepcopy(session)
        if path == "billing_portal/sessions": return {"url":"https://billing.stripe.com/p/session/fixture"}
        raise AssertionError(path)
    h.stripe = stripe
    async def scrape(url, **kwargs):
        if url.endswith("failure"): raise ValueError("fixture failed")
        await asyncio.sleep(0.01)
        return {"markdown":"# Fixture", "rendered":False}
    client=TestClient(TestServer(make_app(billing,scrape,hosted=h)))
    await client.start_server()
    yield h,client,session,sub,nonce,calls
    await client.close()
    billing.db.close()


async def test_delivery_is_idempotent_and_duplicate_events_do_not_refill(hosted):
    h,_,session,_,nonce,_=hosted
    await h.fulfill(session["id"])
    key=h.key_for(session["id"],_hash(nonce))
    account=h.billing.account_for(key)
    assert account and h.entitled(account)
    h.billing.charge(account,"page",30)
    await h.fulfill(session["id"])
    assert h.billing.remaining(account)==24970
    assert h.db.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]==1


async def test_unpaid_checkout_does_not_create_access(hosted):
    h,_,session,_,_,_=hosted
    session["payment_status"]="unpaid"
    assert await h.fulfill(session["id"]) is None
    assert h.db.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]==0


async def test_cross_business_checkout_rejected(hosted):
    h,_,session,_,_,_=hosted
    session["metadata"]["product"]="another-business"
    with pytest.raises(ValueError): await h.fulfill(session["id"])


async def test_claim_requires_matching_browser_secret(hosted):
    h,c,s,_,nonce,_=hosted
    headers={"Origin":SITE,"Cookie":"scraper_claim_"+s["id"]+"=incorrect"}
    assert (await c.post("/api/claim",json={"session_id":s["id"]},headers=headers)).status==403
    assert h.db.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]==0
    headers["Cookie"]="scraper_claim_"+s["id"]+"="+nonce
    r=await c.post("/api/claim",json={"session_id":s["id"]},headers=headers)
    assert r.status==200
    assert h.billing.account_for((await r.json())["key"])


async def test_renewal_refills_once_and_cancellation_revokes_entitlement(hosted):
    h,_,s,sub,nonce,_=hosted
    await h.fulfill(s["id"])
    a=h.billing.account_for(h.key_for(s["id"],_hash(nonce)))
    h.billing.charge(a,"page",10)
    sub["items"]["data"][0]["current_period_start"]+=2592000
    sub["items"]["data"][0]["current_period_end"]+=2592000
    await h.sync_subscription(sub["id"])
    assert h.billing.remaining(a)==25000
    h.billing.charge(a,"page",5)
    await h.sync_subscription(sub["id"])
    assert h.billing.remaining(a)==24995
    sub["status"]="canceled"
    await h.sync_subscription(sub["id"])
    assert not h.entitled(a)


async def test_failed_invoice_does_not_grant_new_period(hosted):
    h,_,s,sub,nonce,_=hosted
    await h.fulfill(s["id"])
    a=h.billing.account_for(h.key_for(s["id"],_hash(nonce)))
    h.billing.charge(a,"page",10)
    sub["latest_invoice"]["status"]="open"
    sub["status"]="past_due"
    await h.sync_subscription(sub["id"])
    assert not h.entitled(a) and h.billing.remaining(a)==24990


async def test_webhook_signature_rejects_forgery_and_accepts_signed_event(hosted):
    h,c,s,_,_,_=hosted
    raw=json.dumps({"type":"checkout.session.completed","data":{"object":s}}).encode()
    assert (await c.post("/api/stripe/webhook",data=raw)).status==400
    ts=str(int(time.time()))
    signature=hmac.new(h.webhook_secret.encode(),ts.encode()+b"."+raw,hashlib.sha256).hexdigest()
    headers={"Stripe-Signature":f"t={ts},v1={signature}"}
    assert (await c.post("/api/stripe/webhook",data=raw,headers=headers)).status==200
    assert (await c.post("/api/stripe/webhook",data=raw+b" ",headers=headers)).status==400


async def test_failed_scrape_refunds_and_concurrent_requests_cannot_overspend(hosted):
    h,c,s,_,nonce,_=hosted
    await h.fulfill(s["id"])
    key=h.key_for(s["id"],_hash(nonce)); a=h.billing.account_for(key)
    headers={"Authorization":"Bearer "+key}
    r=await c.post("/v1/scrape",json={"url":"https://example.com/failure","render":"never"},headers=headers)
    assert r.status==400 and h.billing.remaining(a)==25000
    h.billing.charge(a,"page",24999)
    responses=await asyncio.gather(*[c.post("/v1/scrape",json={"url":"https://example.com","render":"never"},headers=headers) for _ in range(2)])
    assert sorted(r.status for r in responses)==[200,402]
    assert h.billing.remaining(a)==0


async def test_checkout_has_recurring_price_and_browser_cookie(hosted):
    _,c,_,_,_,calls=hosted
    r=await c.post("/api/checkout",json={"plan":"growth"},headers={"Origin":SITE})
    assert r.status==200 and "HttpOnly" in r.headers["Set-Cookie"] and "Secure" in r.headers["Set-Cookie"]
    form=calls[-1][2]
    assert form["line_items[0][price_data][unit_amount]"]=="7900"
    assert form["subscription_data[metadata][product]"]==PRODUCT
    assert "{CHECKOUT_SESSION_ID}" in form["success_url"]


async def test_untrusted_origin_is_rejected(hosted):
    _,c,*_=hosted
    assert (await c.post("/api/checkout",json={"plan":"starter"},headers={"Origin":"https://untrusted.example"})).status==403


async def test_free_trial_is_once_per_email_and_ip(hosted):
    h,_,s,_,_,_=hosted
    s.update(mode="payment", subscription=None, payment_status="no_payment_required")
    s["metadata"]["plan"]="free"
    await h.fulfill(s["id"])
    s["id"]="cs_test_Another123"
    with pytest.raises(ValueError): await h.fulfill(s["id"])
    assert h.db.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]==1


async def test_saved_search_is_isolated_between_accounts(hosted):
    h,c,s,_,nonce,_=hosted
    await h.fulfill(s["id"])
    key=h.key_for(s["id"],_hash(nonce)); account=h.billing.account_for(key)
    other=h.billing.create_account('other@example.com')
    h.save_page(account,{"url":"https://example.com/mine","text":"purple telescope","title":"Mine"})
    h.save_page(other,{"url":"https://example.com/private","text":"purple telescope","title":"Other"})
    r=await c.post('/v1/search',json={"query":"purple"},headers={"Authorization":"Bearer "+key})
    assert r.status==200 and [x['title'] for x in (await r.json())['results']]==['Mine']


async def test_batch_reports_individual_failures_and_bills_only_success(hosted):
    h,c,s,_,nonce,_=hosted
    await h.fulfill(s["id"])
    key=h.key_for(s["id"],_hash(nonce)); account=h.billing.account_for(key)
    r=await c.post('/v1/batch',json={"urls":["https://example.com","https://example.com/failure"],"render":"never"},headers={"Authorization":"Bearer "+key})
    data=await r.json()
    assert r.status==200 and [x['status'] for x in data['results']]==[200,400]
    assert h.billing.remaining(account)==24999


async def test_map_rejects_private_addresses_before_charge(hosted):
    h,c,s,_,nonce,_=hosted
    await h.fulfill(s["id"])
    key=h.key_for(s["id"],_hash(nonce)); account=h.billing.account_for(key)
    r=await c.post('/v1/map',json={"url":"http://127.0.0.1"},headers={"Authorization":"Bearer "+key})
    assert r.status==400 and h.billing.remaining(account)==25000


async def test_attempt_budget_blocks_repeated_failures_without_spending_credits(hosted):
    h,c,s,_,nonce,_=hosted
    await h.fulfill(s["id"])
    key=h.key_for(s["id"],_hash(nonce)); account=h.billing.account_for(key)
    period=h.db.execute('SELECT period_start FROM accounts WHERE id=?',(account,)).fetchone()[0]
    h.db.execute('INSERT INTO attempt_counts VALUES(?,?,?)',(account,period,50000))
    r=await c.post('/v1/scrape',json={"url":"https://example.com","render":"never"},headers={"Authorization":"Bearer "+key})
    assert r.status==429 and h.billing.remaining(account)==25000


async def test_batch_keeps_completed_results_when_attempt_limit_is_hit(hosted):
    h,c,s,_,nonce,_=hosted
    await h.fulfill(s["id"])
    key=h.key_for(s["id"],_hash(nonce)); account=h.billing.account_for(key)
    period=h.db.execute('SELECT period_start FROM accounts WHERE id=?',(account,)).fetchone()[0]
    h.db.execute('INSERT INTO attempt_counts VALUES(?,?,?)',(account,period,49999))
    r=await c.post('/v1/batch',json={"urls":["https://example.com","https://example.org"],"render":"never"},headers={"Authorization":"Bearer "+key})
    d=await r.json()
    assert r.status==200 and [x['status'] for x in d['results']]==[200,429]
    assert h.billing.remaining(account)==24999


async def test_free_search_and_usage_share_api_rate_limit_without_charging(hosted):
    h,c,s,_,nonce,_=hosted
    await h.fulfill(s["id"])
    key=h.key_for(s["id"],_hash(nonce)); account=h.billing.account_for(key)
    headers={"Authorization":"Bearer "+key}
    for _ in range(60):
        response=await c.post('/v1/search',json={"query":"example"},headers=headers)
        assert response.status==200
    response=await c.get('/v1/usage',headers=headers)
    assert response.status==429 and response.headers['Retry-After']=='60'
    assert h.billing.remaining(account)==25000

async def test_paid_scrape_burst_rejects_seventh_without_charging(hosted):
    h, _, session, _, nonce, _ = hosted
    await h.fulfill(session["id"])
    key = h.key_for(session["id"], _hash(nonce))
    headers = {"Authorization": "Bearer " + key}
    body = {"url": "https://example.com", "render": "auto"}
    full, release = asyncio.Event(), asyncio.Event()
    started = 0
    async def held_scrape(url, **options):
        nonlocal started
        started += 1
        if started == 6:
            full.set()
        await release.wait()
        return {"markdown": "fixture", "rendered": False}
    async with TestClient(TestServer(make_app(h.billing, held_scrape, hosted=h))) as client:
        pending = asyncio.gather(*(client.post("/v1/scrape", headers=headers, json=body) for _ in range(6)))
        try:
            await asyncio.wait_for(full.wait(), 5)
            response = await client.post("/v1/scrape", headers=headers, json=body)
            assert response.status == 429 and response.headers.get("Retry-After")
        finally:
            release.set()
            responses = await pending
        assert all(response.status == 200 for response in responses)
    assert h.billing.remaining(h.billing.account_for(key)) == 24994
