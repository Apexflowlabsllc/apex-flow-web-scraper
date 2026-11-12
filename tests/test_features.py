import gzip
import io
import json
import time
import zipfile
import pytest
from aiohttp import web
from bs4 import BeautifulSoup
from rainbo_scrape.client import ascrape
from rainbo_scrape.discovery import parse_sitemap, site_map
from rainbo_scrape.docx import docx_to_markdown
from rainbo_scrape.engine import run_job, validate_request
from rainbo_scrape.extract import extract
from rainbo_scrape.llm import extract_json, validate_extract_spec
from rainbo_scrape.markdown import html_to_markdown, pick_main, strip_chrome
from rainbo_scrape.network import Fetcher
from rainbo_scrape.policy import ScrapeError
from rainbo_scrape.settings import Settings
from rainbo_scrape.store import Store


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path, host_delay=0, allow_test_loopback=True)


def md(html, url="https://example.com/"):
    soup = BeautifulSoup(html, "lxml")
    strip_chrome(soup)
    return html_to_markdown(pick_main(soup), url, soup=soup)


# ---------- markdown ----------

def test_heading_wrapped_in_link_stays_a_heading():
    out = md('<body><main><a href="/story"><h2>Big news</h2><p>Summary text.</p></a></main></body>')
    assert "## [Big news](https://example.com/story)" in out


def test_gfm_table_and_code_language():
    out = md('<main><table><tr><th>Name</th><th>Age</th></tr><tr><td>Ann</td><td>31</td></tr></table>'
             '<pre><code class="language-python">def f():\n    return 1\n</code></pre></main>')
    assert "| Name | Age |" in out and "| --- | --- |" in out and "| Ann | 31 |" in out
    assert "```python\ndef f():\n    return 1\n```" in out


def test_chrome_is_removed_but_page_title_header_kept():
    out = md('<body><div class="cookie-banner">Accept all cookies</div><nav>Home About</nav>'
             '<header><h1>Real Title</h1></header><main><p>Body text.</p></main><footer>Copyright</footer></body>')
    assert "Real Title" in out and "Body text." in out
    assert "cookies" not in out and "Home About" not in out and "Copyright" not in out


def test_many_articles_listing_uses_whole_page_not_one_card():
    cards = "".join(f"<article><h3>Item {i}</h3><p>${i}</p></article>" for i in range(5))
    out = md(f"<body><div>{cards}</div></body>")
    assert all(f"Item {i}" in out for i in range(5))


def test_nested_lists_and_links_are_absolute():
    out = md('<main><ul><li>One<ul><li>Inner</li></ul></li><li><a href="/x">Two</a></li></ul></main>')
    assert "- One" in out and "  - Inner" in out and "[Two](https://example.com/x)" in out


def test_hidden_elements_are_dropped():
    out = md('<main><p>Visible</p><p style="display: none">Secret</p><div hidden>Also secret</div></main>')
    assert "Visible" in out and "Secret" not in out


def test_extract_reports_needs_render_for_js_shell():
    shell = b'<html><body><div id="root"></div><script src="a.js"></script><script src="b.js"></script></body></html>'
    assert extract(shell, "https://example.com/")["quality"]["needs_render"] is True
    article = ("<html><body><main><p>" + "Plain static article text. " * 40 + "</p></main></body></html>").encode()
    assert extract(article, "https://example.com/")["quality"]["needs_render"] is False


# ---------- network ----------

@pytest.fixture
async def site():
    counts = {}

    async def handler(request):
        path = request.path
        counts[path] = counts.get(path, 0) + 1
        if path == "/robots.txt":
            return web.Response(text="User-agent: *\nCrawl-delay: 2\nSitemap: http://127.0.0.1:%d/sitemap_index.xml\n"
                                % request.url.port)
        if path == "/bigheader":
            return web.Response(text="<html><main><p>" + "ok " * 40 + "</p></main></html>", content_type="text/html",
                                headers={"X-Policy": "a" * 12000})
        if path == "/sitemap_index.xml":
            return web.Response(text='<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                                     '<sitemap><loc>/s1.xml</loc></sitemap><sitemap><loc>/s2.xml.gz</loc></sitemap>'
                                     '</sitemapindex>', content_type="application/xml")
        if path == "/s1.xml":
            return web.Response(text='<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                                     '<url><loc>/a</loc><lastmod>2026-01-01</lastmod></url></urlset>', content_type="application/xml")
        if path == "/s2.xml.gz":
            return web.Response(body=gzip.compress(b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                                                    b'<url><loc>/b</loc></url><url><loc>/c</loc></url></urlset>'),
                                content_type="application/gzip")
        if path == "/changing":
            return web.Response(text="<html><main><h1>Price</h1><p>" + ("Now %s. " % request.app["version"]) * 12 +
                                     "</p></main></html>", content_type="text/html")
        return web.Response(text="<html><title>t</title><main><h1>Hi</h1><p>" + "Hello world. " * 20 + "</p></main></html>",
                            content_type="text/html")
    application = web.Application()
    application["version"] = "1"
    application.router.add_route("GET", "/{path:.*}", handler)
    runner = web.AppRunner(application)
    await runner.setup()
    server = web.TCPSite(runner, "127.0.0.1", 0)
    await server.start()
    port = server._server.sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}", counts, application
    await runner.cleanup()


