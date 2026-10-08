import asyncio
import gzip
import json
import socket
import sqlite3
import time
from pathlib import Path
import pytest
from aiohttp import web, ClientSession
from rainbo_scrape.settings import Settings
from rainbo_scrape.policy import ScrapeError, canonical_url, check_url, check_address, PublicResolver
from rainbo_scrape.extract import extract, validate_recipe
from rainbo_scrape.network import Fetcher
from rainbo_scrape.store import Store
from rainbo_scrape.engine import validate_request, run_job
from rainbo_scrape.service import Service


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path, host_delay=0, allow_test_loopback=True)


@pytest.mark.parametrize("proxy", [
    "http://proxy.example:3128",
    "http://operator:secret@proxy.example:3128",
])
def test_proxy_configuration_accepts_http_endpoints(tmp_path, proxy):
    configured = Settings(data_dir=tmp_path, proxy_url=proxy)
    configured.validate_proxy()
    assert configured.proxy_url == proxy


@pytest.mark.parametrize("proxy", [
    "https://proxy.example:3128",
    "socks5://proxy.example:1080",
    "http://proxy.example",
    "http://proxy.example:70000",
    "http://proxy.example:3128/path",
    "http://proxy.example:3128?route=other",
    "http://operator@proxy.example:3128",
])
def test_proxy_configuration_rejects_unsupported_or_ambiguous_urls(tmp_path, proxy):
    configured = Settings(data_dir=tmp_path, proxy_url=proxy)
    with pytest.raises(ValueError, match="RAINBO_SCRAPE_PROXY_URL"):
        configured.validate_proxy()


async def test_proxy_destination_preflight_rejects_private_dns(monkeypatch, settings):
    async def private_dns(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]

    monkeypatch.setattr(asyncio, "get_running_loop", lambda: type("Loop", (), {"getaddrinfo": private_dns})())
    settings.allow_test_loopback = False
    fetcher = Fetcher(settings)
    with pytest.raises(ScrapeError, match="public internet"):
        await fetcher._validate_public_target("https://example.com/")


async def test_proxy_destination_preflight_accepts_public_dns(monkeypatch, settings):
    async def public_dns(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    monkeypatch.setattr(asyncio, "get_running_loop", lambda: type("Loop", (), {"getaddrinfo": public_dns})())
    fetcher = Fetcher(settings)
    await fetcher._validate_public_target("https://example.com/")


@pytest.fixture
async def site():
    counts = {}
    async def handler(request):
        path = request.path
        counts[path] = counts.get(path, 0)+1
        if path == "/robots.txt":
            return web.Response(text="User-agent: *\nDisallow: /private\n")
        if path == "/redirect":
            raise web.HTTPFound("/article")
        if path == "/etag":
            if request.headers.get("If-None-Match") == '"v1"':
                return web.Response(status=304)
            return web.Response(text="cached version", headers={"ETag": '"v1"'})
        if path == "/gzip":
            return web.Response(body=gzip.compress(b"x"*20000), headers={"Content-Encoding": "gzip"})
        if path == "/retry" and counts[path] < 2:
            return web.Response(status=429, headers={"Retry-After": "0"})
        if path == "/slow":
            await asyncio.sleep(3)
        if path == "/dynamic":
            return web.Response(text='<html><body><main id="app"></main><script>setTimeout(()=>document.getElementById("app").innerHTML="<h1>Rendered correctly</h1><p>Dynamic page evidence.</p>",100)</script></body></html>', content_type="text/html")
        if path == "/private":
            return web.Response(text="must never fetch")
        return web.Response(text=f'<html><title>{path}</title><nav>Junk</nav><main><h1>Article {path}</h1><p>'+('Useful article text. '*20)+'</p><a href="/a">A</a><a href="/b">B</a><a href="/c">C</a><a href="/private">Private</a></main></html>',content_type="text/html")
    application = web.Application()
    application.router.add_route("GET", "/{path:.*}", handler)
    runner = web.AppRunner(application)
    await runner.setup()
    server = web.TCPSite(runner, "127.0.0.1", 0)
    await server.start()
    port = server._server.sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}", counts
    await runner.cleanup()


@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://127.0.0.1/", "http://[::1]/", "http://169.254.169.254/latest/", "http://10.0.0.1/", "http://user:pass@example.com/", "http://example.com:22/", "http://localhost/", "http://example.internal/"])
def test_reject_private_urls(url):
    with pytest.raises(ScrapeError):
        check_url(url)


def test_canonical_urls():
    assert canonical_url("HTTPS://Example.COM:443/a?utm_source=x&b=2#part") == "https://example.com/a?b=2"
    assert canonical_url("https://example.com/?signature=a%2Fb", False).endswith("signature=a%2Fb")


def test_recipe_and_evidence():
    html = b'<base href="https://example.com/shop/"><title>Product</title><nav>Remove this</nav><main><h1>Widget</h1><span class="price">19.00</span><table><tr><th>Size</th><td>Large</td></tr></table><a href="next">Next</a></main><script type="application/ld+json">{"@type":"Product","name":"Widget"}</script>'
    recipe = {"fields": {"name": {"selector": "h1", "required": True}, "missing": {"selector": ".missing", "required": True}}}
    r = extract(html, "https://example.com/", recipe=recipe)
    assert r["data"] == {"name": "Widget", "missing": None}
    assert r["evidence"]["name"]["matches"] == 1
    assert r["validation_errors"]
    assert "Remove this" not in r["text"]
    assert r["links"][0]["url"] == "https://example.com/shop/next"
    assert r["tables"][0][0] == ["Size", "Large"]
    assert r["structured_data"][0]["name"] == "Widget"


