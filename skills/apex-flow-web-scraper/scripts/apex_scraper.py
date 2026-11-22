#!/usr/bin/env python3
"""Small dependency-free CLI for the hosted Apex Flow Web Scraper API."""

from __future__ import annotations

import argparse
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen


DEFAULT_BASE = "https://webscraper.apexflowlabs.com"


class ApiError(Exception):
    def __init__(self, message: str, exit_code: int = 1):
        super().__init__(message)
        self.exit_code = exit_code


def request_json(method: str, path: str, payload: dict | None = None) -> dict:
    api_key = os.environ.get("APEX_SCRAPER_API_KEY")
    if not api_key:
        raise ApiError("Set APEX_SCRAPER_API_KEY in the AI client's environment, then restart the client.", 2)

    base = os.environ.get("APEX_SCRAPER_API_BASE", DEFAULT_BASE).rstrip("/")
    parsed_base = urlsplit(base)
    loopback = parsed_base.hostname in {"localhost", "127.0.0.1", "::1"}
    if not parsed_base.netloc or (parsed_base.scheme != "https" and not (parsed_base.scheme == "http" and loopback)):
        raise ApiError("APEX_SCRAPER_API_BASE must use HTTPS (HTTP is allowed only for localhost).", 2)
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = Request(base + path, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=100) as response:
            raw = response.read(1_100_000)
    except HTTPError as exc:
        raw = exc.read(64_000)
        try:
            detail = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            detail = {"error": "The API returned an unreadable error."}
        output = {"http_status": exc.code, **detail}
        raise ApiError(json.dumps(output, ensure_ascii=False), 3) from None
    except (URLError, TimeoutError, OSError) as exc:
        raise ApiError(f"Could not reach the Apex Flow API: {exc.reason if isinstance(exc, URLError) else exc}", 4) from None

    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ApiError("The Apex Flow API returned invalid JSON.", 5) from None


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="apex_scraper.py", description="Read public pages with Apex Flow Web Scraper.")
    commands = parser.add_subparsers(dest="command", required=True)

    scrape = commands.add_parser("scrape", help="Read one public URL")
    scrape.add_argument("url")
    scrape.add_argument("--render", choices=("auto", "always", "never"), default="auto")
    scrape.add_argument("--max-tokens", type=int)
    scrape.add_argument("--max-age", type=int)
    scrape.add_argument("--fallback", choices=("archive", "none"))
    scrape.add_argument("--output", choices=("markdown", "text", "data", "evidence", "chunks", "diff"), action="append")

    batch = commands.add_parser("batch", help="Read up to ten known URLs in parallel")
    batch.add_argument("--url", action="append", required=True)
    batch.add_argument("--render", choices=("auto", "always", "never"), default="auto")

    changes = commands.add_parser("changes", help="Compare up to ten URLs with this account's previous reads")
    changes.add_argument("--url", action="append", required=True)
    changes.add_argument("--render", choices=("auto", "always", "never"), default="never")

    site_map = commands.add_parser("map", help="Discover a site's public URLs")
    site_map.add_argument("url")
    site_map.add_argument("--limit", type=int, default=100)
    site_map.add_argument("--search")

    crawl = commands.add_parser("crawl", help="Start a bounded crawl of one site")
    crawl.add_argument("url")
    crawl.add_argument("--limit", type=int, default=25)
    crawl.add_argument("--max-depth", type=int, default=3)
    crawl.add_argument("--include")
    crawl.add_argument("--render", choices=("auto", "always", "never"), default="auto")

    status = commands.add_parser("crawl-status", help="Read crawl status and a page of results")
    status.add_argument("job_id")
    status.add_argument("--offset", type=int, default=0)

    search = commands.add_parser("web-search", help="Search the open web (requires service search configuration)")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=10)

    saved = commands.add_parser("saved-search", help="Search this account's previously scraped pages")
    saved.add_argument("query")
    saved.add_argument("--limit", type=int, default=10)
    commands.add_parser("usage", help="Show account credits, limits, and recent usage")

    monitor = commands.add_parser("monitor", help="Manage scheduled change monitors")
    monitor_commands = monitor.add_subparsers(dest="monitor_command", required=True)
    create_monitor = monitor_commands.add_parser("create", help="Create a recurring monitor after explicit user consent")
    create_monitor.add_argument("url")
    create_monitor.add_argument("--webhook-url", required=True)
    create_monitor.add_argument("--interval-seconds", type=int, default=86400)
    create_monitor.add_argument("--confirm-recurring", action="store_true", required=True)
    monitor_commands.add_parser("list", help="List this account's monitors")
    for action in ("pause", "resume", "delete"):
        command = monitor_commands.add_parser(action, help=f"{action.title()} a scheduled monitor")
        command.add_argument("monitor_id")
    events = monitor_commands.add_parser("events", help="Read monitor changes and webhook delivery state")
    events.add_argument("monitor_id")
    events.add_argument("--limit", type=int, default=20)
    return parser


