"""Hosted research-paper search backed by public scholarly metadata."""
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
_OPENALEX_ROOT = "https://api.openalex.org/works"
_OPENALEX_SELECT = "id,doi,title,authorships,created_date,updated_date,publication_date,topics,cited_by_count,referenced_works,related_works,relevance_score,is_retracted"
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


def _openalex_paper(work):
    doi_url = work.get("doi")
    if not isinstance(doi_url, str):
        return None
    match = re.fullmatch(r"https?://doi\.org/(10\.\d{4,9}/\S+)", doi_url, re.I)
    if not match:
        return None
    doi = match.group(1).lower()
    title = work.get("title")
    if not isinstance(title, str) or not title.strip():
        return None
    authors = []
    for authorship in work.get("authorships") or []:
        author = authorship.get("author") if isinstance(authorship, dict) else None
        name = author.get("display_name") if isinstance(author, dict) else None
        if isinstance(name, str) and name.strip():
            authors.append(name.strip())
    topics = [topic.get("display_name") for topic in work.get("topics") or []
              if isinstance(topic, dict) and isinstance(topic.get("display_name"), str)]
    openalex_id = work.get("id")
    if not isinstance(openalex_id, str) or not re.fullmatch(r"https://openalex\.org/W\d+", openalex_id):
        return None
    references = [value for value in (work.get("referenced_works") or [])[:100]
                  if isinstance(value, str) and re.fullmatch(r"https://openalex\.org/W\d+", value)]
    related = [value for value in (work.get("related_works") or [])[:20]
               if isinstance(value, str) and re.fullmatch(r"https://openalex\.org/W\d+", value)]
    result = {
        "paperId": doi,
        "primaryId": "doi:" + doi,
        "ids": {"doi": [doi], "openalex": [openalex_id.rsplit("/", 1)[-1]]},
        "openAlexId": openalex_id,
        "title": title.strip(),
        "authors": authors,
        "createdDate": work.get("created_date"),
        "updateDate": work.get("updated_date"),
        "publicationDate": work.get("publication_date"),
        "categories": topics,
        "url": "https://doi.org/" + doi,
        "provider": "OpenAlex",
        "citedByCount": work.get("cited_by_count"),
        "referenceCount": len(work.get("referenced_works") or []),
        "referenceIds": references,
        "relatedWorks": related,
        "isRetracted": work.get("is_retracted"),
    }
    if isinstance(work.get("relevance_score"), (int, float)) and not isinstance(work.get("relevance_score"), bool):
        result["score"] = work["relevance_score"]
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
    created = paper.get("publicationDate") or paper.get("createdDate")
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


