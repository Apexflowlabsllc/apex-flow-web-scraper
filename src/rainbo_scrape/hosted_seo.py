"""SEO checks built on the scrape engine.

  POST /v1/seo/page    one page's search health: title, description, headings, canonical, robots, language, images,
                       structured data, links, with every issue ranked by severity. The usual 1 credit for the page.
  POST /v1/seo/audit   a background crawl of a site that runs the page checks on every page and adds what only a
                       whole-site view finds: broken internal links, duplicate titles and descriptions, orphan-free
                       structure. The usual 1 credit a page. Results at GET /v1/seo/jobs/{id}.
"""
import json
from urllib.parse import urldefrag, urlsplit

from aiohttp import web

from .policy import ScrapeError, check_url

SEVERITY = {"error": 15, "warning": 5, "notice": 1}


def _site(host):
    host = (host or "").lower()
    return host[4:] if host.startswith("www.") else host


def analyze(result):
    """Facts and issues for one scraped page (the engine's full HTML result)."""
    title = (result.get("title") or "").strip()
    meta = {(k or "").lower(): v for k, v in (result.get("metadata") or {}).items()}
    seo = result.get("seo") or {}
    url = result.get("url") or ""
    host = _site(urlsplit(url).hostname)
    links = [l for l in result.get("links") or [] if isinstance(l, dict)]
    internal = [l for l in links if _site(urlsplit(l.get("url", "")).hostname) == host]
    headings = result.get("headings") or []
    words = len((result.get("text") or "").split())
    desc = (meta.get("description") or "").strip()
    structured = result.get("structured_data") or []
    types = sorted({str(x.get("@type")) for x in structured if isinstance(x, dict) and x.get("@type")})
    issues = []

    def add(code, severity, message):
        issues.append({"code": code, "severity": severity, "message": message})

    if not title:
        add("title_missing", "error", "The page has no <title>.")
    elif len(title) > 60:
        add("title_long", "warning", f"Title is {len(title)} characters; search results cut it near 60.")
    elif len(title) < 15:
        add("title_short", "warning", f"Title is only {len(title)} characters.")
    if not desc:
        add("description_missing", "warning", "No meta description: search engines will pick their own snippet.")
    elif len(desc) > 160:
        add("description_long", "notice", f"Meta description is {len(desc)} characters; results cut it near 160.")
    elif len(desc) < 70:
        add("description_short", "notice", f"Meta description is only {len(desc)} characters.")
    h1 = seo.get("h1_count", sum(1 for x in headings if x.get("level") == 1))
    if h1 == 0:
        add("h1_missing", "error", "No H1 heading.")
    elif h1 > 1:
        add("h1_multiple", "warning", f"{h1} H1 headings; one main heading is clearer.")
    robots = (seo.get("robots") or meta.get("robots") or "").lower()
    if "noindex" in robots:
        add("noindex", "error", "robots meta says noindex: this page is kept out of search results.")
    canonical = seo.get("canonical")
    if not canonical:
        add("canonical_missing", "notice", "No canonical link.")
    elif canonical.rstrip("/") != url.rstrip("/"):
        add("canonical_elsewhere", "notice", f"Canonical points to another address: {canonical}")
    if not seo.get("lang"):
        add("lang_missing", "notice", "The <html> element has no lang attribute.")
    if seo and not seo.get("viewport"):
        add("viewport_missing", "warning", "No viewport meta tag: the page may not fit phone screens.")
    if seo.get("images_missing_alt"):
        add("images_missing_alt", "warning", f"{seo['images_missing_alt']} of {seo.get('images')} images have no alt text.")
    if words < 300:
        add("thin_content", "notice", f"Only {words} words of main text.")
    if not types:
        add("structured_data_missing", "notice", "No JSON-LD structured data (Organization, Product, Article, FAQ...).")
    if not meta.get("og:title") or not meta.get("og:image"):
        add("social_preview_missing", "notice", "Missing og:title or og:image: shared links show no preview card.")
    if not internal:
        add("no_internal_links", "warning", "No links to other pages on this site.")
    issues.sort(key=lambda i: -SEVERITY[i["severity"]])
    score = max(0, 100 - sum(SEVERITY[i["severity"]] for i in issues))
    return {"url": url, "score": score, "title": title, "title_length": len(title), "description": desc or None,
            "description_length": len(desc), "h1": [x["text"] for x in headings if x.get("level") == 1][:5],
            "headings": {f"h{n}": sum(1 for x in headings if x.get("level") == n) for n in range(1, 4)},
            "words": words, "canonical": canonical, "robots": robots or None, "lang": seo.get("lang"),
            "hreflang": seo.get("hreflang") or [], "images": seo.get("images"), "images_missing_alt": seo.get("images_missing_alt"),
            "structured_data_types": types, "links_internal": len(internal), "links_external": len(links) - len(internal),
            "issues": issues}


