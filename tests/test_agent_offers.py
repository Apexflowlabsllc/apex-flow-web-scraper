import time
import pytest
from test_hosted import hosted, SITE, _hash


async def test_direct_trial_recovery_and_network_limit(hosted):
    h,client,*_=hosted
    origin={"Origin":SITE}
    refused=await client.post("/api/trial",json={"terms":False},headers=origin)
    assert refused.status==400
    started=await client.post("/api/trial",json={"terms":True},headers=origin)
    sid=(await started.json())["session_id"]
    cookie="scraper_trial="+started.cookies["scraper_trial"].value
    owner={**origin,"Cookie":cookie}
    claim=await client.post("/api/claim",json={"session_id":sid},headers=owner)
    key=(await claim.json())["key"]
    acct=h.billing.account_for(key)
    h.billing.charge(acct,"page",1)
    again=await client.post("/api/trial",json={"terms":True},headers=owner)
    assert (await again.json())["session_id"]==sid and h.billing.remaining(acct)==999
    assert (await client.post("/api/claim",json={"session_id":sid},headers=origin)).status==403
    assert (await client.post("/api/trial",json={"terms":True},headers=origin)).status==409


async def test_pass_payment_and_duplicate_fulfillment(hosted):
    h,c,s,_,nonce,calls=hosted
    response=await c.post("/api/checkout",json={"plan":"pass"},headers={"Origin":SITE})
    form=calls[-1][2]
    assert response.status==200 and form["mode"]=="payment"
    assert form["line_items[0][price_data][unit_amount]"]=="500"
    s.update(mode="payment",subscription=None,amount_total=400,currency="usd")
    s["metadata"]["plan"]="pass"
    with pytest.raises(ValueError): await h.fulfill(s["id"])
    s["amount_total"]=500
    await h.fulfill(s["id"])
    acct=h.billing.account_for(h.key_for(s["id"],_hash(nonce)))
    h.billing.charge(acct,"page",2)
    end=h.db.execute("SELECT period_end FROM orders WHERE account=?",(acct,)).fetchone()[0]
    await h.fulfill(s["id"])
    assert h.entitled(acct) and h.billing.remaining(acct)==4998
    assert abs(end-time.time()-30*86400)<5
    assert h.db.execute("SELECT period_end FROM orders WHERE account=?",(acct,)).fetchone()[0]==end


async def test_remote_mcp_auth_tools_and_shared_metering(hosted):
    h,c,s,_,nonce,_=hosted
    await h.fulfill(s["id"])
    key=h.key_for(s["id"],_hash(nonce))
    headers={"Authorization":"Bearer "+key}
    def msg(method,params):
        return {"jsonrpc":"2.0","id":1,"method":method,"params":params}
    assert (await c.post("/v1/mcp",json=msg("tools/list",{}))).status==401
    denied=await c.post("/v1/mcp",headers={**headers,"Origin":"https://untrusted.example"},json=msg("ping",{}))
    assert denied.status==403
    for method,params in [("initialize",{}),("tools/list",{}),("tools/call",{"name":"scrape_page","arguments":{"url":"https://example.com","render":"never"}})]:
        resp=await c.post("/v1/mcp",headers=headers,json=msg(method,params))
        data=await resp.json()
        assert resp.status==200 and "result" in data
        if method=="tools/list": assert len(data["result"]["tools"])==10
        if method=="tools/call": assert "Fixture" in data["result"]["content"][0]["text"]
    assert h.billing.remaining(h.billing.account_for(key))==24999
