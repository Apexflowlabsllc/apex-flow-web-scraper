"""Jobs package: a company's open roles from the job board it publishes for anyone to read.

Greenhouse and Lever each serve a company's public job board as a documented JSON feed meant for
embedding on other sites, so this is the company's own listing, read the way it is offered. (Ashby's feed is left out: its robots.txt asks automated readers to stay away.) It answers what
people take to LinkedIn or Indeed for ("what is this company hiring for?") without reading either.
"""
import json
import re

from aiohttp import web

PROVIDERS = [
    ("greenhouse", "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=false"),
    ("lever", "https://api.lever.co/v0/postings/{slug}?mode=json"),
]
PACKAGE = {"id": "jobs", "endpoint": "/v1/packages/jobs", "credits": 1,
           "what": "A company's open jobs from its own public job board (Greenhouse or Lever): title, team, location, link, and pay where the company publishes it.",
           "input": {"company": "\"stripe\" or the board name", "query": "optional words to match in the title", "location": "optional", "limit": "1-100, default 25"},
           "source": "The company's public job board feed"}


def slug(company):
    if not isinstance(company, str) or not 2 <= len(company.strip()) <= 60:
        raise ValueError("company must be a company or job board name such as \"stripe\"")
    return re.sub(r"[^a-z0-9-]", "", company.strip().lower().replace(" ", ""))


def parse(provider, text):
    data = json.loads(text)
    out = []
    if provider == "greenhouse":
        for j in data.get("jobs", []):
            out.append({"title": j.get("title"), "location": (j.get("location") or {}).get("name"), "team": ", ".join(d.get("name", "") for d in j.get("departments", []) or []) or None,
                        "url": j.get("absolute_url"), "updated": j.get("updated_at")})
    elif provider == "lever":
        for j in data if isinstance(data, list) else []:
            c = j.get("categories") or {}
            pay = j.get("salaryRange") or None
            out.append({"title": j.get("text"), "location": c.get("location"), "team": c.get("team") or c.get("department"), "url": j.get("hostedUrl"),
                        "updated": j.get("createdAt"), "pay": {"min": pay.get("min"), "max": pay.get("max"), "currency": pay.get("currency"), "interval": pay.get("interval")} if pay else None})
    else:
        for j in data.get("jobs", []):
            comp = (j.get("compensation") or {}).get("compensationTierSummary")
            out.append({"title": j.get("title"), "location": j.get("location"), "team": j.get("department") or j.get("team"), "url": j.get("jobUrl"),
                        "updated": j.get("publishedAt"), "pay": comp or None, "remote": j.get("isRemote")})
    return [{k: v for k, v in x.items() if v not in (None, "", [])} for x in out if x.get("title") and str(x.get("url", "")).startswith("https://")]


def pick(jobs, query=None, location=None, limit=25):
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit must be a whole number from 1 to 100")
    for name, value in (("query", query), ("location", location)):
        if value is not None and (not isinstance(value, str) or len(value) > 80):
            raise ValueError(name + " must be a short text")
    words = [w for w in re.findall(r"[a-z0-9]+", (query or "").lower())]
    place = (location or "").lower().strip()
    hits = [j for j in jobs if all(w in j["title"].lower() for w in words) and (not place or place in str(j.get("location", "")).lower())]
    return hits[:limit], len(hits)


def install(h, app, source):
    from .billing import QuotaExceeded
    from .hosted_features import admit_attempt

    async def jobs_for(account, body):
        if not isinstance(body, dict) or set(body) - {"company", "query", "location", "limit"}:
            raise ValueError("Use company and optional query, location, limit")
        board = slug(body.get("company"))
        pick([], body.get("query"), body.get("location"), body.get("limit", 25))   # validate before charging
        admit_attempt(h, account)
        try:
            h.billing.reserve(account, 1)
        except QuotaExceeded:
            return web.json_response({"error": "Not enough credits", "code": "insufficient_credits", "retryable": False,
                                      "credits_left": h.billing.remaining(account), "buy_url": h.site + "/#pricing"}, status=402)
        found = None
        for provider, pattern in PROVIDERS:
            try:
                text = await source(pattern.format(slug=board))
                listing = parse(provider, text)
            except Exception:
                continue
            if listing or provider == "greenhouse" and '"jobs"' in text:
                found = (provider, pattern.format(slug=board), listing)
                break
        if not found:
            h.billing.refund(account, 1)
            return web.json_response({"credits_used": 0, "credits_left": h.billing.remaining(account),
                                      "result": {"company_asked": body["company"], "count": 0, "jobs": [],
                                                 "note": "No public Greenhouse or Lever board answered to this name. Try the name in the company's job board address, such as \"stripe\" in boards.greenhouse.io/stripe. Nothing was charged."}})
        provider, url, listing = found
        chosen, total = pick(listing, body.get("query"), body.get("location"), body.get("limit", 25))
        result = {"company": board, "board": provider, "open_jobs_total": len(listing), "matching": total, "count": len(chosen), "jobs": chosen,
                  "source": {"name": f"{board} public job board on {provider.title()}", "url": url,
                             "attribution": "Listings from the company's own public job board feed, which it publishes for anyone to read and embed."}}
        if not chosen:
            h.billing.refund(account, 1)
        return web.json_response({"credits_used": 1 if chosen else 0, "credits_left": h.billing.remaining(account), "result": result})

    async def handler(request):
        return await jobs_for(request["account"], await request.json())

    app.router.add_post("/v1/packages/jobs", handler)
    return {"/packages/jobs": jobs_for}
