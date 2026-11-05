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
    parser = argparse.ArgumentParser(prog="rainbo-scrape")
    parser.add_argument("command", choices=["health", "scrape", "fetch", "map", "status", "results", "jobs",
                                            "cancel", "worker", "web", "mcp"])
    parser.add_argument("value", nargs="?")
    parser.add_argument("--render", choices=["auto", "never", "always"], default="auto")
    parser.add_argument("--pages", type=int, default=1)
    parser.add_argument("--depth", type=int, default=0)
    parser.add_argument("--port", type=int, default=8840)
    parser.add_argument("--max-age", type=int, default=None, help="cache freshness in milliseconds (0 = always fresh)")
    parser.add_argument("--limit", type=int, default=5000)
    parser.add_argument("--wait", action="store_true", help="scrape: block until the job ends, then print the result")
    parser.add_argument("--track-changes", action="store_true")
    parser.add_argument("--json", action="store_true", help="fetch: print the full result as JSON")
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
    if args.command == "fetch":
        from .client import scrape
        options = {"render": args.render}
        if args.max_age is not None:
            options["max_age"] = args.max_age
        page = scrape(args.value, **options)
        print(json.dumps(page, indent=2, ensure_ascii=False) if args.json else page.markdown)
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
