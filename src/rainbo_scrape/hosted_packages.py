"""Data packages: answers from a publisher's own public data files and feeds.

Some large sites are hard to read page by page but publish the data people want in an official, public form.
A package reads that form and returns a small, typed answer with the source and its attribution.
Nothing here disguises a request or reads a page a site has walled off.
"""
import csv
import io
import time

from aiohttp import web

from .billing import QuotaExceeded

CREDITS = 1
CACHE_SECONDS = 6 * 3600
ZILLOW = {
    "home_values": ("https://files.zillowstatic.com/research/public_csvs/zhvi/Metro_zhvi_uc_sfrcondo_tier_0.33_0.67_sm_sa_month.csv",
                    "Zillow Home Value Index (typical home value, all homes, smoothed and seasonally adjusted), US dollars"),
    "rents": ("https://files.zillowstatic.com/research/public_csvs/zori/Metro_zori_uc_sfrcondomfr_sm_month.csv",
              "Zillow Observed Rent Index (typical asking rent, all homes plus multifamily, smoothed), US dollars a month"),
}
NPR_TOPICS = {"news": 1001, "world": 1004, "business": 1006, "science": 1007, "politics": 1014, "technology": 1019, "health": 1128}
PACKAGES = [
    {"id": "zillow", "endpoint": "/v1/packages/zillow", "credits": CREDITS,
     "what": "Typical home value or rent for a US metro area, the one-year change and a monthly series.",
     "input": {"region": "Metro name, for example \"Sacramento, CA\"", "metric": sorted(ZILLOW), "months": "1-120, default 24"},
     "source": "Zillow Research public data files"},
    {"id": "npr", "endpoint": "/v1/packages/npr", "credits": CREDITS,
     "what": "Latest NPR headlines for a topic, with links you can pass to /v1/scrape.",
     "input": {"topic": sorted(NPR_TOPICS), "limit": "1-25, default 10"},
     "source": "NPR public news feeds"},
]


def zillow_market(csv_text, region, metric="home_values", months=24):
    """One metro's latest value, one-year change and recent series from a Zillow Research file."""
    if metric not in ZILLOW:
        raise ValueError("metric must be one of: " + ", ".join(sorted(ZILLOW)))
    if not isinstance(region, str) or not 2 <= len(region.strip()) <= 80:
        raise ValueError("region must be a metro name such as \"Sacramento, CA\"")
    if isinstance(months, bool) or not isinstance(months, int) or not 1 <= months <= 120:
        raise ValueError("months must be a whole number from 1 to 120")
    rows = csv.reader(io.StringIO(csv_text))
    header = next(rows)
    name_at, type_at, state_at = header.index("RegionName"), header.index("RegionType"), header.index("StateName")
    dates = [(i, h) for i, h in enumerate(header) if len(h) == 10 and h[4] == "-" and h[7] == "-"]
    wanted = region.strip().lower()
    exact, partial = None, []
    for row in rows:
        if len(row) <= name_at:
            continue
        name = row[name_at].lower()
        if name == wanted:
            exact = row
            break
        if wanted in name:
            partial.append(row)
    row = exact or (partial[0] if len(partial) == 1 else None)
    url, measure = ZILLOW[metric]
    source = {"name": "Zillow Research", "url": url, "measure": measure,
              "attribution": "Data provided by Zillow Research (zillow.com/research/data). Subject to Zillow's terms of use."}
    if row is None:
        return {"region_asked": region, "metric": metric, "matches": [r[name_at] for r in partial[:20]], "source": source,
                "note": "No single metro matched. Ask again with one of the names in matches."}
    points = []
    for index, date in dates:
        if index < len(row) and row[index] not in ("", None):
            try:
                points.append((date, round(float(row[index]), 2)))
            except ValueError:
                continue
    if not points:
        return {"region_asked": region, "metric": metric, "matches": [], "source": source, "note": "This metro has no values in the file."}
    latest_date, latest = points[-1]
    year_ago = next((value for date, value in reversed(points)
                     if date[:4] == str(int(latest_date[:4]) - 1) and date[5:7] == latest_date[5:7]), None)
    return {"region": row[name_at], "region_type": row[type_at], "state": row[state_at] or None, "metric": metric,
            "latest": {"date": latest_date, "value": latest},
            "change_1y_pct": round((latest - year_ago) / year_ago * 100, 1) if year_ago else None,
            "series": [{"date": date, "value": value} for date, value in points[-months:]], "source": source}


