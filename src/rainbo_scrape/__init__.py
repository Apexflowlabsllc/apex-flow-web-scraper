"""RAINBO's self-hosted scraping toolkit. No hosted scraping credentials used.

One line:  from rainbo_scrape import scrape;  print(scrape("https://example.com").markdown)
"""
__version__ = "0.2.0"


def scrape(url, **options):
    """Fetch one page (HTML, PDF, DOCX, JSON, feed) and return a Page with .markdown, .text, .links ..."""
    from .client import scrape as run
    return run(url, **options)


def map_site(url, **options):
    """Discover a site's URLs from robots.txt, sitemaps (indexes, gzip) and page links."""
    from .client import map_site as run
    return run(url, **options)


async def ascrape(url, **options):
    from .client import ascrape as run
    return await run(url, **options)
