"""Data packages: answers from a publisher's own public data files and feeds.

A package reads an official public source and returns a small, typed answer with the source and its
attribution. Sources are chosen so that automated reading and redistribution are permitted:
U.S. government data is in the public domain, and NPR publishes its feeds for syndication.
Nothing here disguises a request or reads a page a site has walled off.
"""
import csv
import io
import time

from aiohttp import web

from .billing import QuotaExceeded

CREDITS = 1
CACHE_SECONDS = 6 * 3600
FHFA_METRO = "https://www.fhfa.gov/hpi/download/quarterly_datasets/hpi_at_metro.csv"
NPR_TOPICS = {"news": 1001, "world": 1004, "business": 1006, "science": 1007, "politics": 1014, "technology": 1019, "health": 1128}
PACKAGES = [
    {"id": "housing", "endpoint": "/v1/packages/housing", "credits": CREDITS,
     "what": "House price index for a US metro area: the latest quarter, one-year and five-year change, and a quarterly series.",
     "input": {"region": "Metro name, for example \"Sacramento, CA\"", "quarters": "1-120, default 8"},
     "source": "Federal Housing Finance Agency House Price Index (U.S. government, public domain)"},
    {"id": "npr", "endpoint": "/v1/packages/npr", "credits": CREDITS,
     "what": "Latest NPR headlines for a topic, with links you can pass to /v1/scrape.",
     "input": {"topic": sorted(NPR_TOPICS), "limit": "1-25, default 10"},
     "source": "NPR public news feeds"},
]


def _matches(wanted_city, wanted_state, name):
    """FHFA names join cities and states with hyphens: "Sacramento-Roseville-Folsom, CA", "Kansas City, MO-KS"."""
    cities, _, states = name.rpartition(",")
    if not cities:
        cities, states = name, ""
    if wanted_state and wanted_state not in [s.strip().upper() for s in states.replace("(MSAD)", "").split("-")]:
        return False
    return wanted_city in cities.lower()


def house_prices(csv_text, region, quarters=8):
    """One metro's house price index from the FHFA all-transactions metro file: latest quarter, changes and a series."""
    if not isinstance(region, str) or not 2 <= len(region.strip()) <= 80:
        raise ValueError("region must be a metro name such as \"Sacramento, CA\"")
    if isinstance(quarters, bool) or not isinstance(quarters, int) or not 1 <= quarters <= 120:
        raise ValueError("quarters must be a whole number from 1 to 120")
    wanted = region.strip().lower()
    city, _, state = wanted.partition(",")
    city, state = city.strip(), state.strip().upper()
    found = {}
    exact = None
    for row in csv.reader(io.StringIO(csv_text)):
        if len(row) < 5 or not row[2].isdigit():
            continue
        name = row[0]
        if name.lower() == wanted:
            exact = name
        elif name not in found and not _matches(city, state, name):
            continue
        try:
            value = float(row[4])
        except ValueError:
            found.setdefault(name, [])
            continue
        found.setdefault(name, []).append((int(row[2]), int(row[3]), round(value, 2), row[1]))
    source = {"name": "Federal Housing Finance Agency", "url": FHFA_METRO, "measure": "FHFA House Price Index, all-transactions, not seasonally adjusted",
              "attribution": "Source: Federal Housing Finance Agency House Price Index. U.S. government data, public domain."}
    names = [exact] if exact else sorted(found)
    if len(names) != 1:
        return {"region_asked": region, "matches": names[:20], "source": source,
                "note": "No single metro matched. Ask again with one of the names in matches."}
    points = sorted(found[names[0]])
    if not points:
        return {"region_asked": region, "matches": [], "source": source, "note": "This metro has no index values in the file."}
    year, quarter, latest, cbsa = points[-1]
    by_period = {(y, q): v for y, q, v, _ in points}

    def change(years):
        earlier = by_period.get((year - years, quarter))
        return round((latest - earlier) / earlier * 100, 1) if earlier else None
    return {"region": names[0], "cbsa": cbsa, "metric": "house_price_index",
            "latest": {"period": f"{year}-Q{quarter}", "index": latest},
            "change_1y_pct": change(1), "change_5y_pct": change(5),
            "series": [{"period": f"{y}-Q{q}", "index": v} for y, q, v, _ in points[-quarters:]],
            "note": "An index, not a dollar price: compare periods with each other. Prices in this metro rose or fell by the percentages shown.",
            "source": source}


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

    async def housing_for(account, body):
        if not isinstance(body, dict) or set(body) - {"region", "quarters"}:
            raise ValueError("Use region and optional quarters")
        region, quarters = body.get("region"), body.get("quarters", 8)
        house_prices("", region, quarters)  # validates before any credit is reserved
        return await answer(account, FHFA_METRO, lambda text: house_prices(text, region, quarters))

    async def npr_for(account, body):
        if not isinstance(body, dict) or set(body) - {"topic", "limit"}:
            raise ValueError("Use optional topic and limit")
        topic, limit = body.get("topic", "news"), body.get("limit", 10)
        npr_news("<rss/>", topic, limit)  # validates before any credit is reserved
        return await answer(account, f"https://feeds.npr.org/{NPR_TOPICS[topic]}/rss.xml", lambda text: npr_news(text, topic, limit))

    async def listing_for(account, body=None):
        return web.json_response({"packages": PACKAGES})

    async def housing(request):
        return await housing_for(request["account"], await request.json())

    async def npr(request):
        return await npr_for(request["account"], await request.json())

    async def listing(request):
        return await listing_for(request["account"])

    app.router.add_get("/v1/packages", listing)
    app.router.add_post("/v1/packages/housing", housing)
    app.router.add_post("/v1/packages/npr", npr)
    return {"/packages": listing_for, "/packages/housing": housing_for, "/packages/npr": npr_for}
