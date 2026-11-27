"""Link Intelligence: backlink research built on our own link index and our own crawler.

Instant answers from the link index (rainbo_scrape.linkgraph, built from Common Crawl's web graph):
  POST /v1/links/authority          a domain's authority (0-1000), global rank and referring-domain counts   1 credit
  POST /v1/links/referring-domains  the strongest sites linking to a domain                                  2 credits
  POST /v1/links/gap                sites linking to several competitors but not to you                       10 credits
  POST /v1/links/competitors        sites the same niche linkers also link to                                 5 credits

Background jobs that read the live web with the metered scrape path (every page is the usual 1 credit):
  POST /v1/links/find   pages on one site that link to a target, with anchor text and nofollow flags
  POST /v1/links/dead   a competitor's dead pages: pages the archive saw that now return 404, what they used to say,
                        and the sites to ask for a link to your better version
  GET  /v1/links/jobs/{id}   status and results of either job
"""
import asyncio
import json
import logging
import re
import secrets
import time
from urllib.parse import quote, urlsplit, urlunsplit

import aiohttp
from aiohttp import web

from . import linkgraph
from .billing import QuotaExceeded

PRICES = {"authority": 1, "referring-domains": 2, "gap": 10, "competitors": 5}
KEEP_SECONDS = 7 * 86400
DEADLINE = 1200
HINTS = ("resource", "link", "partner", "tool", "best", "review", "top", "vs", "alternative", "directory", "blog",
         "guide", "recommend", "list", "compare", "useful", "sponsor", "friends", "press", "news")
SKIP = re.compile(r"\.(jpg|jpeg|png|gif|webp|svg|ico|css|js|zip|gz|pdf|mp4|mp3|woff2?|xml|json|txt)$|/(feed|tag|tags|author|page|wp-json|cdn-cgi|category)/|[?&]", re.I)
CDX = "https://web.archive.org/cdx/search/cdx"
STOPWORDS = {"with", "your", "from", "that", "this", "best", "free", "online", "home", "page", "welcome", "official", "site",
             "website", "more", "make", "into", "what", "will", "have", "just", "only", "every", "about", "their", "they"}
JUNK = re.compile(r"%23|%3a~|#|:~:|%20|\s|\.\.|//[^/]*//", re.I)
DOMAINISH = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)*\.(com|net|org|io|co|dev|ai|app|me|us|uk|de|fr|info|biz)$")


def _error(status, message, code, **extra):
    return web.json_response({"error": message, "code": code, "retryable": status in (429, 502, 503), **extra}, status=status)


def _fields(body, allowed, required=()):
    if not isinstance(body, dict) or set(body) - set(allowed):
        raise ValueError("Use only: " + ", ".join(allowed))
    for r in required:
        if r not in body:
            raise ValueError(f"{r} is required")
    return body


def _int(body, name, default, lo, hi):
    v = body.get(name, default)
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise ValueError(f"{name} must be a whole number from {lo} to {hi}")
    return v


def _domains(value, name, most):
    if not isinstance(value, list) or not 1 <= len(value) <= most:
        raise ValueError(f"{name} must list 1 to {most} domains")
    return [linkgraph.clean(v) for v in value]


def _target(text):
    """(host, url prefix or None) for 'example.com' or 'https://example.com/page'."""
    if not isinstance(text, str) or not 3 <= len(text) <= 500:
        raise ValueError("target must be a domain or a page address")
    host = linkgraph.clean(text)
    if "/" in re.sub(r"^[a-z]+://", "", text.strip().lower()).rstrip("/"):
        p = urlsplit(text.strip() if "://" in text else "https://" + text.strip())
        return host, (host[4:] if host.startswith("www.") else host) + p.path.rstrip("/")
    return host, None


def _same_site(host, target):
    host, target = host.lower(), target.lower()
    host = host[4:] if host.startswith("www.") else host
    target = target[4:] if target.startswith("www.") else target
    return host == target or host.endswith("." + target)


