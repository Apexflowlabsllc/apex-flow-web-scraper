"""Outreach and link changes: the steps after a link gap.

  POST /v1/links/changes   strong referring domains a site gained and lost between the last two link-index releases
                           (lost links are the easiest to win back)                                            3 credits
  POST /v1/links/contacts  how to reach a linking site: public contact emails, contact and "write for us" pages,
                           and social profiles, read live from the site's own pages          1 credit per page read
"""
import asyncio
import json
import os
import re
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from aiohttp import web

from . import linkgraph
from .billing import QuotaExceeded

EMAIL = re.compile(r"(?<![\w.+-])[a-z0-9][a-z0-9._%+-]{0,63}@[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,24}(?![\w-])", re.I)
PAGES = re.compile(r"contact|write-for-us|writeforus|guest-?post|contribute|advertis|submit|partner|press|media-kit|about", re.I)
SOCIAL = {"twitter.com": "x", "x.com": "x", "linkedin.com": "linkedin", "facebook.com": "facebook", "instagram.com": "instagram",
          "youtube.com": "youtube", "tiktok.com": "tiktok"}
JUNK_EMAIL = re.compile(r"\.(png|jpe?g|gif|webp|svg)$|^(example|test|user|name|you|email)@|@(example|domain|sentry|wixpress)\.", re.I)
_prev = None


def previous():
    """The link index release before the current one, opened once; None when not installed."""
    global _prev
    if _prev is None:
        path = os.environ.get("APEX_LINKGRAPH_PREV_DIR", linkgraph.DEFAULT_DIR + "-prev")
        try:
            _prev = linkgraph.LinkGraph(path)
        except (FileNotFoundError, OSError):
            return None
    return _prev


def history(cur, domain):
    """Referring domains and authority for a domain in every past release kept under <index>/history."""
    import struct
    base = Path(cur.path) / "history"
    meta = json.loads((base / "index.json").read_text())
    name, row = cur.find(domain)
    points = []
    if row is not None:
        for rel in meta["releases"]:
            with open(base / (rel["release"] + ".refs"), "rb") as f:
                f.seek(row * 4)
                refs = struct.unpack("<I", f.read(4))[0]
            with open(base / (rel["release"] + ".auth"), "rb") as f:
                f.seek(row * 2)
                auth = struct.unpack("<H", f.read(2))[0]
            points.append({"release": rel["release"], "period": _period(rel["release"]), "referring_domains": refs, "authority": auth})
        r = cur.record(row)
        points.append({"release": cur.meta["release"], "period": _period(cur.meta["release"]), "referring_domains": r["referring_domains"],
                       "authority": cur.authority(r["position"])})
    first = next((p for p in points if p["referring_domains"]), None)
    return {"domain": name, "found": row is not None, "points": points,
            "first_seen_linked": first["period"] if first else None,
            "change": {"referring_domains": (points[-1]["referring_domains"] - points[-2]["referring_domains"]) if len(points) > 1 else None},
            "note": "Each point is one Common Crawl web graph release (three months of crawling). Counts include links from every domain, strong or weak."}


def _period(release):
    parts = release.replace("cc-main-", "").split("-")
    return parts[0] + " " + "-".join(p for p in parts[1:] if not p.isdigit())


def changes(cur, prev, domain, limit=100):
    """New and lost strong referring domains. Both releases list at most in_cap referrers per domain, so when a list is
    full, only referrers stronger than the weakest listed one in the other release are compared."""
    name, row = cur.find(domain)
    _, prow = prev.find(domain)
    now = {cur.name(r): cur.rank_authority(r) for r in (cur.referrers(row) if row is not None else [])}
    then = {prev.name(r): prev.rank_authority(r) for r in (prev.referrers(prow) if prow is not None else [])}
    floor_now = min(now.values()) if len(now) >= cur.cap else 0
    floor_then = min(then.values()) if len(then) >= prev.cap else 0
    new = sorted(({"domain": d, "authority": a} for d, a in now.items() if d not in then and a > floor_then), key=lambda x: -x["authority"])
    lost = sorted(({"domain": d, "authority_then": a} for d, a in then.items() if d not in now and a > floor_now), key=lambda x: -x["authority_then"])
    new = [x for x in new if not linkgraph._platform(x["domain"])]
    lost = [x for x in lost if not linkgraph._platform(x["domain"])]
    return {"domain": name, "from_release": prev.meta["release"], "to_release": cur.meta["release"],
            "strong_referring_domains": {"then": len(then), "now": len(now)}, "gained": len(new), "lost": len(lost),
            "new": new[:limit], "lost_domains": lost[:limit],
            "note": "Lost means the link was seen in the earlier release and not in the latest one. Common Crawl does not re-read every page "
                    "every quarter, so some lost links are only unseen: confirm each one with /v1/links/find before acting. A link that moved, "
                    "broke or was removed is the easiest to win back."}


