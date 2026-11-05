"""Isolated installed-Brave rendering; every resource goes through our guarded fetcher.

One browser process is started lazily per Fetcher and reused for every page in
the job (warm browser). Each page still gets its own isolated context.
"""
import asyncio
import time
from .policy import ScrapeError, check_url
from .settings import brave_path

HIDDEN_REMOVAL = """() => {
  const skip = new Set(['SCRIPT','STYLE','NOSCRIPT','TEMPLATE','SVG','HEAD','META','LINK','TITLE']);
  const gone = [];
  for (const el of document.body ? document.body.querySelectorAll('*') : []) {
    if (skip.has(el.tagName) || el.closest('details')) continue;
    try { if (!el.checkVisibility({visibilityProperty: true})) gone.push(el); } catch (e) {}
  }
  for (const el of gone) if (el.isConnected) el.remove();
  return gone.length;
}"""


async def get_browser(fetcher):
    if getattr(fetcher, "_browser", None):
        return fetcher._browser
    from playwright.async_api import async_playwright
    executable = brave_path()
    if not executable:
        raise ScrapeError("brave_missing", "Installed Brave is required for rendered pages")
    fetcher._playwright = await async_playwright().start()
    fetcher._browser = await fetcher._playwright.chromium.launch(
        executable_path=executable, headless=True,
        args=["--disable-background-networking", "--disable-component-update", "--disable-sync",
              "--disable-features=MediaRouter", "--force-webrtc-ip-handling-policy=disable_non_proxied_udp"])
    return fetcher._browser


async def close_browser(fetcher):
    browser, manager = getattr(fetcher, "_browser", None), getattr(fetcher, "_playwright", None)
    fetcher._browser = fetcher._playwright = None
    if browser:
        await browser.close()
    if manager:
        await manager.stop()


async def render(fetcher, url, wait_selector=None, scrolls=0, screenshot=False):
    started = time.monotonic()
    count = transferred = 0
    failures = []
    browser = await get_browser(fetcher)
    context = await browser.new_context(service_workers="block", accept_downloads=False,
                                        user_agent=fetcher.settings.user_agent)
    try:
        async def guarded(route):
            nonlocal count, transferred
            request = route.request
            if request.method not in {"GET", "HEAD"} or request.resource_type in {"image", "media", "font"}:
                await route.abort()
                return
            count += 1
            if count > fetcher.settings.max_browser_requests or transferred >= fetcher.settings.max_browser_bytes:
                failures.append("resource_budget")
                await route.abort()
                return
            try:
                check_url(request.url, fetcher.settings.allow_test_loopback)
                response = await fetcher.get(request.url, throttle=request.is_navigation_request())
                transferred += len(response["body"])
                if transferred > fetcher.settings.max_browser_bytes:
                    raise ScrapeError("resource_budget", "Browser response budget reached")
                await route.fulfill(status=response["status"], body=response["body"],
                                    headers={"content-type": response["headers"].get("content-type", "text/plain")})
            except Exception as exc:
                failures.append(getattr(exc, "code", type(exc).__name__))
                await route.abort()
        await context.route("**/*", guarded)
        await context.route_web_socket("**/*", lambda ws: ws.close())
        page = await context.new_page()
        page.set_default_timeout(8000)
        inflight = 0

        def started_request(_):
            nonlocal inflight
            inflight += 1

        def ended_request(_):
            nonlocal inflight
            inflight = max(0, inflight - 1)
        page.on("request", started_request)
        page.on("requestfinished", ended_request)
        page.on("requestfailed", ended_request)
        async with asyncio.timeout(fetcher.settings.timeout):
            await page.goto(url, wait_until="domcontentloaded")
            if wait_selector:
                await page.locator(wait_selector).first.wait_for(state="attached")
            else:
                # Network-quiet wait: done when nothing is in flight for 350 ms (max 4 s).
                quiet_since, deadline = time.monotonic(), time.monotonic() + 4
                while time.monotonic() < deadline:
                    await asyncio.sleep(0.05)
                    if inflight:
                        quiet_since = time.monotonic()
                    elif time.monotonic() - quiet_since >= 0.35:
                        break
            for _ in range(scrolls):
                await page.evaluate("window.scrollBy(0, window.innerHeight)")
                await page.wait_for_timeout(300)
            hidden_removed = await page.evaluate(HIDDEN_REMOVAL)
            shot = await page.screenshot(full_page=True, type="png") if screenshot else None
            body = (await page.content()).encode()
            if len(body) > fetcher.settings.max_body:
                raise ScrapeError("body_limit", "Rendered HTML exceeds the per-page limit")
            return {"body": body, "url": page.url, "status": 200, "headers": {"content-type": "text/html"},
                    "cache": "rendered", "elapsed_ms": round((time.monotonic()-started)*1000, 2),
                    "screenshot": shot,
                    "browser": {"resources": count, "bytes": transferred, "blocked_resources": failures[:20],
                                "hidden_elements_removed": hidden_removed}}
    finally:
        await context.close()