def npr_news(xml_text, topic="news", limit=10):
    """Headlines from one NPR topic feed."""
    from defusedxml import ElementTree
    if topic not in NPR_TOPICS:
        raise ValueError("topic must be one of: " + ", ".join(sorted(NPR_TOPICS)))
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 25:
        raise ValueError("limit must be a whole number from 1 to 25")
    stories = []
    for item in ElementTree.fromstring(xml_text.encode() if isinstance(xml_text, str) else xml_text).iter("item"):
        link = (item.findtext("link") or "").strip()
        if not link.startswith("https://"):
            continue
        stories.append({"title": (item.findtext("title") or "").strip(), "url": link,
                        "summary": (item.findtext("description") or "").strip()[:500],
                        "published": (item.findtext("pubDate") or "").strip() or None})
        if len(stories) == limit:
            break
    return {"topic": topic, "count": len(stories), "stories": stories,
            "source": {"name": "NPR", "url": f"https://feeds.npr.org/{NPR_TOPICS[topic]}/rss.xml",
                       "attribution": "Headlines and summaries from NPR's public news feed (npr.org)."}}


async def fetch_text(url):
    """Download one official source file through the same guarded fetcher every scrape uses."""
    from .network import Fetcher
    from .settings import Settings
    from .store import Store
    settings = Settings()
    async with Fetcher(settings, Store(settings)) as fetcher:
        response = await fetcher.get(url, max_age=CACHE_SECONDS * 1000)
    if response["status"] != 200:
        raise OSError("source returned HTTP %s" % response["status"])
    return response["body"].decode("utf-8", errors="replace")


def install(h, app, loader=None):
    load = loader or fetch_text
    cache = {}

    async def source(url):
        hit = cache.get(url)
        if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
            return hit[1]
        text = await load(url)
        cache[url] = (time.monotonic(), text)
        return text

    async def answer(account, url, build):
        """Reserve one credit, build the answer, and return the credit unless a real answer was produced."""
        from .hosted_features import admit_attempt
        admit_attempt(h, account)
        try:
            h.billing.reserve(account, CREDITS)
        except QuotaExceeded:
            return web.json_response({"error": "Not enough credits", "code": "insufficient_credits", "retryable": False,
                                      "credits_left": h.billing.remaining(account), "buy_url": h.site + "/#pricing"}, status=402)
        try:
            try:
                text = await source(url)
            except ValueError:
                raise
            except Exception:
                h.billing.refund(account, CREDITS)
                return web.json_response({"error": "The official source did not answer. Nothing was charged; retry shortly.",
                                          "code": "source_failed", "retryable": True, "retry_after": 30}, status=502)
            result = build(text)
        except BaseException:
            h.billing.refund(account, CREDITS)
            raise
        answered = "matches" not in result and result.get("count", 1) != 0
        if not answered:
            h.billing.refund(account, CREDITS)
        return web.json_response({"credits_used": CREDITS if answered else 0, "credits_left": h.billing.remaining(account), "result": result})

    async def zillow_for(account, body):
        if not isinstance(body, dict) or set(body) - {"region", "metric", "months"}:
            raise ValueError("Use region, and optional metric and months")
        metric, months, region = body.get("metric", "home_values"), body.get("months", 24), body.get("region")
        zillow_market("RegionName,RegionType,StateName\n", region, metric, months)  # validates before any credit is reserved
        return await answer(account, ZILLOW[metric][0], lambda text: zillow_market(text, region, metric, months))

    async def npr_for(account, body):
        if not isinstance(body, dict) or set(body) - {"topic", "limit"}:
            raise ValueError("Use optional topic and limit")
        topic, limit = body.get("topic", "news"), body.get("limit", 10)
        npr_news("<rss/>", topic, limit)  # validates before any credit is reserved
        return await answer(account, f"https://feeds.npr.org/{NPR_TOPICS[topic]}/rss.xml", lambda text: npr_news(text, topic, limit))

    async def listing_for(account, body=None):
        return web.json_response({"packages": PACKAGES})

    async def zillow(request):
        return await zillow_for(request["account"], await request.json())

    async def npr(request):
        return await npr_for(request["account"], await request.json())

    async def listing(request):
        return await listing_for(request["account"])

    app.router.add_get("/v1/packages", listing)
    app.router.add_post("/v1/packages/zillow", zillow)
    app.router.add_post("/v1/packages/npr", npr)
    return {"/packages": listing_for, "/packages/zillow": zillow_for, "/packages/npr": npr_for}
