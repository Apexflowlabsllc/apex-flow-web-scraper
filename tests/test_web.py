import asyncio
import pytest
from aiohttp import web, ClientSession
from rainbo_scrape.settings import Settings
from rainbo_scrape.service import Service
from rainbo_scrape.web import app
from rainbo_scrape.engine import validate_request

@pytest.mark.asyncio
async def test_private_web_boundary_and_exports(tmp_path):
    service=Service(Settings(data_dir=tmp_path))
    runner=web.AppRunner(app(service));await runner.setup()
    site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
    base='http://127.0.0.1:'+str(site._server.sockets[0].getsockname()[1])
    try:
        async with ClientSession() as client:
            async with client.get(base+'/api/health') as r:
                assert r.status==200 and (await r.json())['database']=='ready'
            async with client.post(base+'/api/jobs',json={'urls':['http://127.0.0.1']}) as r:
                assert r.status==400
            async with client.get(base+'/api/health',headers={'Host':'evil.example'}) as r:
                assert r.status==403
            async with client.post(base+'/api/jobs',json={},headers={'Origin':'https://evil.example'}) as r:
                assert r.status==403
            async with client.post(base+'/api/jobs',data='x') as r:
                assert r.status==415
            job=service.store.create(validate_request({'urls':['https://example.com'],'max_pages':1},service.settings))
            service.store.save_page(job['id'],'https://example.com/',result={'title':'=DANGEROUS()','text':'Hello','data':{},'source_sha256':'abc'})
            async with client.get(base+'/api/jobs/'+job['id']+'/export?format=csv') as r:
                assert r.status==200 and "'=DANGEROUS()" in await r.text()
                assert r.headers['X-Frame-Options']=='DENY'
            async with client.get(base+'/api/jobs/'+job['id']+'/export') as r:
                assert (await r.json())[0]['result']['text']=='Hello'
    finally:await runner.cleanup()

def test_signed_url_is_preserved(tmp_path):
    url='https://example.com/a?key=a%20b&x=%2F&x=1&utm_source=required'
    assert validate_request({'urls':[url]},Settings(data_dir=tmp_path))['urls']==[url]
