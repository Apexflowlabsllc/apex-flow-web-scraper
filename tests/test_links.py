"""Link Intelligence: the link index reader, the paid graph answers, the dead-page hunt, the link finder and SEO checks."""
import asyncio
import json
import struct

import pytest
from aiohttp.test_utils import TestClient, TestServer

from rainbo_scrape import linkgraph
from rainbo_scrape.api import make_app
from rainbo_scrape.billing import Billing
from rainbo_scrape.hosted import Hosted
from rainbo_scrape.hosted_seo import analyze
from test_packages import account


def _leb(values):
    out, prev = bytearray(), 0
    for v in values:
        d, prev = v - prev, v
        while True:
            b = d & 0x7F
            d >>= 7
            out.append(b | (0x80 if d else 0))
            if not d:
                break
    return bytes(out)


def write_graph(path, ranked, edges, cap=1000):
    """ranked: domain names strongest first (all are strong sources). edges: (from, to) pairs."""
    path.mkdir()
    rank = {d: i for i, d in enumerate(ranked)}
    inn, out = {}, {}
    for f, t in edges:
        inn.setdefault(t, set()).add(rank[f])
        out.setdefault(f, set()).add(rank[t])
    rows = []
    inl, outl = bytearray(), bytearray()
    for d in ranked:
        refs = sorted(inn.get(d, ()))[:cap]
        io = len(inl) if refs else linkgraph.NONE
        inl += _leb(refs)
        outs = sorted(out.get(d, ()))
        oo = len(outl) if outs else linkgraph.NONE
        outl += _leb(outs)
        rows.append((linkgraph.key(linkgraph.flip(d)), rank[d] + 1, len(inn.get(d, ())), len(inn.get(d, ())), io, oo, len(outs)))
    rows.sort()
    (path / "keys.u64").write_bytes(b"".join(struct.pack("<Q", r[0]) for r in rows))
    (path / "rec.bin").write_bytes(b"".join(linkgraph.REC.pack(*r[1:]) for r in rows))
    (path / "inl.bin").write_bytes(bytes(inl))
    (path / "outl.bin").write_bytes(bytes(outl))
    names = [linkgraph.flip(d) for d in ranked]
    (path / "names.bin").write_bytes("\n".join(names).encode() + b"\n")
    offs, at = [0], 0
    for n in names:
        at += len(n.encode()) + 1
        offs.append(at)
    (path / "names.off").write_bytes(struct.pack(f"<{len(offs)}I", *offs))
    (path / "meta.json").write_text(json.dumps({"release": "test-graph", "domains_in_graph": 1000, "links_in_graph": len(edges),
                                                "indexed": len(ranked), "strong_sources": len(ranked), "in_cap": cap}))
    return linkgraph.LinkGraph(path)


RANKED = ["bignews.com", "techblog.io", "toolreviews.net", "wordpress.com", "rival1.com", "rival2.com", "nicheblog.org",
          "apexflowlabs.com", "smalldir.co.uk", "other.com"]
EDGES = [("bignews.com", "rival1.com"), ("bignews.com", "rival2.com"), ("techblog.io", "rival1.com"), ("techblog.io", "rival2.com"),
         ("techblog.io", "apexflowlabs.com"), ("toolreviews.net", "rival1.com"), ("toolreviews.net", "rival2.com"),
         ("wordpress.com", "rival1.com"), ("wordpress.com", "rival2.com"), ("nicheblog.org", "rival1.com"),
         ("smalldir.co.uk", "other.com"), ("nicheblog.org", "other.com"), ("toolreviews.net", "other.com")]


@pytest.fixture
def graph(tmp_path):
    return write_graph(tmp_path / "graph", RANKED, EDGES)


def test_clean_accepts_urls_and_rejects_junk():
    assert linkgraph.clean("https://WWW.Example.com:443/a/b?x=1") == "www.example.com"
    for bad in ("", "localhost", "10.0.0.1", "exa mple.com", "-bad.com", 5):
        with pytest.raises(ValueError):
            linkgraph.clean(bad)


