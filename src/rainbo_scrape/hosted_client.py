"""Python client for the hosted Apex Flow 3D Scraper API. Standard library only.

    from rainbo_scrape.hosted_client import Apex
    apex = Apex()                       # reads APEX_SCRAPER_API_KEY
    print(apex.scrape("https://example.com", outputs=["markdown"])["result"]["markdown"])
"""
import json
import os
import time
import urllib.error
import urllib.request

BASE = "https://webscraper.apexflowlabs.com/v1"


class ApexError(Exception):
    """A failed call. `code`, `retryable`, `retry_after`, `buy_url` and `hint` mirror the API's error body."""

    def __init__(self, status, body):
        super().__init__(body.get("error") or f"HTTP {status}")
        self.status, self.body = status, body
        for name in ("code", "retryable", "retry_after", "buy_url", "hint", "credits_left"):
            setattr(self, name, body.get(name))


class Apex:
    def __init__(self, api_key=None, base_url=BASE, timeout=100, retries=2):
        self.key = api_key or os.environ.get("APEX_SCRAPER_API_KEY")
        if not self.key:
            raise ValueError("Pass api_key or set APEX_SCRAPER_API_KEY. Get a key at https://webscraper.apexflowlabs.com/trial.html")
        self.base, self.timeout, self.retries = base_url.rstrip("/"), timeout, retries

    def _call(self, method, path, body=None):
        data = None if body is None else json.dumps({k: v for k, v in body.items() if v is not None}).encode()
        for attempt in range(self.retries + 1):
            request = urllib.request.Request(self.base + path, data=data, method=method, headers={
                "Authorization": "Bearer " + self.key, "Content-Type": "application/json", "User-Agent": "apex-scraper-python/0.6.0"})
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.load(response)
            except urllib.error.HTTPError as exc:
                try:
                    problem = json.load(exc)
                except ValueError:
                    problem = {}
                error = ApexError(exc.code, problem)
                if exc.code == 429 and attempt < self.retries:      # the API says how long to wait
                    time.sleep(min(30, error.retry_after or int(exc.headers.get("Retry-After") or 5)))
                    continue
                raise error

    def scrape(self, url, **options):
        """One page. Options: render, outputs, recipe, chunks, max_tokens, page, fallback="archive", max_age, extract."""
        return self._call("POST", "/scrape", {"url": url, **options})

    def scrape_all(self, url, **options):
        """Every part of a long page, joined. Follows pagination for you."""
        first = self.scrape(url, **options)
        result, page = first["result"], (first.get("pagination") or {}).get("next_page")
        while page:
            more = self.scrape(url, **{**options, "page": page})
            for field in ("markdown", "text"):
                if field in result and field in more["result"]:
                    result[field] += more["result"][field]
            for field, value in more["result"].items():
                if isinstance(value, list) and isinstance(result.get(field), list) and field != "warnings":
                    result[field] += value
            page = more["pagination"]["next_page"]
        return result

    def batch(self, urls, **options):
        return self._call("POST", "/batch", {"urls": list(urls), **options})

    def changes(self, urls, render="never"):
        """What changed since this account last read each URL, without page bodies."""
        return self.batch(urls, render=render, outputs=["diff"])

    def map(self, url, limit=1000, search=None):
        return self._call("POST", "/map", {"url": url, "limit": limit, "search": search})

    def search(self, query, limit=10):
        """Search pages this account has already scraped. Free."""
        return self._call("POST", "/search", {"query": query, "limit": limit})

    def web_search(self, query, limit=10):
        return self._call("POST", "/websearch", {"query": query, "limit": limit})

    def usage(self):
        return self._call("GET", "/usage")

    def start_crawl(self, url, limit=25, **options):
        return self._call("POST", "/crawl", {"url": url, "limit": limit, **options})

    def crawl_status(self, job_id, offset=0):
        return self._call("GET", f"/crawl/{job_id}?offset={int(offset)}")

    def cancel_crawl(self, job_id):
        return self._call("DELETE", f"/crawl/{job_id}")

    def crawl(self, url, limit=25, poll=2.0, **options):
        """Crawl one site and yield each page as it becomes available."""
        job, offset = self.start_crawl(url, limit, **options), 0
        while offset is not None:
            status = self.crawl_status(job["id"], offset)
            yield from status["results"]
            if status["next_offset"] == offset and status["status"] == "running":
                time.sleep(poll)
            offset = status["next_offset"]