def contacts_from(pages, site):
    emails, forms, social = {}, {}, {}
    for url, result in pages:
        text = " ".join(str(result.get(k) or "") for k in ("markdown", "text"))
        for link in result.get("links") or []:
            href = link.get("url", "") if isinstance(link, dict) else ""
            if href.lower().startswith("mailto:"):
                text += " " + href[7:].split("?")[0]
                continue
            host = (urlsplit(href).hostname or "").lower().removeprefix("www.")
            if host in SOCIAL and urlsplit(href).path.strip("/"):
                social.setdefault(SOCIAL[host], href)
            elif host.removeprefix("www.").endswith(site) and PAGES.search(urlsplit(href).path):
                forms.setdefault(href.split("#")[0], (link.get("text") or "").strip()[:80])
        for m in EMAIL.findall(text):
            m = m.lower().strip(".")
            if not JUNK_EMAIL.search(m):
                emails.setdefault(m, url)
    own = lambda e: e.split("@")[1] == site or e.split("@")[1].endswith("." + site)
    rows = [{"email": e, "found_on": u, "on_site_domain": own(e)} for e, u in emails.items()]
    rows.sort(key=lambda r: (not r["on_site_domain"], r["email"]))
    return {"site": site, "emails": rows[:20], "contact_pages": [{"url": u, "label": t} for u, t in list(forms.items())[:15]],
            "social": social, "pages_read": len(pages),
            "note": "Public contact details as the site publishes them. Use them for a relevant, personal request; do not add people to bulk lists."}


