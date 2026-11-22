"""Short-lived, domain-scoped browser sessions for stateful page interaction.

Sessions keep browser state only in memory, accept declarative actions rather than
caller code, and send every browser request through the scraper's guarded fetcher.
"""
import asyncio
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlsplit

from .browser import perform_actions
from .engine import validate_actions
from .network import Fetcher
from .policy import ScrapeError, check_url
from .settings import Settings, brave_path
from .store import Store


def origin_of(url):
    parsed = urlsplit(check_url(url))
    port = parsed.port
    default = 443 if parsed.scheme == "https" else 80
    authority = parsed.hostname.lower()
    if port and port != default:
        authority += f":{port}"
    return f"{parsed.scheme}://{authority}"


def validate_origins(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 10:
        raise ValueError("allowedOrigins must contain 1–10 public HTTP(S) origins")
    origins = set()
    for item in value:
        if not isinstance(item, str):
            raise ValueError("allowedOrigins entries must be origin URLs")
        parsed = urlsplit(check_url(item))
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment or parsed.username or parsed.password:
            raise ValueError("allowedOrigins entries must be origins without paths, queries, or credentials")
        origins.add(origin_of(item))
    return frozenset(origins)


def iso(stamp):
    return datetime.fromtimestamp(stamp, timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class BrowserSession:
    id: str
    account: str
    created: float
    expires: float
    activity_ttl: int
    allowed_origins: frozenset
    fetcher: object
    context: object
    page: object
    last_activity: float
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    requests: int = 0
    transferred: int = 0


class InteractService:
    MAX_SESSIONS = 2
    MAX_REQUESTS = 120
    MAX_BYTES = 20 * 1024 * 1024

    def __init__(self, hosted, opener=None, action_runner=None):
        self.hosted = hosted
        self.opener = opener or self._open_real
        self.action_runner = action_runner or perform_actions
        self.sessions = {}
        self.destroyed_sessions = []
        self.lock = asyncio.Lock()
        self.sweeper = None
        self.browser_slots = asyncio.BoundedSemaphore(self.MAX_SESSIONS)

    async def _open_real(self, origins, usage):
        if not brave_path():
            raise ScrapeError("browser_unavailable", "This service has no configured browser runtime for interactive sessions")
        settings = Settings().prepare()
        fetcher = Fetcher(settings, Store(settings), browser_slots=self.browser_slots)
        await fetcher.__aenter__()
        context = None
        try:
            from .browser import get_browser
            browser = await get_browser(fetcher)
            context = await browser.new_context(service_workers="block", accept_downloads=False,
                                                user_agent=settings.user_agent)

            async def guarded(route):
                request = route.request
                try:
                    if request.method not in {"GET", "HEAD"}:
                        raise ScrapeError("blocked_method", "Interactive sessions allow read-only GET and HEAD requests")
                    checked = check_url(request.url)
                    if origin_of(checked) not in origins:
                        raise ScrapeError("blocked_origin", "Browser request origin is outside this session's allowlist")
                    if request.resource_type in {"image", "media", "font"}:
                        await route.abort()
                        return
                    usage["requests"] += 1
                    if usage["requests"] > self.MAX_REQUESTS:
                        raise ScrapeError("session_budget", "Interactive session request budget reached")
                    response = await fetcher.get(checked, robots=True, allowed_origins=origins)
                    final_url = check_url(response["url"])
                    if origin_of(final_url) not in origins:
                        raise ScrapeError("blocked_origin", "Redirected request origin is outside this session's allowlist")
                    usage["bytes"] += len(response["body"])
                    if usage["bytes"] > self.MAX_BYTES:
                        raise ScrapeError("session_budget", "Interactive session transfer budget reached")
                    await route.fulfill(status=response["status"], body=response["body"],
                                        headers={"content-type": response["headers"].get("content-type", "text/plain")})
                except Exception:
                    await route.abort()

            await context.route("**/*", guarded)
            await context.route_web_socket("**/*", lambda socket: socket.close())
            page = await context.new_page()
            page.set_default_timeout(8000)
            return fetcher, context, page
        except BaseException as exc:
            if context:
                try:
                    await context.close()
                except Exception:
                    pass
            await fetcher.__aexit__(None, None, None)
            if isinstance(exc, (asyncio.CancelledError, ScrapeError)):
                raise
            raise ScrapeError("browser_unavailable", "Browser session could not be started") from exc

    async def _open(self, origins, usage):
        opened = await self.opener(origins, usage)
        return opened

    async def _close(self, session):
        try:
            await session.context.close()
        finally:
            await session.fetcher.__aexit__(None, None, None)

    async def _expire_loop(self):
        try:
            while True:
                await asyncio.sleep(5)
                now = time.time()
                async with self.lock:
                    candidates = [session for session in self.sessions.values()
                                  if now >= session.expires or now - session.last_activity >= session.activity_ttl]
                expired = []
                for session in candidates:
                    await session.lock.acquire()
                    async with self.lock:
                        if (self.sessions.get(session.id) is session and
                                (now >= session.expires or now - session.last_activity >= session.activity_ttl)):
                            self.sessions.pop(session.id, None)
                            self._remember_destroyed(session)
                            expired.append(session)
                        else:
                            session.lock.release()
                for session in expired:
                    try:
                        await self._close(session)
                    except Exception:
                        pass
                    finally:
                        session.lock.release()
        except asyncio.CancelledError:
            raise

    def _remember_destroyed(self, session):
        self.destroyed_sessions.append({"account": session.account, "id": session.id, "status": "destroyed",
            "createdAt": iso(session.created), "lastActivity": iso(session.last_activity),
            "expiresAt": iso(session.expires), "allowedOrigins": sorted(session.allowed_origins),
            "destroyedAt": time.time()})
        cutoff = time.time() - 3600
        self.destroyed_sessions = [item for item in self.destroyed_sessions if item["destroyedAt"] >= cutoff]

    async def create(self, account, body):
        if not isinstance(body, dict) or set(body) - {"ttl", "activityTtl", "allowedOrigins", "url", "streamWebView", "blockAds"}:
            raise ValueError("Session creation accepts ttl, activityTtl, allowedOrigins, url, streamWebView and blockAds only")
        ttl = body.get("ttl", 300)
        activity_ttl = body.get("activityTtl", 120)
        if isinstance(ttl, bool) or not isinstance(ttl, int) or not 30 <= ttl <= 900:
            raise ValueError("ttl must be an integer from 30 to 900 seconds")
        if isinstance(activity_ttl, bool) or not isinstance(activity_ttl, int) or not 10 <= activity_ttl <= 300:
            raise ValueError("activityTtl must be an integer from 10 to 300 seconds")
        if body.get("streamWebView", False) is not False:
            raise ValueError("Live browser streaming is not available on this service")
        if body.get("blockAds", False) is not False:
            raise ValueError("blockAds is not yet supported for interactive sessions")
        origins = validate_origins(body.get("allowedOrigins"))
        initial = body.get("url")
        if initial is not None:
            if not isinstance(initial, str) or origin_of(initial) not in origins:
                raise ValueError("url must belong to one of allowedOrigins")
        if self.hosted.billing.limits(account)["price"] == 0:
            raise ScrapeError("paid_plan_required", "Interactive browser sessions require a paid plan")
        async with self.lock:
            now = time.time()
            if len(self.sessions) >= self.MAX_SESSIONS or any(s.account == account for s in self.sessions.values()):
                raise ScrapeError("session_capacity", "Interactive session capacity is full; close an active session and retry")
        try:
            self.hosted.billing.charge(account, "render")
        except Exception as exc:
            if hasattr(exc, "needed"):
                raise ScrapeError("insufficient_credits", str(exc)) from exc
            raise
        usage = {"requests": 0, "bytes": 0}
        opened = None
        session = None
        try:
            opened = await self._open(origins, usage)
            fetcher, context, page = opened
            if initial:
                async with asyncio.timeout(20):
                    await page.goto(check_url(initial), wait_until="domcontentloaded")
            now = time.time()
            session = BrowserSession("interact_" + secrets.token_urlsafe(16), account, now,
                                     now + ttl, activity_ttl, origins, fetcher, context, page, now,
                                     requests=usage["requests"], transferred=usage["bytes"])
            async with self.lock:
                if len(self.sessions) >= self.MAX_SESSIONS or any(s.account == account for s in self.sessions.values()):
                    raise ScrapeError("session_capacity", "Interactive session capacity is full; close an active session and retry")
                self.sessions[session.id] = session
                if self.sweeper is None or self.sweeper.done():
                    self.sweeper = asyncio.create_task(self._expire_loop())
            return {"success": True, "id": session.id, "expiresAt": iso(session.expires),
                    "actionsUrl": f"/v2/interact/{session.id}/execute",
                    "allowedOrigins": sorted(origins), "creditCost": 3}
        except BaseException:
            try:
                if session:
                    async with self.lock:
                        if self.sessions.get(session.id) is session:
                            self.sessions.pop(session.id, None)
                if opened:
                    try:
                        await opened[1].close()
                    finally:
                        await opened[0].__aexit__(None, None, None)
            except Exception:
                pass
            finally:
                self.hosted.billing.refund(account, 3)
            raise

    def list(self, account, status=None):
        if status not in (None, "active", "destroyed"):
            raise ValueError("status must be active or destroyed")
        now = time.time()
        sessions = [session for session in self.sessions.values()
                    if session.account == account and now < session.expires
                    and now - session.last_activity < session.activity_ttl]
        if status == "destroyed":
            entries = [item for item in self.destroyed_sessions if item["account"] == account]
            return {"success": True, "sessions": [{key: value for key, value in item.items() if key != "account" and key != "destroyedAt"}
                                                     for item in entries]}
        return {"success": True, "sessions": [{"id": s.id, "status": "active",
                "createdAt": iso(s.created), "lastActivity": iso(s.last_activity),
                "expiresAt": iso(s.expires), "allowedOrigins": sorted(s.allowed_origins)} for s in sessions]}

    async def execute(self, account, ident, body):
        if not isinstance(body, dict) or set(body) - {"url", "actions"}:
            raise ValueError("Execution accepts an optional url and a bounded actions list; executable code is disabled")
        action_list = validate_actions(body.get("actions", []))
        async with self.lock:
            session = self.sessions.get(ident)
        if not session or session.account != account:
            raise ScrapeError("not_found", "Interactive session not found for this account")
        now = time.time()
        if now >= session.expires or now - session.last_activity >= session.activity_ttl:
            await self.delete(account, ident)
            raise ScrapeError("session_expired", "Interactive session has expired")
        target = body.get("url")
        if target is not None:
            if not isinstance(target, str) or origin_of(target) not in session.allowed_origins:
                raise ValueError("url must belong to one of this session's allowedOrigins")
        try:
            self.hosted.billing.charge(account, "render")
        except Exception as exc:
            if hasattr(exc, "needed"):
                raise ScrapeError("insufficient_credits", str(exc)) from exc
            raise
        try:
            async with session.lock, asyncio.timeout(20):
                async with self.lock:
                    if self.sessions.get(ident) is not session:
                        raise ScrapeError("not_found", "Interactive session not found for this account")
                if target:
                    await session.page.goto(check_url(target), wait_until="domcontentloaded")
                await self.action_runner(session.page, action_list)
                page_url = session.page.url
                if page_url != "about:blank" and origin_of(page_url) not in session.allowed_origins:
                    raise ScrapeError("blocked_origin", "Page navigated outside this session's allowedOrigins")
                title = await session.page.title()
                text = await session.page.locator("body").inner_text(timeout=5000) if page_url != "about:blank" else ""
                session.last_activity = time.time()
                return {"success": True, "id": ident, "url": page_url, "title": title,
                        "text": text[:100000], "creditCost": 3,
                        "expiresAt": iso(session.expires)}
        except BaseException:
            self.hosted.billing.refund(account, 3)
            raise

    async def delete(self, account, ident):
        async with self.lock:
            session = self.sessions.get(ident)
            if not session or session.account != account:
                raise ScrapeError("not_found", "Interactive session not found for this account")
        await session.lock.acquire()
        async with self.lock:
            if self.sessions.get(ident) is not session:
                session.lock.release()
                raise ScrapeError("not_found", "Interactive session not found for this account")
            self.sessions.pop(ident, None)
            self._remember_destroyed(session)
        try:
            await self._close(session)
        finally:
            session.lock.release()
        return {"success": True, "id": ident, "status": "destroyed"}

    async def close_all(self, app=None):
        if self.sweeper and not self.sweeper.done():
            self.sweeper.cancel()
            try:
                await self.sweeper
            except asyncio.CancelledError:
                pass
        async with self.lock:
            sessions = list(self.sessions.values())
            self.sessions.clear()
        await asyncio.gather(*(self._close(session) for session in sessions), return_exceptions=True)
