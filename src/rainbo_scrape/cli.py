import argparse
import asyncio
import json
from .service import Service


def main():
    parser = argparse.ArgumentParser(prog="rainbo-scrape")
    parser.add_argument("command", choices=["health", "scrape", "status", "results", "jobs", "cancel", "worker", "web", "mcp"])
    parser.add_argument("value", nargs="?")
    parser.add_argument("--render", choices=["auto", "never", "always"], default="auto")
    parser.add_argument("--pages", type=int, default=1)
    parser.add_argument("--port", type=int, default=8840)
    args = parser.parse_args()
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
    s = Service()
    if args.command == "health":
        result = s.health()
    elif args.command == "scrape":
        result = s.submit({"urls": [args.value], "max_pages": args.pages, "render": args.render})
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