async def test_large_response_headers_are_accepted(site, settings):
    base, _, _ = site
    async with Fetcher(settings, None) as fetcher:
        response = await fetcher.get(base + "/bigheader", robots=False)
    assert response["status"] == 200


async def test_crawl_delay_does_not_hold_back_the_first_page(site, settings):
    base, _, _ = site
    async with Fetcher(settings, None) as fetcher:
        started = time.monotonic()
        await fetcher.get(base + "/page")
        assert time.monotonic() - started < 1.5  # robots says Crawl-delay: 2
        started = time.monotonic()
        await fetcher.get(base + "/page2")
        assert time.monotonic() - started >= 1.8  # but the delay still separates page fetches


async def test_max_age_zero_forces_a_fresh_fetch(site, settings):
    base, counts, _ = site
    store = Store(settings)
    async with Fetcher(settings, store) as fetcher:
        await fetcher.get(base + "/cached", robots=False)
        await fetcher.get(base + "/cached", robots=False)
        assert counts["/cached"] == 1
        await fetcher.get(base + "/cached", robots=False, max_age=0)
        assert counts["/cached"] == 2
        again = await fetcher.get(base + "/cached", robots=False, max_age=60000)
        assert again["cache"] == "hit" and counts["/cached"] == 2


async def test_map_reads_index_gzip_children_and_links(site, settings):
    base, _, _ = site
    async with Fetcher(settings, Store(settings)) as fetcher:
        found = await site_map(fetcher, base + "/", limit=100)
    urls = {u["url"] for u in found["urls"]}
    assert {base + "/a", base + "/b", base + "/c"} <= urls
    assert found["sitemaps_read"] >= 3 and not found["truncated"]


def test_parse_sitemap_index_vs_urlset():
    children, items = parse_sitemap(b'<sitemapindex xmlns="x"><sitemap><loc>https://a/s.xml</loc></sitemap></sitemapindex>')
    assert children == ["https://a/s.xml"] and items == []
    children, items = parse_sitemap(b'<urlset xmlns="x"><url><loc>https://a/p</loc><lastmod>d</lastmod></url></urlset>')
    assert items == [("https://a/p", "d")]


async def test_change_tracking_new_same_changed(site, settings):
    base, _, application = site
    store = Store(settings)
    statuses = []
    for version in ("1", "1", "2"):
        application["version"] = version
        options = validate_request({"urls": [base + "/changing"], "max_pages": 1, "max_depth": 0, "render": "never",
                                    "cache": False, "change_tracking": True}, settings)
        ident = store.create(options)["id"]
        store.claim_job()
        await run_job(store, ident, options)
        statuses.append(store.results(ident)[0]["result"]["change"])
    assert [s["status"] for s in statuses] == ["new", "same", "changed"]
    assert "Now 2" in statuses[2]["diff"]


async def test_sdk_one_liner_returns_attribute_access(site, settings):
    base, _, _ = site
    page = await ascrape(base + "/page", render="never", settings=settings)
    assert page.title == "t" and "Hello world." in page.markdown and page.hosted_scraping_calls == 0


# ---------- DOCX ----------

def make_docx():
    ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    body = (f'<w:document {ns}><w:body>'
            '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Quarterly report</w:t></w:r></w:p>'
            '<w:p><w:r><w:t>Revenue grew.</w:t></w:r></w:p>'
            '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Region</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Sales</w:t></w:r></w:p></w:tc></w:tr>'
            '<w:tr><w:tc><w:p><w:r><w:t>EU</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>42</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
            '</w:body></w:document>')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", body)
    return buffer.getvalue()


