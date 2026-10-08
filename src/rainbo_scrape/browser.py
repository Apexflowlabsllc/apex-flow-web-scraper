"""Isolated installed-Brave rendering; every resource goes through our guarded fetcher."""
import asyncio
import time
from .policy import ScrapeError, check_url
from .settings import brave_path


async def render(fetcher, url, wait_selector=None, scrolls=0):
    from playwright.async_api import async_playwright
    executable = brave_path()
    if not executable:
        raise ScrapeError("brave_missing", "Installed Brave is required for rendered pages")
    started = time.monotonic()
    count = transferred = 0
    failures = []
    async with async_playwright() as manager:
        browser = await manager.chromium.launch(executable_path=executable, headless=True,
            args=["--disable-background-networking", "--disable-component-update", "--disable-sync",
                  "--disable-features=MediaRouter", "--force-webrtc-ip-handling-policy=disable_non_proxied_udp"])
        try:
            context = await browser.new_context(service_workers="block", accept_downloads=False,
                                                 user_agent=fetcher.settings.user_agent)
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
                    response = await fetcher.get(request.url)
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
            async with asyncio.timeout(fetcher.settings.timeout):
                await page.goto(url, wait_until="domcontentloaded")
                if wait_selector:
                    await page.locator(wait_selector).first.wait_for(state="attached")
                else:
                    await page.wait_for_timeout(750)
                for _ in range(scrolls):
                    await page.evaluate("window.scrollBy(0, window.innerHeight)")
                    await page.wait_for_timeout(300)
                body = (await page.content()).encode()
                if len(body) > fetcher.settings.max_body:
                    raise ScrapeError("body_limit", "Rendered HTML exceeds the per-page limit")
                return {"body": body, "url": page.url, "status": 200, "headers": {"content-type": "text/html"},
                        "cache": "rendered", "elapsed_ms": round((time.monotonic()-started)*1000, 2),
                        "browser": {"resources": count, "bytes": transferred, "blocked_resources": failures[:20]}}
        finally:
            await browser.close()