def execute(args: argparse.Namespace) -> dict:
    if args.command == "scrape":
        payload = {"url": args.url, "render": args.render}
        if args.max_tokens is not None:
            payload["max_tokens"] = args.max_tokens
        if args.max_age is not None:
            payload["max_age"] = args.max_age
        if args.fallback is not None:
            payload["fallback"] = args.fallback
        if args.output:
            payload["outputs"] = args.output
        return request_json("POST", "/v1/scrape", payload)
    if args.command == "batch":
        if len(args.url) > 10:
            raise ApiError("batch accepts at most ten --url values.", 2)
        return request_json("POST", "/v1/batch", {"urls": args.url, "render": args.render})
    if args.command == "changes":
        if len(args.url) > 10:
            raise ApiError("changes accepts at most ten --url values.", 2)
        return request_json("POST", "/v1/batch", {"urls": args.url, "render": args.render, "outputs": ["diff"]})
    if args.command == "map":
        payload = {"url": args.url, "limit": args.limit}
        if args.search:
            payload["search"] = args.search
        return request_json("POST", "/v1/map", payload)
    if args.command == "crawl":
        payload = {"url": args.url, "limit": args.limit, "max_depth": args.max_depth, "render": args.render}
        if args.include:
            payload["include"] = args.include
        return request_json("POST", "/v1/crawl", payload)
    if args.command == "crawl-status":
        if args.offset < 0:
            raise ApiError("offset must be zero or greater.", 2)
        query = urlencode({"offset": args.offset})
        return request_json("GET", f"/v1/crawl/{quote(args.job_id, safe='')}?{query}")
    if args.command == "web-search":
        return request_json("POST", "/v1/websearch", {"query": args.query, "limit": args.limit})
    if args.command == "saved-search":
        return request_json("POST", "/v1/search", {"query": args.query, "limit": args.limit})
    if args.command == "usage":
        return request_json("GET", "/v1/usage")
    if args.command == "monitor":
        if args.monitor_command == "create":
            return request_json("POST", "/v1/monitors/create", {
                "url": args.url, "webhook_url": args.webhook_url,
                "interval_seconds": args.interval_seconds, "confirm_recurring": True})
        if args.monitor_command == "list":
            return request_json("GET", "/v1/monitors/list")
        if args.monitor_command in {"pause", "resume", "delete"}:
            return request_json("POST", f"/v1/monitors/{args.monitor_command}", {"id": args.monitor_id})
        if args.monitor_command == "events":
            if not 1 <= args.limit <= 100:
                raise ApiError("monitor events --limit must be from 1 to 100.", 2)
            return request_json("POST", "/v1/monitors/events", {"id": args.monitor_id, "limit": args.limit})
    raise ApiError("Unsupported command.", 2)


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    try:
        result = execute(args)
    except ApiError as exc:
        print(str(exc), file=sys.stderr)
        return exc.exit_code
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