def test_summary_rolls_subdomains_up_to_the_registered_domain(graph):
    s = graph.summary("https://beauty.apexflowlabs.com/shop")
    assert s["found"] and s["domain"] == "apexflowlabs.com" and s["global_rank"] == 8
    assert s["referring_domains"] == 1 and 0 < s["authority"] < 1000
    assert graph.summary("nowhere-at-all.com")["found"] is False
    assert graph.authority(1) == 1000 and graph.authority(1000) == 0


def test_referring_domains_are_strongest_first_and_filterable(graph):
    r = graph.referring("rival1.com")
    assert [x["domain"] for x in r["referring_domains"]] == ["bignews.com", "techblog.io", "toolreviews.net", "wordpress.com", "nicheblog.org"]
    assert "wordpress.com" not in [x["domain"] for x in graph.referring("rival1.com", skip_platforms=True)["referring_domains"]]
    floor = graph.rank_authority(2)
    assert all(x["authority"] >= floor for x in graph.referring("rival1.com", min_authority=floor)["referring_domains"])
    assert graph.referring("rival1.com", limit=2, offset=1)["referring_domains"][0]["domain"] == "techblog.io"


def test_gap_counts_competitors_flags_earned_and_drops_platforms(graph):
    g = graph.gap("apexflowlabs.com", ["rival1.com", "rival2.com"], min_competitors=2, min_authority=0)
    rows = {r["domain"]: r for r in g["gap"]}
    assert set(rows) == {"bignews.com", "techblog.io", "toolreviews.net"}        # wordpress.com is a platform
    assert rows["techblog.io"]["earned"] is True and rows["bignews.com"]["earned"] is False
    assert rows["bignews.com"]["links_to"] == ["rival1.com", "rival2.com"]
    assert g["summary"]["gap"] == 2 and g["summary"]["earned"] == 1
    only = graph.gap("apexflowlabs.com", ["rival1.com", "rival2.com"], min_authority=0, include_earned=False, exclude=["bignews.com"])
    assert [r["domain"] for r in only["gap"]] == ["toolreviews.net"]


def test_competitors_from_shared_niche_linkers(graph):
    c = graph.competitors("rival1.com", 5)
    names = [x["domain"] for x in c["competitors"]]
    assert names[0] == "rival2.com" and "rival1.com" not in names


def test_seo_analyze_ranks_issues():
    good = analyze({"url": "https://a.com/p", "title": "A clear page title for search", "text": "word " * 400,
                    "metadata": {"description": "d" * 120, "og:title": "x", "og:image": "y"},
                    "seo": {"canonical": "https://a.com/p", "lang": "en", "viewport": True, "h1_count": 1, "images": 2, "images_missing_alt": 0},
                    "headings": [{"level": 1, "text": "Main"}], "links": [{"url": "https://a.com/q"}],
                    "structured_data": [{"@type": "Organization"}]})
    assert good["score"] == 100 and good["issues"] == []
    bad = analyze({"url": "https://a.com/p", "title": "", "text": "short", "metadata": {"robots": "noindex"},
                   "seo": {"h1_count": 0, "images": 3, "images_missing_alt": 2, "viewport": False}, "links": []})
    codes = [i["code"] for i in bad["issues"]]
    assert codes[:3] == ["title_missing", "h1_missing", "noindex"] and "images_missing_alt" in codes and bad["score"] < 40


async def test_graph_endpoints_charge_their_price(tmp_path, monkeypatch, graph):
    monkeypatch.delenv("SCRAPER_GATEWAY_SECRET", raising=False)
    billing = Billing(tmp_path / "billing.db")
    h = Hosted(billing, "fixture", "fixture-webhook", "fixture-delivery")

    async def scrape(url, **kwargs):
        return {"url": url, "markdown": "# x", "text": "x", "rendered": False}

    acct, auth = account(h, "starter", "cs_test_links1")
    start = billing.remaining(acct)
    app = make_app(billing, scrape, hosted=h, link_options={"graph": lambda: graph})
    async with TestClient(TestServer(app)) as client:
        r = await client.post("/v1/links/authority", json={"domain": "apexflowlabs.com"}, headers=auth)
        assert r.status == 200 and (await r.json())["credits_used"] == 1
        r = await client.post("/v1/links/referring-domains", json={"domain": "rival1.com", "limit": 3}, headers=auth)
        body = await r.json()
        assert body["credits_used"] == 2 and body["result"]["returned"] == 3
        r = await client.post("/v1/links/gap", json={"domain": "apexflowlabs.com", "competitors": ["rival1.com", "rival2.com"], "min_authority": 0}, headers=auth)
        body = await r.json()
        assert body["credits_used"] == 10 and body["result"]["summary"]["gap"] == 2
        r = await client.post("/v1/links/competitors", json={"domain": "rival1.com"}, headers=auth)
        assert (await r.json())["credits_used"] == 5
        assert billing.remaining(acct) == start - 18
        r = await client.post("/v1/links/gap", json={"domain": "apexflowlabs.com", "competitors": []}, headers=auth)
        assert r.status == 400 and billing.remaining(acct) == start - 18
        r = await client.post("/v1/links/authority", json={"domain": "x"}, headers={"Authorization": "Bearer nope"})
        assert r.status == 401


