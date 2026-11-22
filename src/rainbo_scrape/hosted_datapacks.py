"""Answer packages for the questions people take to Zillow and Glassdoor, built from data that may be resold.

  rent      HUD Fair Market Rents FY2026 by bedroom count, every US metro and county area (public domain)
  pay       BLS Occupational Employment and Wage Statistics, May 2024: pay by job in every US metro (public domain)
  mortgage  Freddie Mac Primary Mortgage Market Survey, weekly 30- and 15-year rates (used with attribution)
  home      one call that joins the house price trend, rents and mortgage rates for a metro

The rent and pay tables ship with the engine as compressed files built by scripts/build-package-data.py, so an
answer needs no outside call. Mortgage rates are read from Freddie Mac's published file and cached.
"""
import csv
import gzip
import io
import re
from functools import lru_cache
from pathlib import Path

from aiohttp import web

DATA = Path(__file__).with_name("data")
PMMS = "https://www.freddiemac.com/pmms/docs/PMMS_history.csv"
RENT_SOURCE = {"name": "U.S. Department of Housing and Urban Development, Fair Market Rents FY2026", "url": "https://www.huduser.gov/portal/datasets/fmr.html",
               "attribution": "Source: HUD Fair Market Rents, FY2026. U.S. government data, public domain.",
               "measure": "Gross rent (rent plus utilities) at the 40th percentile of standard-quality units recently rented in the area"}
PAY_SOURCE = {"name": "U.S. Bureau of Labor Statistics, Occupational Employment and Wage Statistics, May 2024", "url": "https://www.bls.gov/oes/",
              "attribution": "Source: BLS OEWS, May 2024. U.S. government data, public domain.",
              "measure": "Annual wages from an employer survey of about 1.1 million establishments"}
MORTGAGE_SOURCE = {"name": "Freddie Mac Primary Mortgage Market Survey", "url": "https://www.freddiemac.com/pmms",
                   "attribution": "Source: Freddie Mac, Primary Mortgage Market Survey. Used with attribution."}
STOP = {"and", "of", "the", "a", "an", "all", "other", "workers", "worker"}


def _words(text):
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOP]


BOROUGHS = {"brooklyn": "kings", "manhattan": "new york", "queens": "queens", "the bronx": "bronx", "staten island": "richmond"}


def _place(region):
    if not isinstance(region, str) or not 2 <= len(region.strip()) <= 80:
        raise ValueError("region must be a place such as \"Sacramento, CA\"")
    city, _, state = region.strip().partition(",")
    city = city.strip().lower()
    return BOROUGHS.get(city, city), state.strip().upper()


@lru_cache(maxsize=1)
def _rents():
    with gzip.open(DATA / "rents_fy2026.csv.gz", "rt", encoding="utf-8") as f:
        return list(csv.DictReader(f))


