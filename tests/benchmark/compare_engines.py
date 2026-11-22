"""Run one signed, permissioned gold set through Apex or Firecrawl.

Each invocation selects exactly one engine. Run the same manifest once per
engine and feed each output to goldset_evaluator.py. Network calls are
sequential, never retried, and require an explicit billable-run flag.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp

try:
    from .goldset_evaluator import GoldSetError, _key_from_env, _read_json, verify_manifest
except ImportError:  # direct script execution from tests/benchmark
    from goldset_evaluator import GoldSetError, _key_from_env, _read_json, verify_manifest


DEFAULT_APEX_URL = "https://webscraper.apexflowlabs.com"
DEFAULT_FIRECRAWL_URL = "https://api.firecrawl.dev"
MAX_RESPONSE_BYTES = 20 * 1024 * 1024


def _field_type(value):
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, str):
        return {"type": "string"}
    if isinstance(value, list):
        types = sorted({json.dumps(_field_type(item), sort_keys=True) for item in value})
        items = json.loads(types[0]) if len(types) == 1 else {"anyOf": [json.loads(item) for item in types]}
        return {"type": "array", "items": items} if types else {"type": "array"}
    if isinstance(value, dict):
        props = {str(k): _field_type(v) for k, v in value.items()}
        return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}
    raise GoldSetError("gold-set expected values must be JSON values")


def schema_for(case):
    """Build an equivalent nested object schema from the case's dotted paths."""
    root = {"_node": True, "properties": {}, "required": set()}
    for path, rule in case["expected_fields"].items():
        parts = path.split(".")
        if any(not p for p in parts):
            raise GoldSetError(f"invalid expected field path: {path!r}")
        cursor = root
        for part in parts[:-1]:
            cursor["required"].add(part)
            child = cursor["properties"].setdefault(part, {"_node": True, "properties": {}, "required": set()})
            if not child.get("_node"):
                raise GoldSetError(f"conflicting nested expected field path: {path!r}")
            cursor = child
        key = parts[-1]
        cursor["required"].add(key)
        if key in cursor["properties"]:
            raise GoldSetError(f"conflicting expected field paths at: {path!r}")
        expected_type = _field_type(rule["value"])
        cursor["properties"][key] = (expected_type if rule["value"] is None else
                                      {"anyOf": [expected_type, {"type": "null"}]})

    def finish(node):
        out = {"type": "object", "properties": {}, "required": sorted(node["required"]),
               "additionalProperties": False}
        for key, value in node["properties"].items():
            out["properties"][key] = finish(value) if value.get("_node") else value
        return out

    return finish(root)


def _endpoint(engine, base_url, allow_custom_host=False):
    parts = urlsplit(base_url)
    hostname = (parts.hostname or "").lower().rstrip(".")
    loopback = hostname in {"localhost", "127.0.0.1", "::1"}
    official = {"apex": "webscraper.apexflowlabs.com", "firecrawl": "api.firecrawl.dev"}[engine]
    if parts.scheme != "https" and not loopback:
        raise GoldSetError("API endpoint must use HTTPS")
    if not loopback and hostname != official and not allow_custom_host:
        raise GoldSetError("custom API hosts require --allow-custom-api-host")
    try:
        port = parts.port
    except ValueError as exc:
        raise GoldSetError("API endpoint has an invalid port") from exc
    if not loopback and port not in (None, 443):
        raise GoldSetError("API endpoint must use the standard HTTPS port")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise GoldSetError("API endpoint must not contain credentials, query, or fragment")
    path = parts.path.rstrip("/")
    if engine == "apex":
        if path.endswith("/v2"):
            return base_url.rstrip("/") + "/scrape"
        return base_url.rstrip("/") + "/v2/scrape"
    if path.endswith("/v2"):
        return base_url.rstrip("/") + "/scrape"
    return base_url.rstrip("/") + "/v2/scrape"


def _request_body(case):
    fields = ", ".join(case["expected_fields"])
    prompt = "Extract these fields from the page, using null when a value is absent: " + fields
    return {"url": case["url"], "onlyMainContent": True, "maxAge": 0,
            "formats": [{"type": "json", "schema": schema_for(case), "prompt": prompt}]}


def _read_result(status, payload):
    if status < 200 or status >= 300 or not isinstance(payload, dict) or payload.get("success") is False:
        code = payload.get("code") if isinstance(payload, dict) else None
        code = str(code or f"http_{status}")[:80]
        blocked = {"robots_denied", "access_challenge", "captcha_detected", "source_blocked"}
        return {"status": "blocked" if code in blocked else "error", "error_code": code, "fields": {}}
    data = payload.get("data")
    fields = data.get("json") if isinstance(data, dict) else None
    if not isinstance(fields, dict):
        return {"status": "error", "error_code": "missing_structured_result", "fields": {}}
    row = {"status": "success", "fields": fields}
    credits = payload.get("creditsUsed", payload.get("credits_used"))
    if isinstance(credits, (int, float)) and not isinstance(credits, bool) and credits >= 0:
        row["cost"] = {"credits": credits}
    return row