async def test_missing_index_answers_503_and_charges_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("SCRAPER_GATEWAY_SECRET", raising=False)
    billing = Billing(tmp_path / "billing.db")
    h = Hosted(billing, "fixture", "fixture-webhook", "fixture-delivery")
    acct, auth = account(h, "starter", "cs_test_links2")
    start = billing.remaining(acct)

    def missing():
        raise FileNotFoundError

    async def scrape(url, **kwargs):
        return {"url": url, "text": "x", "rendered": False}

    async with TestClient(TestServer(make_app(billing, scrape, hosted=h, link_options={"graph": missing}))) as client:
        r = await client.post("/v1/links/authority", json={"domain": "apexflowlabs.com"}, headers=auth)
        assert r.status == 503 and (await r.json())["code"] == "links_unavailable" and billing.remaining(acct) == start


async def _wait(client, auth, job):
    for _ in range(200):
        r = await client.get("/v1/links/jobs/" + job, headers=auth)
        body = await r.json()
        if body["status"] != "running":
            return body
        await asyncio.sleep(0.02)
    raise AssertionError("job did not finish")


async def test_dead_page_hunt_finds_404s_and_reads_the_archive(tmp_path, monkeypatch, graph):
    monkeypatch.delenv("SCRAPER_GATEWAY_SECRET", raising=False)
    billing = Billing(tmp_path / "billing.db")
    h = Hosted(billing, "fixture", "fixture-webhook", "fixture-delivery")
    acct, auth = account(h, "starter", "cs_test_links3")

    async def archive(domain, limit):
        return [("https://rival1.com/crm-pricing-guide", "20220301000000"), ("https://rival1.com/live-page", "20240101000000"),
                ("https://rival1.com/old-offer", "20230101000000")]

    async def status(url):
        return {"crm-pricing-guide": (404, False), "live-page": (200, False), "old-offer": (200, True)}[url.rsplit("/", 1)[1]]

    async def latest(url):
        return "20250505000000" if url.endswith("crm-pricing-guide") else None

    async def scrape(url, **kwargs):
        assert "web.archive.org/web/20250505000000id_/" in url or "web.archive.org/web/20230101000000id_/" in url
        return {"url": url, "title": "CRM Pricing Guide", "markdown": "# CRM Pricing Guide\n\nWhat each plan costs.\n## Plans", "text": "x", "rendered": False}

    start = billing.remaining(acct)
    app = make_app(billing, scrape, hosted=h, link_options={"graph": lambda: graph, "archive_fn": archive, "status_fn": status, "snapshot": latest})
    async with TestClient(TestServer(app)) as client:
        r = await client.post("/v1/links/dead", json={"domain": "rival1.com", "read_archive": 1}, headers=auth)
        assert r.status == 200
        body = await _wait(client, auth, (await r.json())["id"])
        assert body["status"] == "completed", body
        res = body["result"]
        assert res["checked"] == 3 and {d["url"].rsplit("/", 1)[1] for d in res["dead_pages"]} == {"crm-pricing-guide", "old-offer"}
        first = next(d for d in res["dead_pages"] if "used_to_say" in d)
        assert first["used_to_say"]["title"] == "CRM Pricing Guide" and "Plans" in first["used_to_say"]["headings"]
        if first["url"].endswith("crm-pricing-guide"):
            assert first["last_seen_working"] == "2025-05-05" and "20250505000000" in first["archive_url"]
        assert res["who_links_to_the_site"][0]["domain"] == "bignews.com"
        assert body["credits_used"] == 4 and billing.remaining(acct) == start - 4


