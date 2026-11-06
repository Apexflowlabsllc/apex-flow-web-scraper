"""Site map discovery: robots.txt Sitemap lines, sitemap indexes (gzip aware), then page links.

Unlike hosted /map endpoints that stop after a fixed number of sitemap files,
this reads up to MAX_SITEMAPS files and reports whether it was truncated.
"""
import re
import zlib
from urllib.parse import urljoin, urlsplit
from defusedxml import ElementTree
from .parser import parse
from .policy import ScrapeError, canonical_url

MAX_SITEMAPS = 200
MAX_XML = 20 * 1024 * 1024


def _unzip(body):
    if body[:2] != b"\x1f\x8b":
        return body
    out = zlib.decompressobj(31).decompress(body, MAX_XML)
    return out


def parse_sitemap(body):
    """Return (child_sitemaps, [(url, lastmod)])."""
    root = ElementTree.fromstring(_unzip(body))
    kind = root.tag.rsplit("}", 1)[-1]
    items = []
    for node in root:
        loc = lastmod = None
        for child in node:
            tag = child.tag.rsplit("}", 1)[-1]
            if tag == "loc":
                loc = (child.text or "").strip()
            elif tag == "lastmod":
                lastmod = (child.text or "").strip()
        if loc:
            items.append((loc, lastmod))
    if kind == "sitemapindex":
        return [i[0] for i in items], []
    return [], items


async def site_map(fetcher, url, limit=5000, search=None, include_links=True, max_sitemaps=MAX_SITEMAPS):
    start = canonical_url(url)
    parts = urlsplit(start)
    origin = f"{parts.scheme}://{parts.netloc}"
    queue, seen_maps, found, notes = [], set(), {}, []
    try:
        robots = await fetcher.get(origin + "/robots.txt", robots=False)
        if robots["status"] == 200:
            for line in robots["body"].decode("utf-8", errors="replace").splitlines():
                if line.lower().startswith("sitemap:"):
                    queue.append(line.split(":", 1)[1].strip())
    except ScrapeError as exc:
        notes.append("robots.txt unavailable: " + exc.code)
    queue += [origin + "/sitemap.xml", origin + "/sitemap_index.xml"]
    truncated = False
    while queue and len(seen_maps) < max_sitemaps:
        sitemap = queue.pop(0)
        if sitemap in seen_maps or urlsplit(sitemap).netloc.split(":")[0].split(".")[-2:] != parts.hostname.split(".")[-2:]:
            continue
        seen_maps.add(sitemap)
        try:
            response = await fetcher.get(sitemap)
            if response["status"] != 200:
                continue
            children, items = parse_sitemap(response["body"])
        except (ScrapeError, ElementTree.ParseError, ValueError, zlib.error):
            continue
        queue.extend(urljoin(sitemap, c) for c in children if urljoin(sitemap, c) not in seen_maps)
        for loc, lastmod in items:
            try:
                target = canonical_url(urljoin(sitemap, loc))
            except ScrapeError:
                continue
            if target not in found:
                found[target] = {"url": target, "lastmod": lastmod, "source": "sitemap"}
            if len(found) >= limit:
                truncated = True
                queue.clear()
                break
    if queue:
        truncated = True
    if include_links and len(found) < limit:
        try:
            page = await fetcher.get(start)
            if page["status"] == 200:
                result = await parse(page["body"], page["url"], page["headers"].get("content-type", "text/html"))
                for link in result.get("links", []):
                    if urlsplit(link["url"]).netloc == parts.netloc and link["url"] not in found and len(found) < limit:
                        found[link["url"]] = {"url": link["url"], "lastmod": None, "source": "link"}
        except ScrapeError as exc:
            notes.append("start page links unavailable: " + exc.code)
    urls = list(found.values())
    if search:
        words = re.findall(r"\w+", search.lower())
        urls.sort(key=lambda u: -sum(w in u["url"].lower() for w in words))
        urls = [u for u in urls if any(w in u["url"].lower() for w in words)] or urls
    return {"start": start, "count": len(urls), "urls": urls[:limit], "sitemaps_read": len(seen_maps),
            "truncated": truncated, "notes": notes}
