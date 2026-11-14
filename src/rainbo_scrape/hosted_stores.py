"""Hosted store watch: an account tracks Shopify stores and asks what is moving in them.

POST /v1/stores/track   read the store's public catalog now and save a snapshot. 1 credit per catalog page
                        read (250 products a page), charged only when the snapshot is saved.
POST /v1/stores/rising  products ranked by what changed between this account's snapshots. Free.
GET  /v1/stores         the stores this account tracks. Free.
POST /v1/stores/forget  stop tracking a store and delete its saved data. Free.

Each account sees only its own stores. The catalog is read through the guarded fetcher, so robots.txt is obeyed.
"""
import asyncio
import time

from aiohttp import web

from .billing import QuotaExceeded
from .policy import ScrapeError
from .storewatch import Watch, read_catalog, store_origin

MAX_PAGES = 20            # 5,000 products a store
MIN_INTERVAL = 3600       # one snapshot of a store an hour
STORE_LIMITS = {"free": 3}
PAID_STORES = 50
CREDITS_NOTE = "1 credit per catalog page read (250 products a page). Reading the ranking is free."


def store_limit(h, account):
    from .billing import PLANS
    limits = h.billing.limits(account)
    plan = next(name for name, value in PLANS.items() if value is limits)
    return STORE_LIMITS.get(plan, PAID_STORES)


def install(h, app, reader=None):
    read = reader or read_catalog
    running = 0
    Watch(h.db)   # create the tables once

    def only(body, allowed, message):
        if not isinstance(body, dict) or set(body) - allowed:
            raise ValueError(message)
        return body

    async def track_for(account, body):
        nonlocal running
        origin = store_origin(only(body, {"store"}, "Use store").get("store"))
        watch = Watch(h.db, account)
        _, last, _ = watch.latest(origin)
        if last and time.time() - last < MIN_INTERVAL:
            wait = int(MIN_INTERVAL - (time.time() - last)) + 1
            return web.json_response({"error": "This store was read less than an hour ago. Nothing was charged.",
                                      "code": "too_soon", "retryable": True, "retry_after": wait}, status=429,
                                     headers={"Retry-After": str(wait)})
        if not watch.tracked(origin) and len(watch.stores()) >= store_limit(h, account):
            return web.json_response({"error": "This plan tracks up to %d stores. Forget one or upgrade." % store_limit(h, account),
                                      "code": "store_limit", "retryable": False, "buy_url": h.site + "/#pricing"}, status=402)
        if running >= 2:
            raise web.HTTPTooManyRequests(headers={"Retry-After": "10"})
        from .hosted_features import admit_attempt
        admit_attempt(h, account)
        try:
            h.billing.reserve(account, 1)
        except QuotaExceeded:
            return web.json_response({"error": "Not enough credits", "code": "insufficient_credits", "retryable": False,
                                      "credits_left": h.billing.remaining(account), "buy_url": h.site + "/#pricing"}, status=402)
        charged = 1
        running += 1
        try:
            try:
                async with asyncio.timeout(120):
                    products, pages = await read(origin, MAX_PAGES)
            except ValueError:
                raise
            except (OSError, ScrapeError, TimeoutError):
                h.billing.refund(account, charged)
                charged = 0
                return web.json_response({"error": "The store did not answer. Nothing was charged; retry shortly.",
                                          "code": "source_failed", "retryable": True, "retry_after": 30}, status=502)
            extra = max(pages, 1) - 1
            if extra:
                try:
                    h.billing.reserve(account, extra)
                except QuotaExceeded:
                    h.billing.refund(account, charged)
                    charged = 0
                    return web.json_response({"error": "This store needs %d credits to read." % (extra + 1), "code": "insufficient_credits",
                                              "retryable": False, "credits_left": h.billing.remaining(account),
                                              "buy_url": h.site + "/#pricing"}, status=402)
                charged += extra
            result = watch.record(origin, products)
            result["events"] = result["events"][:500]
            spent, charged = charged, 0
            return web.json_response({"credits_used": spent, "credits_left": h.billing.remaining(account), "result": result})
        finally:
            running -= 1
            if charged:
                h.billing.refund(account, charged)

    async def rising_for(account, body):
        body = only(body, {"store", "days", "limit"}, "Use store and optional days and limit")
        origin = store_origin(body.get("store"))
        result = Watch(h.db, account).rising(origin, days=body.get("days", 7), limit=body.get("limit", 20))
        return web.json_response({"credits_used": 0, "credits_left": h.billing.remaining(account), "result": result})

    async def listing_for(account, body=None):
        return web.json_response({"stores": Watch(h.db, account).stores(), "limit": store_limit(h, account), "credits": CREDITS_NOTE})

    async def forget_for(account, body):
        origin = store_origin(only(body, {"store"}, "Use store").get("store"))
        return web.json_response({"store": origin, "forgotten": Watch(h.db, account).forget(origin)})

    async def track(request):
        return await track_for(request["account"], await request.json())

    async def rising(request):
        return await rising_for(request["account"], await request.json())

    async def listing(request):
        return await listing_for(request["account"])

    async def forget(request):
        return await forget_for(request["account"], await request.json())

    app.router.add_get("/v1/stores", listing)
    app.router.add_post("/v1/stores/track", track)
    app.router.add_post("/v1/stores/rising", rising)
    app.router.add_post("/v1/stores/forget", forget)
    return {"/stores": listing_for, "/stores/track": track_for, "/stores/rising": rising_for, "/stores/forget": forget_for}
