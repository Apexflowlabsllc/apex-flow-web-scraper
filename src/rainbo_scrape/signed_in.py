"""Read pages through your own signed-in browser profile, on your own machine.

Self-hosted only. You sign in yourself, in a real browser window, to a site you have an account with.
The session stays in a profile folder inside your data directory; it is never uploaded, and the hosted
API has no part in it. Nothing here hides that the browser is automated or solves a challenge: if a
site refuses, the refusal is reported. You are responsible for staying within the terms of the site
you signed in to.
"""
import asyncio
import hashlib
import re
import time

from .browser import HIDDEN_REMOVAL
from .parser import parse
from .policy import ScrapeError, check_url
from .settings import Settings, brave_path

REFUSED = {401: "sign_in_required", 403: "sign_in_required", 407: "sign_in_required", 429: "rate_limited"}


def profile_dir(settings, profile="default"):
    """Folder that holds one signed-in profile. Names are plain words so a profile cannot escape the data folder."""
    if not isinstance(profile, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,39}", profile):
        raise ValueError("profile must be 1-40 letters, digits, hyphens or underscores")
    return settings.data_dir / "profiles" / profile


async def _open(settings, profile, headless):
    from playwright.async_api import async_playwright
    executable = brave_path()
    if not executable:
        raise ScrapeError("brave_missing", "Installed Brave is required for signed-in reading")
    folder = profile_dir(settings, profile)
    folder.mkdir(parents=True, exist_ok=True)
    manager = await async_playwright().start()
    try:
        context = await manager.chromium.launch_persistent_context(
            str(folder), executable_path=executable, headless=headless, accept_downloads=False,
            args=["--disable-sync", "--no-first-run", "--no-default-browser-check"])
    except BaseException:
        await manager.stop()
        raise
    return manager, context


async def sign_in(url, profile="default", settings=None, headless=False, wait_for_close=True):
    """Open a browser window on the sign-in page and keep the session in the named profile.

    You type your own login. Close the window when you are done; nothing is read or sent anywhere."""
    settings = (settings or Settings()).prepare()
    url = check_url(url, settings.allow_test_loopback)
    manager, context = await _open(settings, profile, headless)
    try:
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(url, wait_until="domcontentloaded")
        if wait_for_close:
            closed = asyncio.Event()
            context.on("close", lambda _=None: closed.set())
            page.on("close", lambda _=None: closed.set() if not context.pages else None)
            await closed.wait()
    finally:
        try:
            await context.close()
        except Exception:
            pass
        await manager.stop()
    return {"profile": profile, "folder": str(profile_dir(settings, profile))}


async def read_signed_in(url, profile="default", settings=None, recipe=None, wait_selector=None, headless=True):
    """Fetch one page with the named profile's session and return the same result shape as a normal scrape."""
    settings = (settings or Settings()).prepare()
    url = check_url(url, settings.allow_test_loopback)
    started = time.monotonic()
    manager, context = await _open(settings, profile, headless)
    try:
        page = context.pages[0] if context.pages else await context.new_page()
        page.set_default_timeout(15000)
        async with asyncio.timeout(settings.timeout * 2):
            response = await page.goto(url, wait_until="domcontentloaded")
            status = response.status if response else 0
            if status in REFUSED:
                raise ScrapeError(REFUSED[status], f"Site returned HTTP {status} to this profile. Sign in with: rainbo-scrape login {url} --profile {profile}"
                                  if REFUSED[status] == "sign_in_required" else f"Site returned HTTP {status}")
            if status >= 400 or status == 0:
                raise ScrapeError("http_error", f"Site returned HTTP {status}")
            check_url(page.url, settings.allow_test_loopback)
            if wait_selector:
                await page.locator(wait_selector).first.wait_for(state="attached")
            else:
                try:
                    await page.wait_for_load_state("networkidle", timeout=6000)
                except Exception:
                    pass
            hidden = await page.evaluate(HIDDEN_REMOVAL)
            body = (await page.content()).encode()
            final_url = page.url
        if len(body) > settings.max_body:
            raise ScrapeError("body_limit", "Rendered HTML exceeds the per-page limit")
    finally:
        try:
            await context.close()
        except Exception:
            pass
        await manager.stop()
    result = await parse(body, final_url, "text/html", recipe)
    result.update(url=final_url, status=status, rendered=True, cache="signed_in",
                  elapsed_ms=round((time.monotonic() - started) * 1000, 2), engine="Apex Flow Web Scraper signed-in reader",
                  content_sha256=hashlib.sha256(result["text"].encode()).hexdigest(), hosted_scraping_calls=0,
                  browser={"hidden_elements_removed": hidden})
    result["receipt"] = {"requested_url": url, "final_url": final_url, "status": status,
                         "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                         "signed_in_profile": profile, "read_on": "this machine", "rendered": True,
                         "content_sha256": result["content_sha256"],
                         "note": "Read through the owner's own signed-in browser profile. Not sent to or stored by the hosted service."}
    return result