async def run_cases(engine, endpoint, api_key, manifest, selected, out_path):
    started = datetime.now(timezone.utc).isoformat()
    timeout = aiohttp.ClientTimeout(total=120, connect=15)
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    raw_dir = out_path.with_name(out_path.stem + ".raw")
    raw_dir.mkdir(parents=True, exist_ok=False)
    pages = []
    connector = aiohttp.TCPConnector(ssl=True)
    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        for case in selected:
            started_page = time.monotonic()
            request_body = _request_body(case)
            try:
                async with session.post(endpoint, headers=headers, json=request_body, allow_redirects=False) as response:
                    status = response.status
                    body = await response.content.read(MAX_RESPONSE_BYTES + 1)
                    if len(body) > MAX_RESPONSE_BYTES:
                        payload = None
                    else:
                        try:
                            payload = json.loads(body.decode("utf-8"))
                        except (UnicodeDecodeError, json.JSONDecodeError):
                            payload = None
                slug = re.sub(r"[^A-Za-z0-9_.-]", "_", case["id"])[:80]
                raw_file = raw_dir / (slug + "-" + hashlib.sha256(case["id"].encode()).hexdigest()[:12] + ".json")
                raw_file.write_text(json.dumps({"http_status": status, "request": request_body, "response": payload,
                                               "response_too_large": payload is None and len(body) > MAX_RESPONSE_BYTES}, ensure_ascii=False,
                                               indent=2) + "\n", encoding="utf-8")
                row = ({"status": "error", "error_code": "response_too_large", "fields": {}}
                       if len(body) > MAX_RESPONSE_BYTES else _read_result(status, payload))
            except (aiohttp.ClientError, asyncio.TimeoutError):
                row = {"status": "error", "error_code": "request_failed", "fields": {}}
            row.update(id=case["id"], url=case["url"], latency_ms=round((time.monotonic() - started_page) * 1000))
            pages.append(row)
            print(f"{engine} {case['id']}: {row['status']} ({row['latency_ms']} ms)", flush=True)

    run = {"dataset_id": manifest["dataset_id"], "label": f"{engine}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
           "engine": engine, "engine_version": "remote-api", "started_at": started,
           "endpoint": endpoint, "completed_at": datetime.now(timezone.utc).isoformat(), "pages": pages}
    out_path.write_text(json.dumps(run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return run


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("engine", choices=("apex", "firecrawl"))
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--only", default="", help="comma-separated signed case IDs")
    parser.add_argument("--allow-billable-requests", action="store_true",
                        help="explicitly allow API requests that may consume paid credits")
    parser.add_argument("--key-env", help="API key environment-variable name; defaults by engine")
    parser.add_argument("--base-url", help="API base URL; defaults to the hosted service")
    parser.add_argument("--allow-custom-api-host", action="store_true",
                        help="allow sending the selected API key to a non-default HTTPS host")
    args = parser.parse_args(argv)
    try:
        if not args.allow_billable_requests:
            raise GoldSetError("no requests sent; pass --allow-billable-requests to authorize API usage")
        signing_key = _key_from_env("APEX_GOLDSET_SIGNING_KEY")
        manifest = verify_manifest(_read_json(args.manifest), signing_key)
        selected_ids = set(filter(None, args.only.split(",")))
        cases = manifest["cases"]
        if selected_ids:
            unknown = selected_ids - {case["id"] for case in cases}
            if unknown:
                raise GoldSetError("unknown case IDs: " + ", ".join(sorted(unknown)))
            cases = [case for case in cases if case["id"] in selected_ids]
        if not cases:
            raise GoldSetError("no cases selected")
        key_env = args.key_env or ("APEX_SCRAPER_API_KEY" if args.engine == "apex" else "FIRECRAWL_API_KEY")
        api_key = os.environ.get(key_env)
        if not api_key:
            raise GoldSetError(f"environment variable {key_env} is not set")
        base_url = args.base_url or (os.environ.get("APEX_API_URL", DEFAULT_APEX_URL) if args.engine == "apex"
                                     else os.environ.get("FIRECRAWL_API_URL", DEFAULT_FIRECRAWL_URL))
        endpoint = _endpoint(args.engine, base_url, allow_custom_host=args.allow_custom_api_host)
        raw_dir = args.output.with_name(args.output.stem + ".raw")
        if args.output.exists() or raw_dir.exists():
            raise GoldSetError("output or raw-response path already exists; choose a new output path")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        result = asyncio.run(run_cases(args.engine, endpoint, api_key, manifest, cases, args.output))
        print(json.dumps({"engine": args.engine, "cases": len(result["pages"]), "output": str(args.output),
                          "raw_directory": str(args.output.with_name(args.output.stem + ".raw"))}))
        return 0
    except GoldSetError as exc:
        parser.exit(2, f"compare_engines: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
