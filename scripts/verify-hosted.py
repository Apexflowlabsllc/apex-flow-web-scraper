"""Run on the API host. Creates and then disables a clearly labeled internal QA account."""
import json
import secrets
import urllib.error
import urllib.request
from pathlib import Path
from rainbo_scrape.billing import Billing

env = dict(line.split('=', 1) for line in Path('/etc/apex-webscraper.env').read_text().splitlines() if '=' in line)
b = Billing('/var/lib/apex-webscraper/billing.db')
tag = 'internal_qa_' + secrets.token_hex(8)
account = b.create_account('internal-qa@apexflowlabs.com', 'free')
key = b.new_key(account, 'Internal release verification; revoked after run')
b.db.execute('INSERT INTO orders(session,account,plan,status,email,free_ip) VALUES (?,?,?,?,?,?)',
             (tag, account, 'free', 'active', tag+'@internal.invalid', tag))
def request(path, data=None):
    headers={'Authorization':'Bearer '+key,'X-Scraper-Gateway':env['SCRAPER_GATEWAY_SECRET'],'Content-Type':'application/json'}
    req=urllib.request.Request('https://webscraper.apexflowlabs.com'+path,headers=headers,
                              data=json.dumps(data).encode() if data is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=100) as response:
            return response.status,json.load(response)
    except urllib.error.HTTPError as e:
        return e.code,json.load(e)
try:
    for name, body in [('plain', {'url':'https://example.com','render':'never'}),
                       ('rendered', {'url':'https://example.com','render':'always'}),
                       ('private-blocked', {'url':'http://127.0.0.1','render':'never'})]:
        status,data=request('/v1/scrape',body)
        print(json.dumps({'check':name,'status':status,'credits_used':data.get('credits_used'),
                          'credits_left':b.remaining(account),'has_markdown':bool(data.get('result',{}).get('markdown')),
                          'error':data.get('error')}),flush=True)
    for name,path,body in [('map','/v1/map',{'url':'https://webscraper.apexflowlabs.com','limit':10}),
                           ('search','/v1/search',{'query':'Example'}),
                           ('batch','/v1/batch',{'urls':['https://example.com','http://127.0.0.1'],'render':'never'}),
                           ('css-extraction','/v1/scrape',{'url':'https://webscraper.apexflowlabs.com','render':'never','max_age':0,'recipe':{'fields':{'heading':{'selector':'h1','required':True}}}})]:
        status,data=request(path,body)
        print(json.dumps({'check':name,'status':status,'count':data.get('result',{}).get('count',len(data.get('results',[]))),
                          'data':data.get('result',{}).get('data'),'validation_errors':data.get('result',{}).get('validation_errors'),'credits_used':data.get('credits_used')}),flush=True)
        assert status == 200, name
        if name == 'css-extraction':
            assert data['result']['data']['heading'], 'CSS extraction did not return the page heading'
    before=b.remaining(account)
    for _ in range(23):
        status,data=request('/v1/search',{'query':'Example'})
        assert status==200, 'Search rate allowance ended too early'
    status,data=request('/v1/usage')
    assert status==429, 'Credit-free endpoints did not enforce the shared API limit'
    assert b.remaining(account)==before, 'Credit-free requests changed the balance'
    print(json.dumps({'check':'shared-api-rate-limit','status':status,'credits_unchanged':True}),flush=True)
finally:
    b.revoke(key)
    b.db.execute("UPDATE orders SET status='qa-complete' WHERE account=?",(account,))
    print(json.dumps({'qa_access_revoked':b.account_for(key) is None}),flush=True)