async def site_urls(url, limit):
    """Every address a site's sitemaps list (up to 40 sitemap files), plus its home page links. Obeys robots.txt."""
    from .discovery import site_map
    from .engine import Fetcher
    from .settings import Settings
    from .store import Store
    settings = Settings()
    async with asyncio.timeout(60):
        async with Fetcher(settings, Store(settings)) as fetcher:
            found = await site_map(fetcher, url, limit=limit, include_links=True, max_sitemaps=40)
    return [u.get("url") if isinstance(u, dict) else u for u in found.get("urls", [])]


def prepare(h):
    h.db.executescript("""
        CREATE TABLE IF NOT EXISTS link_jobs(id TEXT PRIMARY KEY,account TEXT,kind TEXT,status TEXT,created REAL,finished REAL,
            options TEXT,progress TEXT,result TEXT,credits INTEGER DEFAULT 0,note TEXT);
        CREATE INDEX IF NOT EXISTS link_jobs_account ON link_jobs(account,created);
    """)
    h.db.execute("UPDATE link_jobs SET status='interrupted',finished=?,note='Service restarted. Results found so far are kept.' WHERE status='running'",
                 (time.time(),))


def install(h, app, perform, map_urls=None, graph=linkgraph.graph, archive_fn=None, status_fn=None, snapshot=None):
    tasks = {}

    def admit(account, credits):
        from .hosted_features import admit_attempt
        admit_attempt(h, account)
        h.billing.reserve(account, credits)

    async def instant(account, kind, build):
        try:
            g = graph()
        except (FileNotFoundError, OSError):
            return _error(503, "The link index is being installed. Retry in a few minutes; nothing was charged.", "links_unavailable", retry_after=300)
        price = PRICES[kind]
        try:
            admit(account, price)
        except QuotaExceeded:
            return _error(402, "Not enough credits", "insufficient_credits", credits_left=h.billing.remaining(account), buy_url=h.site + "/#pricing")
        try:
            result = build(g)
        except BaseException:
            h.billing.refund(account, price)
            raise
        return web.json_response({"credits_used": price, "credits_left": h.billing.remaining(account), "result": result})

    async def authority_for(account, body):
        b = _fields(body, ["domain"], ["domain"])
        linkgraph.clean(b["domain"])
        return await instant(account, "authority", lambda g: g.summary(b["domain"]))

    async def referring_for(account, body):
        b = _fields(body, ["domain", "limit", "offset", "min_authority", "skip_platforms"], ["domain"])
        linkgraph.clean(b["domain"])
        limit, offset = _int(b, "limit", 100, 1, 1000), _int(b, "offset", 0, 0, 1000)
        floor = _int(b, "min_authority", 0, 0, 1000)
        return await instant(account, "referring-domains",
                             lambda g: g.referring(b["domain"], limit, offset, floor, bool(b.get("skip_platforms", False))))

    async def gap_for(account, body):
        b = _fields(body, ["domain", "competitors", "min_competitors", "min_authority", "exclude_domains", "include_earned", "limit"],
                    ["domain", "competitors"])
        linkgraph.clean(b["domain"])
        rivals = _domains(b["competitors"], "competitors", 20)
        exclude = _domains(b["exclude_domains"], "exclude_domains", 500) if b.get("exclude_domains") else []
        need = _int(b, "min_competitors", 2 if len(rivals) > 1 else 1, 1, 20)
        floor = _int(b, "min_authority", 100, 0, 1000)
        limit = _int(b, "limit", 50, 1, 500)
        return await instant(account, "gap", lambda g: g.gap(b["domain"], rivals, need, floor, exclude,
                                                             b.get("include_earned", True) is not False, limit))

    async def competitors_for(account, body):
        b = _fields(body, ["domain", "limit"], ["domain"])
        linkgraph.clean(b["domain"])
        limit = _int(b, "limit", 20, 1, 100)
        return await instant(account, "competitors", lambda g: g.competitors(b["domain"], limit))

    # -- background jobs ----------------------------------------------------------------------------
    def save(job, **fields):
        sets = ",".join(f"{k}=?" for k in fields)
        h.db.execute(f"UPDATE link_jobs SET {sets} WHERE id=?", (*[json.dumps(v) if k in ("progress", "result") else v
                                                                    for k, v in fields.items()], job))

    def running(job):
        return h.db.execute("SELECT status FROM link_jobs WHERE id=?", (job,)).fetchone()[0] == "running"

    async def page(account, url, outputs, named=False):
        """One metered read. Returns (result or None, credits, stop note or None)."""
        for _ in range(4):
            try:
                body = {"url": url, "render": "never", "fallback": "none", **({"outputs": outputs} if outputs else {})}
                response = await perform(account, body, named=named)
                item = json.loads(response.body)
                status = response.status
            except web.HTTPException as exc:
                status, item = exc.status, {}
            except Exception:
                return None, 0, None
            if status == 200:
                return item.get("result", {}), item.get("credits_used", 1), None
            if status == 402:
                return None, 0, "Stopped: not enough credits. Results found so far are kept."
            if status != 429:
                return None, 0, None
            await asyncio.sleep(10)
        return None, 0, "Stopped: request limits were reached repeatedly. Results found so far are kept."

    async def finder(job, account, target, prefix, site, max_pages):
        start = "https://" + site + "/"
        brand = target.split(".")[0]
        queue, seen, found, credits, read = [start], {start}, [], 0, 0
        scored = {}
        # What the target is about, from its own home page title and description: a page about the same topic is
        # where a link to it usually sits ("best web scraping APIs" for a scraping API).
        topic = set()
        home, used, _ = await page(account, "https://" + (prefix.split("/")[0] if prefix else target) + "/", None)
        credits += used
        if home:
            meta = {(k or "").lower(): v for k, v in (home.get("metadata") or {}).items()}
            text = " ".join(str(x) for x in (home.get("title"), meta.get("description"), meta.get("og:title")) if x).lower()
            topic = {w[:5] for w in re.findall(r"[a-z]{4,}", text) if w not in STOPWORDS and brand not in w}

        def score(u):
            p = urlsplit(u)
            path = p.path.lower()
            words = {w[:5] for w in re.findall(r"[a-z]{4,}", path)}
            s = 10 if u == start else 0
            s += 8 * (brand in u.lower()) + 4 * len(words & topic) + 2 * any(k in path for k in HINTS) - path.count("/")
            return s

        def offer(u):
            p = urlsplit(u)
            if p.scheme in ("http", "https") and _same_site(p.hostname or "", site) and u not in seen and not SKIP.search(u) and len(seen) < 25000:
                seen.add(u)
                scored[u] = score(u)

        if map_urls:
            try:
                for u in await map_urls(start, 20000):
                    offer(u)
            except Exception:
                pass
        note = None
        while read < max_pages and running(job):
            if queue:
                url = queue.pop(0)
            elif scored:
                url = max(scored, key=scored.get)
                scored.pop(url)
            else:
                break
            result, used, note = await page(account, url, ["links"])
            if note:
                break
            read += 1
            credits += used
            for link in (result or {}).get("links") or []:
                href = link.get("url", "") if isinstance(link, dict) else ""
                host = urlsplit(href).hostname or ""
                if _same_site(host, target):
                    plain = (host[4:] if host.startswith("www.") else host) + urlsplit(href).path.rstrip("/")
                    if prefix and not plain.lower().startswith(prefix.lower()):
                        continue
                    rel = [r.lower() for r in (link.get("rel") or [])]
                    found.append({"page": url, "links_to": href, "anchor": link.get("text", ""),
                                  "dofollow": not ({"nofollow", "sponsored", "ugc"} & set(rel)), "rel": rel})
                else:
                    offer(href)
            save(job, credits=credits, progress={"pages_read": read, "links_found": len(found)})
        return {"target": prefix or target, "site": site, "pages_read": read, "links": found[:500],
                "note": "Pages are read in order of likelihood (home page, pages naming the target, resource and review pages)."}, credits, note

    async def archive_urls(domain, limit):
        params = {"url": domain + "/*", "output": "json", "fl": "original,timestamp,digest", "filter": ["statuscode:200", "mimetype:text/html"],
                  "collapse": "urlkey", "limit": str(min(20000, limit * 40))}
        query = "&".join(f"{k}={quote(x, safe='/*:')}" for k, v in params.items() for x in (v if isinstance(v, list) else [v]))
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=90)) as client:
            async with client.get(CDX + "?" + query) as response:
                rows = await response.json(content_type=None) if response.status == 200 else []
        out, seen = [], set()
        # A capture whose bytes match many other addresses is a template (a soft 404 or a catch-all page), not a page.
        digests = {}
        for row in rows[1:]:
            digests[row[2]] = digests.get(row[2], 0) + 1
        for row in rows[1:]:
            url, stamp = row[0], row[1]
            p = urlsplit(url if "://" in url else "http://" + url)
            path = p.path or "/"
            if (path == "/" or SKIP.search(url) or not 1 <= path.strip("/").count("/") + 1 <= 4 or digests[row[2]] > 2
                    or JUNK.search(url) or any(DOMAINISH.match(seg) for seg in path.lower().split("/"))):
                continue
            clean = urlunsplit(("https", p.hostname or domain, path, "", ""))
            if clean.rstrip("/") in seen:
                continue
            seen.add(clean.rstrip("/"))
            out.append((clean, stamp))
        # content pages first: longer, wordier paths are articles; short ones are menus
        out.sort(key=lambda x: -len(re.findall(r"[a-z]{3,}", urlsplit(x[0]).path.lower())))
        return out[:limit]

    async def status_of(url):
        from .engine import Fetcher
        from .settings import Settings
        from .policy import ScrapeError
        settings = Settings()
        try:
            async with asyncio.timeout(30):
                async with Fetcher(settings, None) as f:
                    r = await f.get(url, cache=False)
            final = urlsplit(r["url"])
            return r["status"], (final.path in ("", "/") and urlsplit(url).path not in ("", "/"))
        except ScrapeError as exc:
            return exc.code, False
        except Exception:
            return "fetch_failed", False

    async def hunter(job, account, domain, limit, read_archive):
        note, credits = None, 0
        try:
            urls = await (archive_fn or archive_urls)(domain, limit)
        except Exception:
            urls = []
        if not urls:
            return {"domain": domain, "checked": 0, "dead_pages": [],
                    "note": "The Internet Archive has no pages for this site, or did not answer. Nothing was charged."}, 0, None
        dead, checked = [], 0
        sem = asyncio.Semaphore(4)

        async def check(url, stamp):
            nonlocal checked, credits, note
            async with sem:
                if note or not running(job):
                    return
                try:
                    h.billing.reserve(account, 1)
                except QuotaExceeded:
                    note = "Stopped: not enough credits. Results found so far are kept."
                    return
                code, to_home = await (status_fn or status_of)(url)
                checked += 1
                if code in ("robots_denied", "robots_unavailable", "rate_limited", "upstream_error"):
                    h.billing.refund(account, 1)
                    checked -= 1
                    return
                credits += 1
                if code in (404, 410) or code == "dns_failed" or to_home:
                    dead.append({"url": url, "status": code if isinstance(code, int) else code,
                                 "redirects_home": to_home, "last_seen_working": f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}",
                                 "archive_url": f"https://web.archive.org/web/{stamp}/{url}"})
                save(job, credits=credits, progress={"checked": checked, "of": len(urls), "dead": len(dead)})

        await asyncio.gather(*(check(u, s) for u, s in urls))
        reads = []
        for item in dead[:read_archive]:
            if note or not running(job):
                break
            latest = await snapshot(item["url"]) if snapshot else None
            if latest:
                item["last_seen_working"] = f"{latest[:4]}-{latest[4:6]}-{latest[6:8]}"
                item["archive_url"] = f"https://web.archive.org/web/{latest}/{item['url']}"
            stamp = item["archive_url"].split("/web/")[1].split("/")[0]
            result, used, note = await page(account, f"https://web.archive.org/web/{stamp}id_/{item['url']}", ["markdown"], named=True)
            credits += used
            if result:
                md = result.get("markdown") or ""
                heads = [(len(l) - len(l.lstrip("#")), l.lstrip("# ").strip()) for l in md.splitlines() if l.startswith("#")]
                reads.append((item, result.get("title"), heads, md))
            save(job, credits=credits)
        # A dead address whose archived copy is the site's home page in disguise (same title and headings) was never a
        # real page: some sites answer any address with their home page. Compare with the archived home page.
        if reads and snapshot and not note:
            stamp = await snapshot("https://" + domain + "/")
            if stamp:
                home, used, note = await page(account, f"https://web.archive.org/web/{stamp}id_/https://{domain}/", ["markdown"], named=True)
                credits += used
                if home:
                    home_heads = {l.lstrip("# ").strip() for l in (home.get("markdown") or "").splitlines() if l.startswith("#")}
                    fake = []
                    for r in reads:
                        heads = {t for _, t in r[2]}
                        if r[1] == home.get("title") and heads and len(heads & home_heads) >= 0.6 * len(heads):
                            fake.append(r)
                    for r in fake:
                        reads.remove(r)
                        dead.remove(r[0])
        # Headings that recur on several of the site's pages (calls to action, sidebars) say nothing about one page.
        common = {}
        for _, _, heads, _ in reads:
            for t in {t for _, t in heads}:
                common[t] = common.get(t, 0) + 1
        titles = [t for _, t, _, _ in reads]
        for item, title, heads, md in reads:
            own = [t for lvl, t in heads if t and (common.get(t, 0) < 2 or len(reads) < 2)]
            h1 = next((t for lvl, t in heads if lvl == 1 and t in own), None)
            item["used_to_say"] = {"title": h1 or title, "page_title": title, "words": len(md.split()), "headings": own[:12],
                                   "opening": re.sub(r"\s+", " ", re.sub(r"[#*_>`|]", " ", re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", md)))[:600].strip()}
            if titles.count(title) > 1 and not h1:
                item["used_to_say"]["note"] = "This page shared its title with other dead pages; it may have been a template page."
        dead.sort(key=lambda d: ("used_to_say" not in d, bool(d.get("used_to_say", {}).get("note"))))
        linkers = []
        try:
            linkers = graph().referring(domain, limit=25, min_authority=100, skip_platforms=True)["referring_domains"]
        except Exception:
            pass
        return {"domain": domain, "checked": checked, "dead_pages": dead,
                "who_links_to_the_site": linkers,
                "next_step": "For each dead page: write a better version on your site, then run POST /v1/links/find with target set to the "
                             "dead page and site set to each linking domain to find the exact page and anchor to ask them to update.",
                "note": "Dead = the archive saw the page working; today it returns 404 or 410, its domain no longer resolves, or it redirects to the home page."}, credits, note

    async def run(job, account, kind, work):
        status, note = "completed", None
        try:
            async with asyncio.timeout(DEADLINE):
                result, credits, note = await work
            save(job, result=result, credits=credits)
        except TimeoutError:
            note = "Stopped at the 20-minute job deadline. Results found so far are kept."
        except asyncio.CancelledError:
            status = "cancelled"
        except Exception:
            logging.exception("link job failed job=%s", job)
            status, note = "failed", "The job stopped unexpectedly. Nothing more will be charged."
        finally:
            tasks.pop(job, None)
            if note and status == "completed":
                status = "stopped"
            h.db.execute("UPDATE link_jobs SET status=?,finished=?,note=COALESCE(?,note) WHERE id=? AND status='running'",
                         (status, time.time(), note, job))

    def begin(account, kind, options, work_for):
        busy = h.db.execute("SELECT COUNT(*) FROM link_jobs WHERE account=? AND status='running'", (account,)).fetchone()[0]
        if busy >= 2:
            return _error(429, "Two link jobs are already running on this account. Wait for one to finish.", "job_in_progress", retry_after=30)
        if h.billing.remaining(account) < 1:
            return _error(402, "Not enough credits", "insufficient_credits", credits_left=0, buy_url=h.site + "/#pricing")
        job = "links_" + secrets.token_hex(12)
        h.db.execute("DELETE FROM link_jobs WHERE created<?", (time.time() - KEEP_SECONDS,))
        h.db.execute("INSERT INTO link_jobs(id,account,kind,status,created,options) VALUES(?,?,?,'running',?,?)",
                     (job, account, kind, time.time(), json.dumps(options)))
        tasks[job] = asyncio.create_task(run(job, account, kind, work_for(job)))
        return web.json_response({"id": job, "kind": kind, "status": "running", "status_url": h.site + "/v1/links/jobs/" + job,
                                  "note": "Poll status_url. Each page read costs the usual 1 credit; failed pages are free. Results are kept for 7 days."})

    async def find_for(account, body):
        b = _fields(body, ["target", "site", "max_pages"], ["target", "site"])
        target, prefix = _target(b["target"])
        site = linkgraph.clean(b["site"])
        pages = _int(b, "max_pages", 20, 1, 100)
        return begin(account, "find", {"target": b["target"], "site": site, "max_pages": pages},
                     lambda job: finder(job, account, target, prefix, site, pages))

    async def dead_for(account, body):
        b = _fields(body, ["domain", "limit", "read_archive"], ["domain"])
        domain = linkgraph.clean(b["domain"])
        limit = _int(b, "limit", 100, 1, 500)
        read = _int(b, "read_archive", 10, 0, 50)
        return begin(account, "dead", {"domain": domain, "limit": limit, "read_archive": read},
                     lambda job: hunter(job, account, domain, limit, read))

    async def job_for(account, body):
        ident = body.get("id") if isinstance(body, dict) else None
        row = h.db.execute("SELECT id,kind,status,created,finished,options,progress,result,credits,note FROM link_jobs WHERE id=? AND account=?",
                           (ident if isinstance(ident, str) else "", account)).fetchone()
        if not row:
            return _error(404, "No such link job on this account.", "not_found")
        reply = {"id": row[0], "kind": row[1], "status": row[2], "created": row[3], "finished": row[4],
                 "options": json.loads(row[5] or "{}"), "progress": json.loads(row[6] or "null"),
                 "result": json.loads(row[7] or "null"), "credits_used": row[8], "credits_left": h.billing.remaining(account)}
        if row[9]:
            reply["note"] = row[9]
        return web.json_response(reply)

    async def cancel_for(account, body):
        ident = body.get("id") if isinstance(body, dict) else None
        h.db.execute("UPDATE link_jobs SET status='cancelled',finished=? WHERE id=? AND account=? AND status='running'",
                     (time.time(), ident, account))
        return await job_for(account, body)

    h.link_jobs = {"begin": begin, "save": save, "running": running, "error": _error}

    def route(fn):
        async def handler(request):
            body = await request.json() if request.can_read_body else {}
            return await fn(request["account"], body)
        return handler

    async def job_get(request):
        return await job_for(request["account"], {"id": request.match_info["job"]})

    async def job_delete(request):
        return await cancel_for(request["account"], {"id": request.match_info["job"]})

    for path, fn in (("authority", authority_for), ("referring-domains", referring_for), ("gap", gap_for),
                     ("competitors", competitors_for), ("find", find_for), ("dead", dead_for)):
        app.router.add_post("/v1/links/" + path, route(fn))
    app.router.add_get("/v1/links/jobs/{job}", job_get)
    app.router.add_get("/v1/seo/jobs/{job}", job_get)
    app.router.add_delete("/v1/links/jobs/{job}", job_delete)
    return {"/links/authority": authority_for, "/links/referring-domains": referring_for, "/links/gap": gap_for,
            "/links/competitors": competitors_for, "/links/find": find_for, "/links/dead": dead_for,
            "/links/job": job_for, "/links/cancel": cancel_for}