async def test_link_finder_reports_page_anchor_and_nofollow(tmp_path, monkeypatch, graph):
    monkeypatch.delenv("SCRAPER_GATEWAY_SECRET", raising=False)
    billing = Billing(tmp_path / "billing.db")
    h = Hosted(billing, "fixture", "fixture-webhook", "fixture-delivery")
    acct, auth = account(h, "starter", "cs_test_links4")
    pages = {
        "https://techblog.io/": [{"url": "https://techblog.io/best-crm-tools", "text": "Best CRM tools", "rel": []},
                                 {"url": "https://techblog.io/about", "text": "About", "rel": []}],
        "https://techblog.io/best-crm-tools": [{"url": "https://www.rival1.com/pricing", "text": "Rival One pricing", "rel": []},
                                               {"url": "https://rival1.com/", "text": "Rival One", "rel": ["nofollow"]}],
        "https://techblog.io/about": [],
    }

    async def scrape(url, **kwargs):
        return {"url": url, "title": "t", "text": "x", "links": pages.get(url, []), "rendered": False}

    async with TestClient(TestServer(make_app(billing, scrape, hosted=h, link_options={"graph": lambda: graph}))) as client:
        r = await client.post("/v1/links/find", json={"target": "rival1.com", "site": "techblog.io", "max_pages": 5}, headers=auth)
        body = await _wait(client, auth, (await r.json())["id"])
        links = body["result"]["links"]
        assert body["result"]["pages_read"] == 3
        assert {(l["anchor"], l["dofollow"]) for l in links} == {("Rival One pricing", True), ("Rival One", False)}
        assert all(l["page"] == "https://techblog.io/best-crm-tools" for l in links)
        r = await client.post("/v1/links/find", json={"target": "https://rival1.com/pricing", "site": "techblog.io", "max_pages": 5}, headers=auth)
        body = await _wait(client, auth, (await r.json())["id"])
        assert [l["anchor"] for l in body["result"]["links"]] == ["Rival One pricing"]


def test_dead_hunt_skips_addresses_that_were_never_pages():
    from rainbo_scrape.hosted_links import DOMAINISH, JUNK
    for junk in ("https://a.com/blog/post/%23section", "https://a.com/q/x/%23:~:text=Hello", "https://a.com/a b"):
        assert JUNK.search(junk)
    assert DOMAINISH.match("news.ycombinator.com") and not DOMAINISH.match("guide.html") and not DOMAINISH.match("best-crm-tools")
    assert not JUNK.search("https://a.com/blog/best-python-web-scraping-libraries")


def test_changes_between_releases(tmp_path):
    from rainbo_scrape.hosted_outreach import changes
    old = write_graph(tmp_path / "old", RANKED, EDGES + [("smalldir.co.uk", "rival1.com")])
    new = write_graph(tmp_path / "new", RANKED, [e for e in EDGES if e != ("techblog.io", "rival1.com")] + [("other.com", "rival1.com")])
    c = changes(new, old, "rival1.com")
    assert [x["domain"] for x in c["lost_domains"]] == ["techblog.io", "smalldir.co.uk"]
    assert [x["domain"] for x in c["new"]] == ["other.com"] and c["gained"] == 1 and c["lost"] == 2


def test_contacts_pick_site_emails_pages_and_social():
    from rainbo_scrape.hosted_outreach import contacts_from
    pages = [("https://blog.io/", {"markdown": "Write to hello@blog.io or icon@2x.png", "links": [
                {"url": "mailto:editor@blog.io?subject=Hi"}, {"url": "https://blog.io/write-for-us", "text": "Write for us"},
                {"url": "https://twitter.com/blogio"}, {"url": "https://twitter.com/"}, {"url": "https://other.com/contact"}]}),
             ("https://blog.io/contact", {"markdown": "Agency: pr@agency.com", "links": []})]
    c = contacts_from(pages, "blog.io")
    assert [e["email"] for e in c["emails"]] == ["editor@blog.io", "hello@blog.io", "pr@agency.com"]
    assert c["emails"][0]["on_site_domain"] and not c["emails"][2]["on_site_domain"]
    assert [p["url"] for p in c["contact_pages"]] == ["https://blog.io/write-for-us"] and c["social"] == {"x": "https://twitter.com/blogio"}