@pytest.mark.parametrize("recipe", [{"script": "alert(1)"}, {"fields":{"x":{"selector":"["}}}, {"schema":{"$ref":"http://evil.invalid"}}, {"fields":{"x":{"selector":"h1","all":"yes"}}}])
def test_recipe_validation(recipe):
    with pytest.raises(ValueError):
        validate_recipe(recipe)


def test_feed_sitemap_json_and_plain():
    r = extract(b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://example.com/a</loc></url></urlset>', "https://example.com/sitemap.xml", "application/xml")
    assert r["links"][0]["url"].endswith("/a")
    assert extract(b'{"a":1}', "https://example.com/", "application/json")["data"]["a"] == 1
    assert extract(b'hello', "https://example.com/", "text/plain")["text"] == "hello"


def test_xml_entity_expansion_denied():
    with pytest.raises(Exception):
        extract(b'<!DOCTYPE x [<!ENTITY a SYSTEM "file:///etc/passwd">]><rss>&a;</rss>', "https://example.com/", "application/xml")


def test_access_challenge_not_success():
    with pytest.raises(ScrapeError, match="access challenge"):
        extract(b'<html><title>Just a moment...</title><p>Verify you are human</p></html>', "https://example.com/")


async def test_fetch_robots_redirect_cache_retry_and_etag(settings, site):
    base, counts = site
    store = Store(settings)
    async with Fetcher(settings, store) as f:
        with pytest.raises(ScrapeError, match="disallows"):
            await f.get(base+"/private")
        assert "/private" not in counts
        r = await f.get(base+"/redirect")
        assert r["url"].endswith("/article")
        assert (await f.get(base+"/article"))["cache"] == "hit"
        assert (await f.get(base+"/retry"))["status"] == 200
        await f.get(base+"/etag")
        settings.cache_ttl = -1
        assert (await f.get(base+"/etag"))["cache"] == "revalidated"
        assert counts["/etag"] == 2


async def test_decoded_gzip_budget(settings, site):
    settings.max_body = 1000
    async with Fetcher(settings) as f:
        with pytest.raises(ScrapeError, match="Decoded response"):
            await f.get(site[0]+"/gzip")


async def test_atomic_crawl_budget_and_recovery(settings, site):
    store = Store(settings)
    req = validate_request({"urls":[site[0]+"/article"],"max_pages":3,"max_depth":2,"render":"never"},settings)
    ident = store.create(req)["id"]
    store.claim_job()
    await run_job(store,ident,req)
    s = store.status(ident)
    assert s["state"] == "completed"
    assert s["total"] == 3
    assert s["counts"]["done"] == 3
    assert s["bytes"] > 0
    ident2 = store.create(req)["id"]
    store.claim_job()
    store.claim_pages(ident2,1)
    store.recover()
    assert store.status(ident2)["state"] == "queued"
    assert store.status(ident2)["counts"]["queued"] == 1


async def test_cancel_inflight(settings, site):
    store = Store(settings)
    req = validate_request({"urls":[site[0]+"/slow"],"max_pages":1,"render":"never"},settings)
    ident = store.create(req)["id"]
    store.claim_job()
    task = asyncio.create_task(run_job(store,ident,req))
    await asyncio.sleep(.15)
    store.cancel(ident)
    await asyncio.wait_for(task,1.5)
    assert store.status(ident)["state"] == "cancelled"


def test_byte_reservation_and_queue_limits(settings):
    store = Store(settings)
    req = validate_request({"urls":["https://example.com"],"max_pages":1},settings)
    ident = store.create(req)["id"]
    store.charge(ident,100,150)
    with pytest.raises(ScrapeError):
        store.charge(ident,100,150)
    assert store.status(ident)["bytes"] == 100
    assert store.enqueue(ident,["https://example.com/a","https://example.com/b"],1,2) == 1
    assert store.enqueue(ident,["https://example.com/c"],1,2) == 0


@pytest.mark.parametrize("field,value", [("max_pages",0),("max_pages",True),("concurrency",100),("render","magic"),("max_depth",11),("cache","yes")])
def test_job_boundary(settings,field,value):
    with pytest.raises(ValueError):
        validate_request({"urls":["https://example.com"],field:value},settings)


async def test_rendered_brave_fixture(settings,site):
    from rainbo_scrape.engine import one_page
    result = await one_page(settings,Store(settings),site[0]+"/dynamic", "always")
    assert "Rendered correctly" in result["text"]
    assert result["rendered"] is True
    assert result["hosted_scraping_calls"] == 0


def test_literal_search(settings):
    store = Store(settings)
    req = validate_request({"urls":["https://example.com"]},settings)
    ident = store.create(req)["id"]
    store.save_page(ident,req["urls"][0],{"title":"A","text":"100% useful","content_sha256":"a"})
    assert len(store.search("100%")) == 1
    assert store.search("' OR 1=1 --") == []
