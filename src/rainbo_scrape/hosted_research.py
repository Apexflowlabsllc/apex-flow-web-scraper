"""Hosted research-paper search backed by public Crossref metadata."""
import asyncio
import hashlib
import json
import os
import re
import time
from datetime import date
from urllib.parse import quote

import aiohttp
from aiohttp import web

from .billing import QuotaExceeded, cost_of

_CROSSREF_ROOT = "https://api.crossref.org/works"
_CROSSREF_MAILTO = "apexflowlabs@gmail.com"
_CACHE_TTL = 3600
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$", re.I)


class ResearchProviderError(Exception):
    def __init__(self, status):
        self.status = status


def _date(value):
    if not isinstance(value, dict):
        return None
    parts = value.get("date-parts")
    if not isinstance(parts, list) or not parts or not isinstance(parts[0], list) or not parts[0]:
        return None
    values = parts[0][:3]
    if not all(isinstance(part, int) and not isinstance(part, bool) for part in values):
        return None
    try:
        return date(*values).isoformat() if len(values) == 3 else "-".join(f"{part:02d}" if index else str(part)
                                                                          for index, part in enumerate(values))
    except ValueError:
        return None


def _paper(work, score=False):
    doi = work.get("DOI")
    if not isinstance(doi, str) or not _DOI_RE.fullmatch(doi):
        return None
    doi = doi.lower()
    titles = work.get("title") or []
    title = next((item.strip() for item in titles if isinstance(item, str) and item.strip()), "")
    if not title:
        return None
    authors = []
    for author in work.get("author") or []:
        if not isinstance(author, dict):
            continue
        name = author.get("name") or " ".join(filter(None, (author.get("given"), author.get("family"))))
        if isinstance(name, str) and name.strip():
            authors.append(name.strip())
    created = _date(work.get("created"))
    updated = _date(work.get("updated")) or _date(work.get("indexed"))
    result = {
        "paperId": doi,
        "primaryId": "doi:" + doi,
        "ids": {"doi": [doi]},
        "title": title,
        "authors": authors,
        "createdDate": created,
        "updateDate": updated,
        "categories": [value for value in (work.get("subject") or []) if isinstance(value, str)],
        "url": "https://doi.org/" + doi,
        "provider": "Crossref",
    }
    if score and isinstance(work.get("score"), (int, float)) and not isinstance(work.get("score"), bool):
        result["score"] = work["score"]
    return {key: value for key, value in result.items() if value is not None}


def _filter_values(query, name):
    values = []
    for raw in query.getall(name, []):
        values.extend(item.strip() for item in raw.split(",") if item.strip())
    if len(values) > 20 or any(len(value) > 120 for value in values):
        raise ValueError(f"{name} supports at most 20 values of 120 characters")
    return values


def _match_paper(paper, authors, categories, start, end):
    paper_authors = [name.casefold() for name in paper["authors"]]
    paper_categories = [name.casefold() for name in paper["categories"]]
    if any(not any(wanted.casefold() in name for name in paper_authors) for wanted in authors):
        return False
    if any(not any(wanted.casefold() in name for name in paper_categories) for wanted in categories):
        return False
    created = paper.get("createdDate")
    if start and (not created or created[:10] < start):
        return False
    if end and (not created or created[:10] > end):
        return False
    return True


async def _fetch_crossref(hosted, params, doi=None):
    db = hosted.db
    db.execute("BEGIN IMMEDIATE")
    try:
        db.execute("CREATE TABLE IF NOT EXISTS research_provider_state(provider TEXT PRIMARY KEY, next_allowed REAL NOT NULL)")
        row = db.execute("SELECT next_allowed FROM research_provider_state WHERE provider='crossref'").fetchone()
        now = time.time()
        slot = max(now, float(row[0]) if row else now)
        mailto = os.environ.get("CROSSREF_MAILTO", _CROSSREF_MAILTO).strip()
        interval = 0.36 if mailto else 1.05
        db.execute("INSERT INTO research_provider_state(provider,next_allowed) VALUES('crossref',?) "
                   "ON CONFLICT(provider) DO UPDATE SET next_allowed=excluded.next_allowed", (slot + interval,))
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise
    await asyncio.sleep(max(0, slot - now))
    path = "/" + quote(doi, safe="/") if doi else ""
    query = dict(params)
    if mailto:
        query["mailto"] = mailto
    headers = {"User-Agent": f"ApexFlowWebScraper/1.0 (https://webscraper.apexflowlabs.com; mailto:{mailto})"}
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        async with session.get(_CROSSREF_ROOT + path, params=query, allow_redirects=False) as response:
            if response.status != 200:
                raise ResearchProviderError(response.status)
            chunks, size = [], 0
            async for chunk in response.content.iter_chunked(65536):
                size += len(chunk)
                if size > _MAX_RESPONSE_BYTES:
                    raise ResearchProviderError(502)
                chunks.append(chunk)
    try:
        return json.loads(b"".join(chunks).decode("utf-8")).get("message")
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError) as exc:
        raise ResearchProviderError(502) from exc