async def _fetch_openalex(hosted, params):
    db = hosted.db
    db.execute("BEGIN IMMEDIATE")
    try:
        row = db.execute("SELECT next_allowed FROM research_provider_state WHERE provider='openalex'").fetchone()
        now = time.time()
        slot = max(now, float(row[0]) if row else now)
        db.execute("INSERT INTO research_provider_state(provider,next_allowed) VALUES('openalex',?) "
                   "ON CONFLICT(provider) DO UPDATE SET next_allowed=excluded.next_allowed", (slot + 1.05,))
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise
    await asyncio.sleep(max(0, slot - now))
    query = dict(params)
    query["api_key"] = os.environ["OPENALEX_API_KEY"].strip()
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout,
                                     headers={"User-Agent": "ApexFlowWebScraper/1.0 (https://webscraper.apexflowlabs.com)"}) as session:
        async with session.get(_OPENALEX_ROOT, params=query, allow_redirects=False) as response:
            if response.status != 200:
                raise ResearchProviderError(response.status)
            chunks, size = [], 0
            async for chunk in response.content.iter_chunked(65536):
                size += len(chunk)
                if size > _MAX_RESPONSE_BYTES:
                    raise ResearchProviderError(502)
                chunks.append(chunk)
    try:
        payload = json.loads(b"".join(chunks).decode("utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise ValueError("OpenAlex response has no results array")
        return payload
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ResearchProviderError(502) from exc


async def _search_openalex(hosted, query, k, start, end, sort):
    params = {
        "search": query,
        "per_page": 100,
        "cursor": "*",
        "select": _OPENALEX_SELECT,
    }
    if sort == "date_desc":
        params["sort"] = "publication_date:desc"
    filters = []
    if start:
        filters.append("from_publication_date:" + start)
    if end:
        filters.append("to_publication_date:" + end)
    if filters:
        params["filter"] = ",".join(filters)
    papers = []
    for _ in range(max(1, (k + 99) // 100)):
        payload = await _fetch_openalex(hosted, params)
        papers.extend(paper for work in payload["results"] if isinstance(work, dict)
                      for paper in [_openalex_paper(work)] if paper)
        if len(papers) >= k or not payload.get("meta", {}).get("next_cursor"):
            break
        params["cursor"] = payload["meta"]["next_cursor"]
    return papers[:k]


async def _search_crossref(hosted, query, k, start, end, sort):
    params = {
        "query.bibliographic": query,
        "rows": min(1000, max(k, k * 3)),
        "select": "DOI,title,author,created,updated,indexed,score,subject",
        "sort": "created" if sort == "date_desc" else "score",
        "order": "desc",
    }
    date_filters = []
    if start:
        date_filters.append("from-created-date:" + start)
    if end:
        date_filters.append("until-created-date:" + end)
    if date_filters:
        params["filter"] = ",".join(date_filters)
    message = await _fetch_crossref(hosted, params)
    items = message.get("items", []) if isinstance(message, dict) else []
    papers = [paper for work in items if isinstance(work, dict)
              for paper in [_paper(work, score=True)] if paper]
    if sort == "date_desc":
        papers.sort(key=lambda paper: (paper.get("createdDate") or "", paper["paperId"]), reverse=True)
    return papers[:k]


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

        provider = "OpenAlex" if os.environ.get("OPENALEX_API_KEY", "").strip() else "Crossref"
        filters = {"query": query, "k": k, "authors": authors, "categories": categories,
                   "from": start, "to": end, "sort": sort, "provider": provider}
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
            elif provider == "OpenAlex":
                try:
                    papers = await _search_openalex(hosted, query, k, start, end, sort)
                except (ResearchProviderError, aiohttp.ClientError, asyncio.TimeoutError, OSError):
                    papers = await _search_crossref(hosted, query, k, start, end, sort)
                if not papers:
                    papers = await _search_crossref(hosted, query, k, start, end, sort)
            else:
                papers = await _search_crossref(hosted, query, k, start, end, sort)
            if not row:
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
            return error("paper_id must be a DOI returned by the research paper search endpoint")
        paper_id = paper_id.lower()
        provider = "OpenAlex" if os.environ.get("OPENALEX_API_KEY", "").strip() else "Crossref"
        cache_key = hashlib.sha256((provider + ":doi:" + paper_id).encode()).hexdigest()
        row = hosted.db.execute("SELECT payload FROM research_paper_cache WHERE cache_key=? AND expires>?",
                                (cache_key, time.time())).fetchone()
        try:
            hosted.billing.reserve(request["account"], cost_of("search"), kind="search")
        except QuotaExceeded as exc:
            return error(str(exc), "insufficient_credits", 402)
        try:
            paper = json.loads(row[0]) if row else None
            if paper is None:
                if provider == "OpenAlex":
                    try:
                        payload = await _fetch_openalex(hosted, {
                            "filter": "doi:https://doi.org/" + paper_id,
                            "per_page": 1,
                            "select": _OPENALEX_SELECT,
                        })
                        work = payload["results"][0] if payload["results"] else {}
                        paper = _openalex_paper(work if isinstance(work, dict) else {})
                    except (ResearchProviderError, aiohttp.ClientError, asyncio.TimeoutError, OSError):
                        paper = None
                    if paper is None:
                        work = await _fetch_crossref(
                            hosted, {"select": "DOI,title,author,created,updated,indexed,subject"}, paper_id)
                        paper = _paper(work if isinstance(work, dict) else {})
                else:
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

    async def paper_relations(request, relation):
        if not os.environ.get("OPENALEX_API_KEY", "").strip():
            return error("Paper relationships require OPENALEX_API_KEY to be configured",
                         "research_provider_unavailable", 503)
        paper_id = request.match_info["paper_id"].strip()
        if paper_id.lower().startswith("doi:"):
            paper_id = paper_id[4:]
        work_match = re.fullmatch(r"(?:https://openalex\.org/)?(W\d+)", paper_id, re.I)
        if work_match:
            work_id = work_match.group(1).upper()
            cache_subject = work_id
        elif _DOI_RE.fullmatch(paper_id):
            paper_id = paper_id.lower()
            work_id = None
            cache_subject = paper_id
        else:
            return error("paper_id must be a DOI or OpenAlex work ID")
        if set(request.query) - {"k"}:
            return error("Only the k parameter is supported")
        raw_k = request.query.get("k", "20")
        if not raw_k.isascii() or not raw_k.isdecimal() or not 1 <= int(raw_k) <= 100:
            return error("k must be an integer from 1 to 100")
        k = int(raw_k)
        try:
            hosted.billing.reserve(request["account"], cost_of("search"), kind="search")
        except QuotaExceeded as exc:
            return error(str(exc), "insufficient_credits", 402)
        cache_key = hashlib.sha256(f"openalex:{relation}:{cache_subject}:{k}".encode()).hexdigest()
        row = hosted.db.execute("SELECT payload FROM research_paper_cache WHERE cache_key=? AND expires>?",
                                (cache_key, time.time())).fetchone()
        try:
            papers = json.loads(row[0]) if row else None
            if papers is None:
                base_work = None
                if work_id is None or relation == "related":
                    filt = "doi:https://doi.org/" + paper_id if work_id is None else "openalex:" + work_id
                    payload = await _fetch_openalex(hosted, {"filter": filt, "per_page": 1,
                                                             "select": _OPENALEX_SELECT})
                    base_work = payload["results"][0] if payload["results"] else None
                    if not isinstance(base_work, dict):
                        raise ResearchProviderError(404)
                    if work_id is None:
                        match = re.fullmatch(r"https://openalex\.org/(W\d+)", base_work.get("id") or "")
                        if not match:
                            raise ResearchProviderError(404)
                        work_id = match.group(1)
                if relation == "related":
                    related = base_work.get("related_works", []) if isinstance(base_work, dict) else []
                    related_ids = [value.rsplit("/", 1)[-1] for value in related
                                   if isinstance(value, str) and re.fullmatch(r"https://openalex\.org/W\d+", value)][:20]
                    if related_ids:
                        payload = await _fetch_openalex(hosted, {
                            "filter": "openalex:" + "|".join(related_ids),
                            "per_page": len(related_ids), "select": _OPENALEX_SELECT,
                        })
                        papers = [paper for work in payload["results"] if isinstance(work, dict)
                                  for paper in [_openalex_paper(work)] if paper]
                        order = {value: index for index, value in enumerate(related_ids)}
                        papers.sort(key=lambda paper: order.get(paper.get("ids", {}).get("openalex", [""])[0], 999))
                    else:
                        papers = []
                else:
                    payload = await _fetch_openalex(hosted, {
                        "filter": "cites:" + work_id,
                        "per_page": min(100, k * 2), "sort": "publication_date:desc",
                        "select": _OPENALEX_SELECT,
                    })
                    papers = [paper for work in payload["results"] if isinstance(work, dict)
                              for paper in [_openalex_paper(work)] if paper][:k]
                hosted.db.execute("DELETE FROM research_paper_cache WHERE expires<=?", (time.time(),))
                hosted.db.execute("INSERT OR REPLACE INTO research_paper_cache(cache_key,payload,expires) VALUES(?,?,?)",
                                  (cache_key, json.dumps(papers, separators=(",", ":")), time.time() + _CACHE_TTL))
            papers = papers[:k]
        except ResearchProviderError as exc:
            hosted.billing.refund(request["account"], cost_of("search"))
            if exc.status == 404:
                return error("Paper not found in OpenAlex", "not_found", 404)
            response = error("OpenAlex is temporarily unavailable", "research_provider_unavailable", 503)
            response.headers["Retry-After"] = "2"
            return response
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError, KeyError, TypeError):
            hosted.billing.refund(request["account"], cost_of("search"))
            response = error("OpenAlex is temporarily unavailable", "research_provider_unavailable", 503)
            response.headers["Retry-After"] = "2"
            return response
        except BaseException:
            hosted.billing.refund(request["account"], cost_of("search"))
            raise
        return web.json_response({"success": True, "paperId": paper_id, "relation": relation,
                                  "results": papers, "creditsUsed": cost_of("search")})

    async def related_papers(request):
        return await paper_relations(request, "related")

    async def citing_papers(request):
        return await paper_relations(request, "citations")

    app.router.add_get("/v2/search/research/papers", wrap(search_papers))
    app.router.add_get("/v2/search/research/papers/{paper_id:.+}/related", wrap(related_papers))
    app.router.add_get("/v2/search/research/papers/{paper_id:.+}/citations", wrap(citing_papers))
    app.router.add_get("/v2/search/research/papers/{paper_id:.+}", wrap(get_paper))