def install(h, app, perform, graph=linkgraph.graph, prev=previous):
    async def changes_for(account, body):
        if not isinstance(body, dict) or set(body) - {"domain", "limit"} or "domain" not in body:
            raise ValueError("Send a domain and optional limit")
        linkgraph.clean(body["domain"])
        limit = body.get("limit", 100)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("limit must be 1 to 1000")
        try:
            cur, old = graph(), prev()
        except (FileNotFoundError, OSError):
            cur, old = None, None
        if cur is None or old is None:
            return web.json_response({"error": "Link history is being installed. Nothing was charged.", "code": "links_unavailable",
                                      "retryable": True, "retry_after": 300}, status=503)
        from .hosted_features import admit_attempt
        admit_attempt(h, account)
        try:
            h.billing.reserve(account, 3)
        except QuotaExceeded:
            return web.json_response({"error": "Not enough credits", "code": "insufficient_credits", "retryable": False,
                                      "credits_left": h.billing.remaining(account), "buy_url": h.site + "/#pricing"}, status=402)
        try:
            result = changes(cur, old, body["domain"], limit)
        except BaseException:
            h.billing.refund(account, 3)
            raise
        return web.json_response({"credits_used": 3, "credits_left": h.billing.remaining(account), "result": result})

    async def read(account, url):
        try:
            response = await perform(account, {"url": url, "render": "never", "fallback": "none", "outputs": ["markdown", "links"]}, named=True)
            item = json.loads(response.body)
            return response.status, item
        except web.HTTPException as exc:
            return exc.status, {}
        except Exception:
            return 502, {}

    async def contacts_for(account, body):
        if not isinstance(body, dict) or set(body) - {"site", "max_pages"} or "site" not in body:
            raise ValueError("Send a site and optional max_pages")
        site = linkgraph.clean(body["site"]).removeprefix("www.")
        most = body.get("max_pages", 5)
        if isinstance(most, bool) or not isinstance(most, int) or not 1 <= most <= 10:
            raise ValueError("max_pages must be 1 to 10")
        home = "https://" + site + "/"
        status, item = await read(account, home)
        if status == 402:
            return web.json_response(item, status=402)
        pages, credits = [], 0
        if status == 200:
            pages.append((home, item.get("result") or {}))
            credits += item.get("credits_used", 1)
        found = [l.get("url") for l in (pages[0][1].get("links") if pages else []) or []
                 if isinstance(l, dict) and PAGES.search(urlsplit(l.get("url", "")).path)
                 and (urlsplit(l.get("url", "")).hostname or "").removeprefix("www.").endswith(site)]
        guesses = [urljoin(home, p) for p in ("contact", "contact-us", "about", "write-for-us")]
        queue = list(dict.fromkeys([u.split("#")[0] for u in found if u] + guesses))[:most - len(pages)]
        results = await asyncio.gather(*(read(account, u) for u in queue))
        for url, (st, it) in zip(queue, results):
            if st == 200:
                pages.append((url, it.get("result") or {}))
                credits += it.get("credits_used", 1)
        if not pages:
            return web.json_response({"error": "The site could not be read; nothing was charged.", "code": "source_failed", "retryable": True}, status=502)
        return web.json_response({"credits_used": credits, "credits_left": h.billing.remaining(account), "result": contacts_from(pages, site)})

    def route(fn):
        async def handler(request):
            body = await request.json() if request.can_read_body else {}
            return await fn(request["account"], body)
        return handler

    async def history_for(account, body):
        if not isinstance(body, dict) or set(body) - {"domain"} or "domain" not in body:
            raise ValueError("Send a domain")
        linkgraph.clean(body["domain"])
        try:
            cur = graph()
            if not (Path(cur.path) / "history" / "index.json").exists():
                raise FileNotFoundError
        except (FileNotFoundError, OSError):
            return web.json_response({"error": "Link history is being installed. Nothing was charged.", "code": "links_unavailable",
                                      "retryable": True, "retry_after": 300}, status=503)
        from .hosted_features import admit_attempt
        admit_attempt(h, account)
        try:
            h.billing.reserve(account, 2)
        except QuotaExceeded:
            return web.json_response({"error": "Not enough credits", "code": "insufficient_credits", "retryable": False,
                                      "credits_left": h.billing.remaining(account), "buy_url": h.site + "/#pricing"}, status=402)
        try:
            result = history(cur, body["domain"])
        except BaseException:
            h.billing.refund(account, 2)
            raise
        return web.json_response({"credits_used": 2, "credits_left": h.billing.remaining(account), "result": result})

    async def free_links(request):
        """The free backlink checker on the website: authority and the ten strongest referring domains, no key,
        20 checks an hour per visitor."""
        body = await request.json() if request.can_read_body else {}
        domain = body.get("domain") if isinstance(body, dict) else None
        try:
            linkgraph.clean(domain)
        except ValueError as exc:
            return web.json_response({"error": str(exc), "code": "invalid_request"}, status=400)
        h.limit(request, "free-links", 20, 3600)
        try:
            g = graph()
        except (FileNotFoundError, OSError):
            return web.json_response({"error": "The link index is being installed. Retry shortly.", "code": "links_unavailable"}, status=503)
        summary = g.summary(domain)
        top = g.referring(domain, limit=10, skip_platforms=True)["referring_domains"] if summary["found"] else []
        return web.json_response({"result": {**summary, "top_referring_domains": top},
                                  "more": "The full list, link gap, dead-page hunt and outreach contacts are in the API: " + h.site + "/backlinks.html"})

    app.router.add_post("/api/free-links", free_links)
    app.router.add_post("/v1/links/history", route(history_for))
    app.router.add_post("/v1/links/changes", route(changes_for))
    app.router.add_post("/v1/links/contacts", route(contacts_for))
    return {"/links/changes": changes_for, "/links/contacts": contacts_for, "/links/history": history_for}
