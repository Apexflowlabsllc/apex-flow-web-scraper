import argparse
import asyncio
import json
import sys
import time


def _utf8():
    # Scraped pages carry arbitrary Unicode; a cp1252 Windows console must not crash the CLI
    # (measured 2026-11-05: `results` of a completed job raised UnicodeEncodeError).
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv=None):
    _utf8()
    from .policy import ScrapeError
    parser = argparse.ArgumentParser(prog="rainbo-scrape")
    parser.add_argument("command", choices=["health", "scrape", "fetch", "map", "status", "results", "jobs",
                                            "cancel", "worker", "web", "mcp", "search", "index", "crawl-web", "llms-txt", "api", "new-key", "login"])
    parser.add_argument("value", nargs="?")
    parser.add_argument("--render", choices=["auto", "never", "always"], default="auto")
    parser.add_argument("--pages", type=int, default=1)
    parser.add_argument("--depth", type=int, default=0)
    parser.add_argument("--port", type=int, default=8840)
    parser.add_argument("--max-age", type=int, default=None, help="cache freshness in milliseconds (0 = always fresh)")
    parser.add_argument("--limit", type=int, default=5000)
    parser.add_argument("--wait", action="store_true", help="scrape: block until the job ends, then print the result")
    parser.add_argument("--track-changes", action="store_true")
    parser.add_argument("--plan", choices=["free", "starter", "growth", "scale"], default="free", help="new-key: plan for the account")
    parser.add_argument("--full", action="store_true", help="llms-txt: also include every page's Markdown (llms-full.txt)")
    parser.add_argument("--json", action="store_true", help="fetch: print the full result as JSON")
    parser.add_argument("--signed-in", action="store_true",
                        help="fetch: read through your own signed-in browser profile on this machine (run login first)")
    parser.add_argument("--profile", default="default", help="login / fetch --signed-in: which saved browser profile to use")
    args = parser.parse_args(argv)
    if args.command == "mcp":
        from .mcp_server import mcp
        mcp.run()
        return
    if args.command == "worker":
        from .worker import serve
        asyncio.run(serve())
        return
    if args.command == "web":
        from .web import run
        run(port=args.port)
        return
    if args.command == "login":
        # Opens a real browser window. You sign in yourself; the session stays in a profile folder on this machine.
        from .signed_in import sign_in
        print("A browser window is opening. Sign in, then close the window.", file=sys.stderr, flush=True)
        try:
            saved = asyncio.run(sign_in(args.value, profile=args.profile))
        except (ValueError, ScrapeError) as exc:
            sys.exit("error: " + str(exc))
        print("profile=" + saved["profile"])
        print("folder=" + saved["folder"])
        return
    if args.command == "fetch" and args.signed_in:
        from .signed_in import read_signed_in
        try:
            page = asyncio.run(read_signed_in(args.value, profile=args.profile))
        except (ValueError, ScrapeError) as exc:
            sys.exit("error: " + str(exc))
        print(json.dumps(page, indent=2, ensure_ascii=False) if args.json else page.get("markdown") or page.get("text") or "")
        return
    if args.command == "fetch":
        from .client import scrape
        options = {"render": args.render}
        if args.max_age is not None:
            options["max_age"] = args.max_age
        page = scrape(args.value, **options)
        print(json.dumps(page, indent=2, ensure_ascii=False) if args.json else page.markdown)
        return
    if args.command == "api":
        from .api import run as run_api
        run_api(port=args.port if args.port != 8840 else 8850)
        return
    if args.command == "new-key":
        from .billing import Billing
        from .settings import Settings
        billing = Billing(Settings().data_dir / "billing.db")
        account = billing.create_account(args.value or "unnamed", args.plan)
        print("account=" + account)
        print("api_key=" + billing.new_key(account))  # shown once; only its hash is stored
        return
    if args.command == "llms-txt":
        from .llmstxt import build
        print(asyncio.run(build(args.value, limit=min(max(args.pages, 2), 200) if args.pages > 1 else 30, full=args.full)), end="")
        return
    if args.command == "map":
        from .client import map_site
        found = map_site(args.value, limit=args.limit)
        for item in found["urls"]:
            print(item["url"])
        print(f"# {found['count']} urls, {found['sitemaps_read']} sitemap files read, truncated={found['truncated']}",
              file=sys.stderr)
        return
    from .service import Service
    s = Service()
    if args.command == "crawl-web":
        # Open-web crawl: start from a seed URL, follow links to any public site (robots.txt and per-host
        # pacing still apply), and index every page. Bounded by --pages (max 1000 per job); run repeatedly to grow.
        job = s.submit({"urls": [args.value], "max_pages": min(max(args.pages, 2), 1000), "max_depth": max(args.depth, 2),
                        "render": "never", "follow_external": True, "concurrency": 4, "max_seconds": 3600})
        print("job_id=" + job["id"], flush=True)
        return
    if args.command == "index":
        # Grow the owned search index: discover a site's URLs (sitemaps + links), then crawl them in one job.
        from .client import map_site
        found = map_site(args.value, limit=min(args.pages if args.pages > 1 else 100, 100))
        job = s.submit({"urls": [u["url"] for u in found["urls"]], "max_pages": max(len(found["urls"]), 1),
                        "max_depth": 0, "render": args.render})
        print("job_id=" + job["id"], flush=True)
        while s.store.status(job["id"])["state"] in {"queued", "running"}:
            time.sleep(0.5)
        print(json.dumps(s.store.status(job["id"])["counts"]))
        return
    if args.command == "search":
        print(json.dumps(s.store.search(args.value, args.limit if args.limit != 5000 else 10), indent=2, ensure_ascii=False))
        return
    if args.command == "health":
        result = s.health()
    elif args.command == "scrape":
        request = {"urls": [args.value], "max_pages": args.pages, "max_depth": args.depth, "render": args.render,
                   "change_tracking": args.track_changes}
        if args.max_age is not None:
            request["max_age"] = args.max_age
        result = s.submit(request)
        # First line is stable and script-parseable: job_id=<16 hex characters>
        print("job_id=" + result["id"], flush=True)
        if args.wait:
            while s.store.status(result["id"])["state"] in {"queued", "running"}:
                time.sleep(0.3)
            print(json.dumps(s.store.results(result["id"]), indent=2, ensure_ascii=False))
            return
    elif args.command == "jobs":
        result = s.store.list_jobs()
    elif args.command == "status":
        result = s.store.status(args.value)
    elif args.command == "cancel":
        result = s.store.cancel(args.value)
    else:
        result = s.store.results(args.value)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