@lru_cache(maxsize=1)
def _wages():
    with gzip.open(DATA / "wages_may2024.csv.gz", "rt", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    areas = {}
    for r in rows:
        areas.setdefault(r["area_title"], []).append(r)
    return areas


def _area_match(names, city, state):
    """Names look like "Sacramento-Roseville-Folsom, CA" or "Sacramento--Roseville--Arden-Arcade, CA HUD Metro FMR Area"."""
    exact, loose = [], []
    for name in names:
        head, _, tail = name.partition(",")
        states = re.findall(r"\b[A-Z]{2}\b", tail)
        if state and state not in states:
            continue
        parts = [p.strip().lower() for p in re.split(r"-+", head)]
        if city in parts:
            exact.append(name)
        elif city in head.lower():
            loose.append(name)
    return exact or loose


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return v or None


def rent(region):
    city, state = _place(region)
    rows = _rents()
    county = [r["area"] for r in rows if (not state or r["state"] == state)
              and any(city == c.lower().replace(" county", "").strip() for c in r["counties"].split("|"))]
    names = county if city in BOROUGHS.values() and county else _area_match([r["area"] for r in rows], city, state)
    if not names:   # a county or town name: "Folsom, CA" -> the area that lists it
        names = [r["area"] for r in rows if (not state or r["state"] == state) and any(city == c.lower().replace(" county", "").strip() or city in c.lower() for c in r["counties"].split("|"))]
    if not names:   # a secondary city of a metro: "Folsom, CA" -> "Sacramento-Roseville-Folsom, CA" -> Sacramento's rent area
        metro = _area_match(list(_wages()), city, state)
        if metro:
            lead = re.split(r"-+", metro[0].partition(",")[0])[0].strip().lower()
            names = _area_match([r["area"] for r in rows], lead, state)
    names = sorted(set(names), key=lambda n: -next(_int(r["pop2023"]) or 0 for r in rows if r["area"] == n))
    if not names:
        return {"region_asked": region, "matches": [], "source": RENT_SOURCE, "note": "No HUD rent area matched. Try \"City, ST\"."}
    r = next(x for x in rows if x["area"] == names[0])
    return {"region": names[0], "state": r["state"], "metro": r["metro"] == "1", "year": "FY2026",
            "monthly_rent": {"studio": _int(r["fmr_0"]), "1_bedroom": _int(r["fmr_1"]), "2_bedroom": _int(r["fmr_2"]), "3_bedroom": _int(r["fmr_3"]), "4_bedroom": _int(r["fmr_4"])},
            "counties": r["counties"].split("|")[:12], "other_matches": names[1:6], "source": RENT_SOURCE}


def pay(job, region, limit=5):
    if not isinstance(job, str) or not 2 <= len(job.strip()) <= 80:
        raise ValueError("job must be a job title such as \"registered nurse\"")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
        raise ValueError("limit must be a whole number from 1 to 20")
    city, state = _place(region)
    areas = _wages()
    names = _area_match(list(areas), city, state)
    if len(names) != 1:
        return {"region_asked": region, "matches": names[:20], "source": PAY_SOURCE, "note": "Ask again with one of the metro names in matches." if names else "No metro matched. Try \"City, ST\"."}
    want = set(_words(job))
    scored = []
    for r in areas[names[0]]:
        have = set(_words(r["occ_title"]))
        hit = len(want & have) + sum(0.5 for w in want for h in have if w != h and (h.startswith(w) or w.startswith(h)) and min(len(w), len(h)) >= 4)
        if hit:
            scored.append((-hit / len(want), r["o_group"] != "detailed", -(_int(r["tot_emp"]) or 0) if isinstance(_int(r["tot_emp"]), int) else 0, r))
    scored.sort(key=lambda t: t[:3])
    jobs = [{"title": r["occ_title"], "code": r["occ_code"], "employed": _int(r["tot_emp"]),
             "annual": {"median": _int(r["a_median"]), "mean": _int(r["a_mean"]), "p10": _int(r["a_pct10"]), "p25": _int(r["a_pct25"]), "p75": _int(r["a_pct75"]), "p90": _int(r["a_pct90"])},
             "hourly_median": r["h_median"] or None} for *_, r in scored[:limit]]
    everyone = next((r for r in areas[names[0]] if r["occ_code"] == "00-0000"), None)
    return {"region": names[0], "job_asked": job, "count": len(jobs), "jobs": jobs,
            "all_jobs_median": _int(everyone["a_median"]) if everyone else None, "period": "May 2024",
            "note": "Values marked 239200+ are at or above the survey's top band. Empty values were not published for this metro.",
            "source": PAY_SOURCE}


def mortgage(csv_text):
    rows = [r for r in csv.DictReader(io.StringIO(csv_text)) if r.get("pmms30")]
    if not rows:
        return {"count": 0, "source": MORTGAGE_SOURCE}
    last = rows[-1]
    from datetime import datetime
    when = datetime.strptime(last["date"], "%m/%d/%Y")
    year_ago = min(rows, key=lambda r: abs((datetime.strptime(r["date"], "%m/%d/%Y") - when).days + 364))

    def f(v):
        return float(v) if v else None
    return {"week_of": when.date().isoformat(), "rate_30y_fixed": f(last["pmms30"]), "rate_15y_fixed": f(last.get("pmms15")),
            "year_ago": {"week_of": datetime.strptime(year_ago["date"], "%m/%d/%Y").date().isoformat(), "rate_30y_fixed": f(year_ago["pmms30"]), "rate_15y_fixed": f(year_ago.get("pmms15"))},
            "last_8_weeks": [{"week_of": datetime.strptime(r["date"], "%m/%d/%Y").date().isoformat(), "rate_30y_fixed": f(r["pmms30"])} for r in rows[-8:]],
            "note": "National average rates for conforming loans, published weekly.", "source": MORTGAGE_SOURCE}


def install(h, app, house_prices_for, source):
    """house_prices_for(region) -> housing answer dict; source(url) -> text (cached). Returns MCP operations."""
    from .billing import QuotaExceeded
    from .hosted_features import admit_attempt

    async def charge(account, build):
        admit_attempt(h, account)
        try:
            h.billing.reserve(account, 1)
        except QuotaExceeded:
            return web.json_response({"error": "Not enough credits", "code": "insufficient_credits", "retryable": False,
                                      "credits_left": h.billing.remaining(account), "buy_url": h.site + "/#pricing"}, status=402)
        try:
            result = await build()
        except ValueError:
            h.billing.refund(account, 1)
            raise
        except Exception:
            h.billing.refund(account, 1)
            return web.json_response({"error": "An official source did not answer. Nothing was charged; retry shortly.",
                                      "code": "source_failed", "retryable": True, "retry_after": 30}, status=502)
        answered = "matches" not in result and result.get("count", 1) != 0
        if not answered:
            h.billing.refund(account, 1)
        return web.json_response({"credits_used": 1 if answered else 0, "credits_left": h.billing.remaining(account), "result": result})

    def fields(body, allowed):
        if not isinstance(body, dict) or set(body) - set(allowed):
            raise ValueError("Use only: " + ", ".join(allowed))
        return body

    async def rent_for(account, body):
        b = fields(body, ["region"])
        _place(b.get("region"))
        return await charge(account, lambda: _done(rent(b["region"])))

    async def pay_for(account, body):
        b = fields(body, ["job", "region", "limit"])
        _place(b.get("region"))
        if not isinstance(b.get("job"), str) or not 2 <= len(b["job"].strip()) <= 80:
            raise ValueError("job must be a job title such as \"registered nurse\"")
        return await charge(account, lambda: _done(pay(b["job"], b["region"], b.get("limit", 5))))

    async def mortgage_for(account, body=None):
        fields(body or {}, [])

        async def build():
            return mortgage(await source(PMMS))
        return await charge(account, build)

    async def home_for(account, body):
        b = fields(body, ["region"])
        _place(b.get("region"))

        async def build():
            prices = await house_prices_for(b["region"])
            out = {"region_asked": b["region"], "house_prices": prices, "rent": rent(b["region"])}
            try:
                out["mortgage_rates"] = mortgage(await source(PMMS))
            except Exception:
                out["mortgage_rates"] = {"error": "Freddie Mac did not answer; the rest of this answer is complete."}
            if "matches" in prices and "matches" in out["rent"]:
                out["matches"] = prices["matches"]
            out["note"] = "House prices are an index (compare periods); rents are monthly dollars by bedroom count; rates are national weekly averages."
            return out
        return await charge(account, build)

    def route(fn):
        async def handler(request):
            body = await request.json() if request.can_read_body else {}
            return await fn(request["account"], body)
        return handler

    app.router.add_post("/v1/packages/rent", route(rent_for))
    app.router.add_post("/v1/packages/pay", route(pay_for))
    app.router.add_post("/v1/packages/mortgage-rates", route(mortgage_for))
    app.router.add_post("/v1/packages/home", route(home_for))
    return {"/packages/rent": rent_for, "/packages/pay": pay_for, "/packages/mortgage-rates": mortgage_for, "/packages/home": home_for}


async def _done(value):
    return value


PACKAGES = [
    {"id": "home", "endpoint": "/v1/packages/home", "credits": 1,
     "what": "Everything about a US metro's housing in one answer: house price trend (1- and 5-year change), monthly rents by bedroom count, and this week's mortgage rates.",
     "input": {"region": "\"Sacramento, CA\""}, "source": "FHFA, HUD and Freddie Mac"},
    {"id": "rent", "endpoint": "/v1/packages/rent", "credits": 1,
     "what": "Monthly rent for a studio to a 4-bedroom in any US metro or county area (FY2026).",
     "input": {"region": "\"Austin, TX\" or a county or town"}, "source": RENT_SOURCE["name"]},
    {"id": "pay", "endpoint": "/v1/packages/pay", "credits": 1,
     "what": "Pay for a job in a US metro: median, mean and 10th-90th percentile annual pay, and how many people do it.",
     "input": {"job": "\"software developer\"", "region": "\"Seattle, WA\"", "limit": "1-20, default 5"}, "source": PAY_SOURCE["name"]},
    {"id": "mortgage-rates", "endpoint": "/v1/packages/mortgage-rates", "credits": 1,
     "what": "This week's average 30- and 15-year fixed mortgage rates, a year ago, and the last 8 weeks.",
     "input": {}, "source": MORTGAGE_SOURCE["name"]},
]