def install(app, hosted, wrap, error):
    hosted.db.executescript("""
        CREATE TABLE IF NOT EXISTS research_paper_cache(
            cache_key TEXT PRIMARY KEY, payload TEXT NOT NULL, expires REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS research_provider_state(
            provider TEXT PRIMARY KEY, next_allowed REAL NOT NULL
        );
    """)

    async def search_papers(request):
        allowed = {"query", "k", "authors", "categories", "from", "to", "sort"}
        if set(request.query) - allowed:
            return error("Unsupported research search parameters: " + ", ".join(sorted(set(request.query) - allowed)))
        query = request.query.get("query", "").strip()
        if not query or len(query) > 500:
            return error("query is required and must be no longer than 500 characters")
        raw_k = request.query.get("k", "40")
        if not raw_k.isascii() or not raw_k.isdecimal() or not 1 <= int(raw_k) <= 500:
            return error("k must be an integer from 1 to 500")
        k = int(raw_k)
        authors, categories = _filter_values(request.query, "authors"), _filter_values(request.query, "categories")
        start, end = request.query.get("from"), request.query.get("to")
        try:
            if start:
                start = date.fromisoformat(start).isoformat()
            if end:
                end = date.fromisoformat(end).isoformat()
        except ValueError:
            return error("from and to must use YYYY-MM-DD dates")
        if start and end and start > end:
            return error("from must be on or before to")
        sort = request.query.get("sort", "relevance")
        if sort not in ("relevance", "date_desc"):
            return error("sort must be relevance or date_desc")

        filters = {"query": query, "k": k, "authors": authors, "categories": categories,
                   "from": start, "to": end, "sort": sort}
        cache_key = hashlib.sha256(json.dumps(filters, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        row = hosted.db.execute("SELECT payload FROM research_paper_cache WHERE cache_key=? AND expires>?",
                                (cache_key, time.time())).fetchone()
        try:
            hosted.billing.reserve(request["account"], cost_of("search"), kind="search")
        except QuotaExceeded as exc:
            return error(str(exc), "insufficient_credits", 402)
        try:
            if row:
                papers = json.loads(row[0])
            else:
                provider_params = {
                    "query.bibliographic": query,
                    "rows": min(1000, max(k, k * 3)),
                    "select": "DOI,title,author,created,updated,indexed,score,subject",
                    "sort": "created" if sort == "date_desc" else "score",
                    "order": "desc",
                }
                range_filters = []
                if start:
                    range_filters.append("from-created-date:" + start)
                if end:
                    range_filters.append("until-created-date:" + end)
                if range_filters:
                    provider_params["filter"] = ",".join(range_filters)
                message = await _fetch_crossref(hosted, provider_params)
                items = message.get("items", []) if isinstance(message, dict) else []
                papers = [paper for work in items if isinstance(work, dict)
                          for paper in [_paper(work, score=True)] if paper]
                if sort == "date_desc":
                    papers.sort(key=lambda paper: (paper.get("createdDate") or "", paper["paperId"]), reverse=True)
                hosted.db.execute("DELETE FROM research_paper_cache WHERE expires<=?", (time.time(),))
                hosted.db.execute("INSERT OR REPLACE INTO research_paper_cache(cache_key,payload,expires) VALUES(?,?,?)",
                                  (cache_key, json.dumps(papers, separators=(",", ":")), time.time() + _CACHE_TTL))
            papers = [paper for paper in papers if _match_paper(paper, authors, categories, start, end)][:k]
        except ResearchProviderError as exc:
            hosted.billing.refund(request["account"], cost_of("search"))
            if exc.status == 404:
                return error("Paper not found", "not_found", 404)
            response = error("Research metadata provider is temporarily unavailable", "research_provider_unavailable", 503)
            response.headers["Retry-After"] = "2"
            return response
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError):
            hosted.billing.refund(request["account"], cost_of("search"))
            response = error("Research metadata provider is temporarily unavailable", "research_provider_unavailable", 503)
            response.headers["Retry-After"] = "2"
            return response
        except BaseException:
            hosted.billing.refund(request["account"], cost_of("search"))
            raise
        return web.json_response({"success": True, "results": papers, "creditsUsed": cost_of("search")})

    async def get_paper(request):
        paper_id = request.match_info["paper_id"].strip()
        if paper_id.lower().startswith("doi:"):
            paper_id = paper_id[4:]
        if not _DOI_RE.fullmatch(paper_id):
            return error("paper_id must be a DOI returned by the Crossref research search endpoint")
        paper_id = paper_id.lower()
        cache_key = hashlib.sha256(("doi:" + paper_id).encode()).hexdigest()
        row = hosted.db.execute("SELECT payload FROM research_paper_cache WHERE cache_key=? AND expires>?",
                                (cache_key, time.time())).fetchone()
        try:
            hosted.billing.reserve(request["account"], cost_of("search"), kind="search")
        except QuotaExceeded as exc:
            return error(str(exc), "insufficient_credits", 402)
        try:
            paper = json.loads(row[0]) if row else None
            if paper is None:
                work = await _fetch_crossref(hosted, {"select": "DOI,title,author,created,updated,indexed,subject"}, paper_id)
                paper = _paper(work if isinstance(work, dict) else {})
                if paper is None:
                    raise ResearchProviderError(404)
                hosted.db.execute("INSERT OR REPLACE INTO research_paper_cache(cache_key,payload,expires) VALUES(?,?,?)",
                                  (cache_key, json.dumps(paper, separators=(",", ":")), time.time() + _CACHE_TTL))
        except ResearchProviderError as exc:
            hosted.billing.refund(request["account"], cost_of("search"))
            if exc.status == 404:
                return error("Paper not found", "not_found", 404)
            response = error("Research metadata provider is temporarily unavailable", "research_provider_unavailable", 503)
            response.headers["Retry-After"] = "2"
            return response
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError):
            hosted.billing.refund(request["account"], cost_of("search"))
            response = error("Research metadata provider is temporarily unavailable", "research_provider_unavailable", 503)
            response.headers["Retry-After"] = "2"
            return response
        except BaseException:
            hosted.billing.refund(request["account"], cost_of("search"))
            raise
        return web.json_response({"success": True, "paper": paper, "creditsUsed": cost_of("search")})

    app.router.add_get("/v2/search/research/papers", wrap(search_papers))
    app.router.add_get("/v2/search/research/papers/{paper_id:.+}", wrap(get_paper))
