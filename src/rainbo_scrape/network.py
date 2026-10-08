import asyncio
from email.utils import parsedate_to_datetime
import hashlib
import time
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser
import aiohttp
from .policy import PublicResolver, ScrapeError, check_url


def retry_delay(value):
    try:
        return max(0, min(60, float(value)))
    except (ValueError, TypeError):
        try:
            return max(0, min(60, parsedate_to_datetime(value).timestamp()-time.time()))
        except (ValueError, TypeError, OverflowError):
            return 1


class Fetcher:
    def __init__(self, settings, store=None):
        self.settings, self.store = settings, store
        self.session = None
        self.robots, self.locks, self.last, self.delays = {}, {}, {}, {}
        self.requests = self.transferred = self.cache_hits = 0
        self.charge = None

    async def __aenter__(self):
        connector = aiohttp.TCPConnector(resolver=PublicResolver(self.settings.allow_test_loopback),
                                        ttl_dns_cache=0, limit=8)
        self.session = aiohttp.ClientSession(connector=connector, trust_env=False,
            timeout=aiohttp.ClientTimeout(total=self.settings.timeout),
            cookie_jar=aiohttp.DummyCookieJar(), headers={"User-Agent": self.settings.user_agent})
        return self

    async def __aexit__(self, *args):
        await self.session.close()

    async def _throttle(self, url):
        host = urlsplit(url).netloc
        lock = self.locks.setdefault(host, asyncio.Lock())
        async with lock:
            delay = max(self.settings.host_delay, self.delays.get(host, 0))
            await asyncio.sleep(max(0, self.last.get(host, 0)+delay-time.monotonic()))
            self.last[host] = time.monotonic()

    async def allowed(self, url):
        p = urlsplit(url)
        origin = f"{p.scheme}://{p.netloc}"
        lock = self.locks.setdefault("robots:"+origin, asyncio.Lock())
        async with lock:
            if origin not in self.robots:
                response = await self.get(origin+"/robots.txt", robots=False, cache=True)
                parser = RobotFileParser(origin+"/robots.txt")
                if response["status"] in (401, 403):
                    parser.parse(["User-agent: *", "Disallow: /"])
                elif response["status"] in (404, 410):
                    parser.parse([])
                elif response["status"] >= 400:
                    raise ScrapeError("robots_unavailable", "robots.txt could not be checked")
                else:
                    parser.parse(response["body"].decode("utf-8", errors="replace").splitlines())
                delay = parser.crawl_delay("RAINBO-Scrape") or parser.crawl_delay("*") or 0
                if delay > 60:
                    raise ScrapeError("robots_delay", "Site asks for a crawl delay above this job's limit")
                self.delays[p.netloc] = delay
                self.robots[origin] = parser
            if not self.robots[origin].can_fetch("RAINBO-Scrape", url):
                raise ScrapeError("robots_denied", "Site robots.txt disallows this URL")

    async def get(self, url, *, robots=True, cache=True):
        started = time.monotonic()
        url = check_url(url, self.settings.allow_test_loopback)
        original = url
        for redirect in range(7):
            check_url(url, self.settings.allow_test_loopback)
            if robots:
                await self.allowed(url)
            key = hashlib.sha256(url.encode()).hexdigest()
            cached = self.store.cache_get(key) if cache and self.store else None
            if cached and time.time()-cached["ts"] < self.settings.cache_ttl:
                self.cache_hits += 1
                return {**cached, "cache": "hit", "elapsed_ms": round((time.monotonic()-started)*1000, 2)}
            headers = {}
            if cached:
                if cached["headers"].get("etag"):
                    headers["If-None-Match"] = cached["headers"]["etag"]
                if cached["headers"].get("last-modified"):
                    headers["If-Modified-Since"] = cached["headers"]["last-modified"]
            for attempt in range(3):
                await self._throttle(url)
                try:
                    async with self.session.get(url, headers=headers, allow_redirects=False) as res:
                        self.requests += 1
                        safe_headers = {k.lower(): v for k, v in res.headers.items() if k.lower() in {
                            "content-type", "etag", "last-modified", "cache-control", "location", "retry-after"}}
                        if res.status in {429, 502, 503, 504}:
                            delay = retry_delay(res.headers.get("Retry-After", str(2**attempt)))
                            if attempt == 2 or delay > self.settings.timeout:
                                raise ScrapeError("rate_limited" if res.status == 429 else "upstream_error",
                                                  f"Site returned HTTP {res.status}", delay)
                            await asyncio.sleep(delay)
                            continue
                        if res.status == 304 and cached:
                            cached["headers"].update(safe_headers)
                            self.store.cache_put(key, cached)
                            self.cache_hits += 1
                            return {**cached, "cache": "revalidated", "elapsed_ms": round((time.monotonic()-started)*1000, 2)}
                        if res.status in {301, 302, 303, 307, 308}:
                            if "Location" not in res.headers:
                                raise ScrapeError("redirect_invalid", "Redirect has no location")
                            url = check_url(urljoin(url, res.headers["Location"]), self.settings.allow_test_loopback)
                            break
                        data = bytearray()
                        async for chunk in res.content.iter_chunked(65536):
                            if self.charge:
                                self.charge(len(chunk))
                            self.transferred += len(chunk)
                            data.extend(chunk)
                            if len(data) > self.settings.max_body:
                                raise ScrapeError("body_limit", "Decoded response exceeds the per-page byte limit")
                        response = {"url": str(res.url), "status": res.status, "headers": safe_headers,
                                    "body": bytes(data), "cache": "miss",
                                    "elapsed_ms": round((time.monotonic()-started)*1000, 2)}
                        private = safe_headers.get("cache-control", "").lower()
                        if cache and self.store and res.status == 200 and not any(x in private for x in ("no-store", "private")):
                            self.store.cache_put(key, response)
                        return response
                except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                    if attempt == 2:
                        raise ScrapeError("fetch_failed", type(exc).__name__ + ": request could not complete") from exc
                    await asyncio.sleep(0.5 * (2**attempt))
            else:
                raise ScrapeError("fetch_failed", "Request retries exhausted")
        raise ScrapeError("redirect_limit", "Too many redirects")
