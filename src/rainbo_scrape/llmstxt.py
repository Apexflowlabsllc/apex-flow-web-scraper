"""Build llms.txt / llms-full.txt for a site you operate: map it, scrape each page politely, write one file for AI agents."""
import asyncio
import re
from urllib.parse import urlsplit
from .discovery import site_map
from .engine import Fetcher, scrape
from .settings import Settings
from .store import Store


def _first_sentence(text, limit=160):
    flat = re.sub(r"\s+", " ", re.sub(r"[#>*`|\[\]]", " ", text)).strip()
    cut = re.split(r"(?<=[.!?])\s", flat, maxsplit=1)[0]
    return cut[:limit].rstrip()


def compose(site, pages, full=False):
    """pages: list of result dicts (title, url, markdown/text). Returns the file text."""
    seen, kept = set(), []
    for p in pages:
        if not p or not (p.get("markdown") or p.get("text")):
            continue
        key = p.get("content_sha256") or p["url"]
        if key not in seen:  # a page served at two URLs is listed once
            seen.add(key)
            kept.append({**p, "title": re.sub(r"\s+", " ", p.get("title") or "").strip()})
    pages = kept
    if not pages:
        raise ValueError("no pages could be read")
    home = pages[0]
    name = (home.get("title") or urlsplit(site).netloc).strip()
    lines = [f"# {name}", "", "> " + (_first_sentence(home.get("markdown") or home["text"]) or name), "", "## Pages", ""]
    for p in pages:
        body = p.get("markdown") or p["text"]
        lines.append(f"- [{p.get('title') or p['url']}]({p['url']}): {_first_sentence(body)}")
    if full:
        for p in pages:
            lines += ["", "---", "", f"<!-- source: {p['url']} -->", "", (p.get("markdown") or p["text"]).strip()]
    return "\n".join(lines) + "\n"


async def build(site, limit=30, full=False, settings=None):
    settings = settings or Settings()
    async with Fetcher(settings, Store(settings)) as fetcher:
        found = await site_map(fetcher, site, limit=limit)
        urls = [site] + [u["url"] for u in found["urls"] if u["url"].rstrip("/") != site.rstrip("/")]
        gate = asyncio.Semaphore(4)
        options = {"render": "never", "cache": True}

        async def one(url):
            async with gate:
                try:
                    return await scrape(fetcher, url, options)
                except Exception:
                    return None
        pages = await asyncio.gather(*(one(u) for u in urls[:limit]))
    return compose(site, list(pages), full)