def test_docx_headings_paragraphs_tables():
    doc = docx_to_markdown(make_docx())
    assert doc["markdown"].startswith("# Quarterly report") and "| EU | 42 |" in doc["markdown"]
    assert doc["title"] == "Quarterly report" and "Revenue grew." in doc["text"]
    result = extract(make_docx(), "https://example.com/r.docx",
                     "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    assert result["kind"] == "docx"


def test_invalid_docx_is_a_clean_error():
    with pytest.raises(ScrapeError) as error:
        docx_to_markdown(b"PK\x03\x04 not really")
    assert error.value.code == "docx_invalid"


# ---------- schema extraction (mock model server) ----------

async def test_schema_extraction_validates_and_retries(monkeypatch):
    replies = ['{"name": 5}', '{"name": "Widget", "price": 19.0}']

    async def chat(request):
        assert request.headers["Authorization"] == "Bearer test-key"
        content = replies.pop(0)
        return web.json_response({"choices": [{"message": {"content": content}}]})
    application = web.Application()
    application.router.add_post("/chat", chat)
    runner = web.AppRunner(application)
    await runner.setup()
    server = web.TCPSite(runner, "127.0.0.1", 0)
    await server.start()
    port = server._server.sockets[0].getsockname()[1]
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("RAINBO_LLM_BASE_URL", f"http://127.0.0.1:{port}/chat")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    schema = {"type": "object", "properties": {"name": {"type": "string"}, "price": {"type": "number"}},
              "required": ["name"]}
    try:
        result = await extract_json("Widget costs $19", {"schema": schema})
    finally:
        await runner.cleanup()
    assert result["valid"] and result["data"] == {"name": "Widget", "price": 19.0}
    assert result["retries"] == 1 and result["provider"] == "deepseek"


async def test_extraction_without_a_key_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ScrapeError) as error:
        await extract_json("x", {"schema": {"type": "object"}})
    assert error.value.code == "no_model_key"


def test_extract_spec_rejects_refs_and_unknown_keys():
    with pytest.raises(ValueError):
        validate_extract_spec({"schema": {"$ref": "http://evil/x"}})
    with pytest.raises(ValueError):
        validate_extract_spec({"schema": {"type": "object"}, "url": "http://x"})


# ---------- CLI ----------

def test_cli_scrape_prints_parseable_job_id_first(monkeypatch, capsys):
    import rainbo_scrape.service as service_module
    from rainbo_scrape import cli

    class Fake:
        def submit(self, request):
            assert request["urls"] == ["https://example.com/"]
            return {"id": "0123456789abcdef", "state": "queued"}
    monkeypatch.setattr(service_module, "Service", Fake)
    cli.main(["scrape", "https://example.com/"])
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "job_id=0123456789abcdef"
    assert json.loads("\n".join(lines[1:]))["id"] == "0123456789abcdef"


async def test_unthrottled_requests_skip_the_host_delay(site, settings):
    base, _, _ = site
    settings.host_delay = 1.0
    async with Fetcher(settings, None) as fetcher:
        await fetcher.get(base + "/x", robots=False)
        started = time.monotonic()
        await fetcher.get(base + "/y", robots=False, throttle=False)
        assert time.monotonic() - started < 0.5


def test_follow_external_is_a_validated_boolean(settings):
    options = validate_request({"urls": ["https://example.com/"], "follow_external": True}, settings)
    assert options["follow_external"] is True
    assert validate_request({"urls": ["https://example.com/"]}, settings)["follow_external"] is False
    with pytest.raises(ValueError):
        validate_request({"urls": ["https://example.com/"], "follow_external": "yes"}, settings)

async def test_crawl_delay_shared_by_independent_fetchers(site, settings):
    base, _, _ = site
    async with Fetcher(settings, Store(settings)) as first, Fetcher(settings, Store(settings)) as second:
        await first.get(base + "/page")
        started = time.monotonic()
        await second.get(base + "/page2")
        assert time.monotonic() - started >= 1.8


async def test_waiting_browser_and_failed_launch_release_slots(settings, monkeypatch):
    import asyncio
    from rainbo_scrape import browser
    from playwright import async_api

    started = asyncio.Event()

    class UnavailableBrowser:
        async def start(self):
            started.set()
            raise RuntimeError("fixture launch failure")

    slots = asyncio.BoundedSemaphore(1)
    await slots.acquire()
    monkeypatch.setattr(browser, "brave_path", lambda: "fixture")
    monkeypatch.setattr(async_api, "async_playwright", UnavailableBrowser)
    with pytest.raises(RuntimeError, match="fixture launch failure"):
        async with Fetcher(settings, browser_slots=slots) as fetcher:
            launch = asyncio.create_task(browser.get_browser(fetcher))
            await asyncio.sleep(0)
            assert not started.is_set()
            slots.release()
            await launch
    assert started.is_set() and not slots.locked()