def install(h, app, perform, map_urls=None):
    jobs = h.link_jobs

    async def read(account, url, named):
        try:
            response = await perform(account, {"url": url, "render": "auto", "fallback": "none"}, named=named)
            item = json.loads(response.body)
            return response.status, item
        except web.HTTPException as exc:
            return exc.status, {}
        except (ScrapeError, ValueError) as exc:
            return 400, {"code": getattr(exc, "code", "invalid_request"), "error": str(exc)}

    async def page_for(account, body):
        if not isinstance(body, dict) or set(body) - {"url"} or not isinstance(body.get("url"), str):
            raise ValueError("Send a url")
        try:
            url = check_url(body["url"])
        except ScrapeError as exc:
            raise ValueError(str(exc))
        status, item = await read(account, url, True)
        if status != 200:
            return web.json_response({**item, "error": item.get("error", "The page could not be read; nothing was charged.")}, status=status)
        return web.json_response({"credits_used": item.get("credits_used", 1), "credits_left": h.billing.remaining(account),
                                  "result": analyze(item.get("result") or {})})

    async def auditor(job, account, start, limit):
        host = _site(urlsplit(start).hostname)
        queue, seen, pages, broken, inbound, credits, note = [start], {start}, [], {}, {}, 0, None

        def offer(url, source):
            url = urldefrag(url)[0]
            p = urlsplit(url)
            if p.scheme not in ("http", "https") or _site(p.hostname) != host:
                return
            inbound.setdefault(url, set()).add(source)
            if url not in seen and len(seen) < limit * 5 and not p.path.lower().endswith((".jpg", ".png", ".gif", ".webp", ".svg", ".pdf", ".zip", ".css", ".js")):
                seen.add(url)
                queue.append(url)

        if map_urls:
            try:
                for u in await map_urls(start, limit * 2):
                    if isinstance(u, str) and u not in seen and _site(urlsplit(u).hostname) == host:
                        seen.add(u)
                        queue.append(u)
            except Exception:
                pass
        while queue and len(pages) + len(broken) < limit and jobs["running"](job):
            url = queue.pop(0)
            status, item = await read(account, url, False)
            if status == 402:
                note = "Stopped: not enough credits. Pages already checked are kept."
                break
            if status == 429:
                queue.insert(0, url)
                import asyncio
                await asyncio.sleep(10)
                continue
            if status != 200:
                broken[url] = item.get("code") or status
                continue
            credits += item.get("credits_used", 1)
            result = item.get("result") or {}
            report = analyze(result)
            pages.append(report)
            for link in result.get("links") or []:
                if isinstance(link, dict):
                    offer(link.get("url", ""), url)
            jobs["save"](job, credits=credits, progress={"pages_checked": len(pages), "broken": len(broken), "queued": len(queue)})
        clusters = {}
        for p in pages:
            for issue in p["issues"]:
                c = clusters.setdefault(issue["code"], {"code": issue["code"], "severity": issue["severity"], "pages": 0, "examples": []})
                c["pages"] += 1
                if len(c["examples"]) < 5:
                    c["examples"].append(p["url"])
        for field, code in (("title", "duplicate_title"), ("description", "duplicate_description")):
            groups = {}
            for p in pages:
                if p[field]:
                    groups.setdefault(p[field], []).append(p["url"])
            dupes = [urls for urls in groups.values() if len(urls) > 1]
            if dupes:
                clusters[code] = {"code": code, "severity": "warning", "pages": sum(len(d) for d in dupes),
                                  "examples": [u for d in dupes[:3] for u in d[:2]]}
        broken_rows = [{"url": u, "problem": str(code), "linked_from": sorted(inbound.get(u, []))[:10]} for u, code in broken.items()]
        if broken_rows:
            clusters["broken_internal_links"] = {"code": "broken_internal_links", "severity": "error", "pages": len(broken_rows),
                                                 "examples": [r["url"] for r in broken_rows[:5]]}
        ordered = sorted(clusters.values(), key=lambda c: (-SEVERITY[c["severity"]], -c["pages"]))
        health = round(sum(p["score"] for p in pages) / len(pages)) if pages else 0
        if broken_rows and pages:
            health = max(0, health - min(30, 3 * len(broken_rows)))
        return {"site": start, "health": health, "pages_checked": len(pages), "issues": ordered, "broken": broken_rows,
                "pages": [{k: p[k] for k in ("url", "score", "title", "words")} | {"issues": [i["code"] for i in p["issues"]]}
                          for p in sorted(pages, key=lambda p: p["score"])]}, credits, note

    async def audit_for(account, body):
        from .hosted_crawl import PAGE_LIMITS, _plan
        if not isinstance(body, dict) or set(body) - {"url", "limit"} or not isinstance(body.get("url"), str):
            raise ValueError("Send a url and optional limit")
        try:
            start = urldefrag(check_url(body["url"]))[0]
        except ScrapeError as exc:
            raise ValueError(str(exc))
        most = PAGE_LIMITS.get(_plan(h, account), 100)
        limit = body.get("limit", min(50, most))
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= most:
            raise ValueError(f"limit must be 1-{most} on this plan")
        return jobs["begin"](account, "audit", {"url": start, "limit": limit}, lambda job: auditor(job, account, start, limit))

    def route(fn):
        async def handler(request):
            body = await request.json() if request.can_read_body else {}
            return await fn(request["account"], body)
        return handler

    app.router.add_post("/v1/seo/page", route(page_for))
    app.router.add_post("/v1/seo/audit", route(audit_for))
    return {"/seo/page": page_for, "/seo/audit": audit_for}