async def test_changes_and_contacts_endpoints(tmp_path, monkeypatch, graph):
    monkeypatch.delenv("SCRAPER_GATEWAY_SECRET", raising=False)
    billing = Billing(tmp_path / "billing.db")
    h = Hosted(billing, "fixture", "fixture-webhook", "fixture-delivery")
    acct, auth = account(h, "starter", "cs_test_links5")
    old = write_graph(tmp_path / "old", RANKED, EDGES + [("smalldir.co.uk", "rival1.com")])

    async def scrape(url, **kwargs):
        if url.endswith("/contact"):
            return {"url": url, "title": "c", "markdown": "Email team@techblog.io", "text": "x", "links": [], "rendered": False}
        return {"url": url, "title": "t", "markdown": "home", "text": "x", "links": [{"url": "https://techblog.io/contact", "text": "Contact"}], "rendered": False}

    start = billing.remaining(acct)
    app = make_app(billing, scrape, hosted=h, link_options={"graph": lambda: graph, "prev": lambda: old})
    async with TestClient(TestServer(app)) as client:
        r = await client.post("/v1/links/changes", json={"domain": "rival1.com"}, headers=auth)
        body = await r.json()
        assert r.status == 200 and body["credits_used"] == 3 and body["result"]["lost_domains"][0]["domain"] == "smalldir.co.uk"
        r = await client.post("/v1/links/contacts", json={"site": "techblog.io", "max_pages": 2}, headers=auth)
        body = await r.json()
        assert r.status == 200 and body["result"]["emails"][0]["email"] == "team@techblog.io" and body["credits_used"] == 2
        assert billing.remaining(acct) == start - 5


def test_history_reads_each_release(graph):
    from array import array
    from rainbo_scrape.hosted_outreach import history
    hist = graph.path / "history"
    hist.mkdir()
    n = graph.count
    row = graph.find("rival1.com")[1]
    refs, auth = array("I", [0] * n), array("H", [0] * n)
    refs[row], auth[row] = 2, 150
    (hist / "cc-main-2019-aug-sep-oct.refs").write_bytes(refs.tobytes())
    (hist / "cc-main-2019-aug-sep-oct.auth").write_bytes(auth.tobytes())
    (hist / "cc-main-2017-aug-sep-oct.refs").write_bytes(array("I", [0] * n).tobytes())
    (hist / "cc-main-2017-aug-sep-oct.auth").write_bytes(array("H", [0] * n).tobytes())
    (hist / "index.json").write_text(json.dumps({"releases": [{"release": "cc-main-2017-aug-sep-oct"}, {"release": "cc-main-2019-aug-sep-oct"}]}))
    h = history(graph, "rival1.com")
    assert [p["referring_domains"] for p in h["points"]] == [0, 2, 5]
    assert h["first_seen_linked"] == "2019 aug-sep-oct" and h["change"]["referring_domains"] == 3


async def test_free_checker_needs_no_key_and_is_rate_limited(tmp_path, monkeypatch, graph):
    monkeypatch.delenv("SCRAPER_GATEWAY_SECRET", raising=False)
    billing = Billing(tmp_path / "billing.db")
    h = Hosted(billing, "fixture", "fixture-webhook", "fixture-delivery")

    async def scrape(url, **kwargs):
        return {"url": url, "text": "x", "rendered": False}

    site = {"Origin": "https://webscraper.apexflowlabs.com"}
    async with TestClient(TestServer(make_app(billing, scrape, hosted=h, link_options={"graph": lambda: graph}))) as client:
        assert (await client.post("/api/free-links", json={"domain": "rival1.com"})).status == 403
        r = await client.post("/api/free-links", json={"domain": "rival1.com"}, headers=site)
        body = await r.json()
        assert r.status == 200 and body["result"]["found"] and len(body["result"]["top_referring_domains"]) == 4
        assert (await client.post("/api/free-links", json={"domain": "x"}, headers=site)).status == 400
        for _ in range(19):
            await client.post("/api/free-links", json={"domain": "rival1.com"}, headers=site)
        assert (await client.post("/api/free-links", json={"domain": "rival1.com"}, headers=site)).status == 429
