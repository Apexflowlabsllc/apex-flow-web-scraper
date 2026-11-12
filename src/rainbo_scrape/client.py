"""In-process Python SDK: no server, no worker, no account."""
import asyncio
from .discovery import site_map
from .engine import Fetcher, scrape as _scrape, validate_request
from .settings import Settings
from .store import Store


class Page(dict):
    """A scrape result that also allows attribute access (page.markdown)."""

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return self.get(name)


async def ascrape(url, *, render="auto", recipe=None, max_age=None, screenshot=False, extract=None,
                  wait_selector=None, chunks=None, settings=None, browser_slots=None):
    settings = settings or Settings()
    request = {"urls": [url], "max_pages": 1, "max_depth": 0, "render": render, "screenshot": screenshot}
    for key, value in (("recipe", recipe), ("max_age", max_age), ("extract", extract), ("wait_selector", wait_selector),
                         ("chunks", chunks)):
        if value is not None:
            request[key] = value
    options = validate_request(request, settings)
    async with Fetcher(settings, Store(settings), browser_slots=browser_slots) as fetcher:
        async with asyncio.timeout(settings.timeout * 3 + 20):
            return Page(await _scrape(fetcher, options["urls"][0], options))


def scrape(url, **options):
    return asyncio.run(ascrape(url, **options))


async def amap_site(url, *, limit=5000, search=None, settings=None):
    settings = settings or Settings()
    async with Fetcher(settings, Store(settings)) as fetcher:
        async with asyncio.timeout(120):
            return await site_map(fetcher, url, limit=limit, search=search)


def map_site(url, **options):
    return asyncio.run(amap_site(url, **options))
