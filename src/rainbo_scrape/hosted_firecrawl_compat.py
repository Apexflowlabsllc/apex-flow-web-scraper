"""A deliberately partial Firecrawl v2 wire adapter over Apex's hosted features.

This preserves Apex billing and source-safety rules. Unsupported input is
rejected instead of being accepted and silently ignored. See the site guide for
the exact supported subset; this is not a claim of complete SDK compatibility.
"""
import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import aiohttp
from aiohttp import web
from .billing import public_usage_key_id
from .extract import validate_attribute_selectors
from .hosted_search import normalize_domain_filters, provider_name
from .policy import PublicResolver, ScrapeError, check_url

INTERACT_SERVICE_KEY = web.AppKey("interact_service", object)
BATCH_LEASE_SECONDS = 30
BATCH_LEASE_RENEW_SECONDS = 8
BATCH_WEBHOOK_LEASE_SECONDS = 120
BATCH_WEBHOOK_MAX_BYTES = 128 * 1024
BATCH_WEBHOOK_EVENTS = {
    "started": "batch_scrape.started",
    "page": "batch_scrape.page",
    "completed": "batch_scrape.completed",
}


def _claim_firecrawl_batch(db, job, now, lease_seconds=BATCH_LEASE_SECONDS):
    """Claim or recover one persisted compatibility batch across service workers."""
    token = secrets.token_urlsafe(18)
    claimed = db.execute(
        "UPDATE firecrawl_batch_jobs SET status='scraping',lease_until=?,lease_token=? "
        "WHERE id=? AND status IN ('queued','scraping') "
        "AND (lease_until IS NULL OR lease_until<=?)",
        (now + lease_seconds, token, job, now),
    ).rowcount
    return token if claimed else None


def _claim_firecrawl_batch_webhook(db, event_id, now, lease_seconds=BATCH_WEBHOOK_LEASE_SECONDS):
    token = secrets.token_urlsafe(18)
    claimed = db.execute(
        "UPDATE firecrawl_batch_webhook_events SET lease_until=?,lease_token=?,attempts=attempts+1 "
        "WHERE id=? AND state='pending' AND next_attempt<=? "
        "AND (lease_until IS NULL OR lease_until<=?)",
        (now + lease_seconds, token, event_id, now, now),
    ).rowcount
    if not claimed:
        return None
    row = db.execute("SELECT attempts FROM firecrawl_batch_webhook_events WHERE id=? AND lease_token=?",
                     (event_id, token)).fetchone()
    return (token, row[0]) if row else None


def _batch_webhook_secret(hosted, account, job):
    digest = hmac.new(hosted.delivery_secret.encode(),
                      ("apex-firecrawl-batch-v1:" + account + ":" + job).encode(),
                      hashlib.sha256).digest()
    return "whsec_" + base64.urlsafe_b64encode(digest).decode().rstrip("=")


def _queue_firecrawl_batch_webhook(db, job, hook, short_name, data, page_seq=None):
    if not hook or BATCH_WEBHOOK_EVENTS[short_name] not in hook["events"]:
        return None
    event_id = "evt_" + secrets.token_urlsafe(18)
    event_type = BATCH_WEBHOOK_EVENTS[short_name]
    payload = {"success": True, "type": event_type, "id": job, "webhookId": event_id,
               "data": [] if short_name == "page" else data, "metadata": hook["metadata"]}
    db.execute(
        "INSERT INTO firecrawl_batch_webhook_events(id,job,event_type,payload,page_seq,state,attempts,next_attempt) "
        "VALUES(?,?,?,?,?, 'pending',0,?)",
        (event_id, job, event_type, json.dumps(payload, separators=(",", ":")), page_seq, time.time()),
    )
    return event_id


def _normalize_firecrawl_batch_webhook(value):
    if not isinstance(value, dict) or set(value) - {"url", "events", "metadata"}:
        raise ValueError("webhook supports only url, events, and metadata")
    url = value.get("url")
    checked = check_url(url)
    parts = urlsplit(checked)
    if parts.scheme != "https" or parts.port not in (None, 443):
        raise ValueError("webhook.url must use HTTPS on port 443")
    events = value.get("events", list(BATCH_WEBHOOK_EVENTS))
    if not isinstance(events, list) or not events:
        raise ValueError("webhook.events must be a non-empty list of started, page, or completed")
    normalized = []
    reverse = {full: short for short, full in BATCH_WEBHOOK_EVENTS.items()}
    for event in events:
        short = reverse.get(event, event) if isinstance(event, str) else None
        if short not in BATCH_WEBHOOK_EVENTS:
            raise ValueError("webhook.events supports only started, page, and completed")
        full = BATCH_WEBHOOK_EVENTS[short]
        if full not in normalized:
            normalized.append(full)
    metadata = value.get("metadata", {})
    if not isinstance(metadata, dict):
        raise ValueError("webhook.metadata must be a JSON object")
    try:
        if len(json.dumps(metadata, separators=(",", ":")).encode("utf-8")) > 16_384:
            raise ValueError("webhook.metadata must be no larger than 16 KiB")
    except (TypeError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith("webhook.metadata"):
            raise
        raise ValueError("webhook.metadata must be a JSON object") from exc
    return {"url": checked, "events": normalized, "metadata": metadata}


async def _post_firecrawl_batch_webhook(client, url, body, headers):
    async with client.post(url, data=body, headers=headers, allow_redirects=False) as response:
        await response.content.read(1024)
        return response.status


def install(app, perform, map_for, websearch_for, crawl_start_for, crawl_status_for, crawl_cancel_for,
            batch_for, hosted, webhook_sender=None):
    from .interact import InteractService
    interact = InteractService(hosted)
    app[INTERACT_SERVICE_KEY] = interact
    app.on_cleanup.append(interact.close_all)
    batch_tasks = {}
    hosted.db.executescript("""
        CREATE TABLE IF NOT EXISTS firecrawl_batch_jobs(
            id TEXT PRIMARY KEY, account TEXT NOT NULL, status TEXT NOT NULL, urls TEXT NOT NULL,
            options TEXT NOT NULL, total INTEGER NOT NULL, completed INTEGER NOT NULL DEFAULT 0,
            credits INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL, finished REAL,
            lease_until REAL, lease_token TEXT);
        CREATE TABLE IF NOT EXISTS firecrawl_batch_pages(
            job TEXT NOT NULL, seq INTEGER NOT NULL, url TEXT NOT NULL, status INTEGER NOT NULL,
            payload TEXT NOT NULL, credits INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(job,seq));
        CREATE TABLE IF NOT EXISTS firecrawl_batch_webhook_events(
            id TEXT PRIMARY KEY, job TEXT NOT NULL, event_type TEXT NOT NULL, payload TEXT NOT NULL,
            page_seq INTEGER,
            state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt REAL NOT NULL, http_status INTEGER, last_error TEXT,
            lease_until REAL, lease_token TEXT);
        CREATE INDEX IF NOT EXISTS firecrawl_batch_account ON firecrawl_batch_jobs(account,created);
        CREATE INDEX IF NOT EXISTS firecrawl_batch_webhook_due ON firecrawl_batch_webhook_events(state,next_attempt,lease_until);
    """)
    webhook_columns = {row[1] for row in hosted.db.execute("PRAGMA table_info(firecrawl_batch_webhook_events)")}
    if "page_seq" not in webhook_columns:
        hosted.db.execute("ALTER TABLE firecrawl_batch_webhook_events ADD COLUMN page_seq INTEGER")
    batch_columns = {row[1] for row in hosted.db.execute("PRAGMA table_info(firecrawl_batch_jobs)")}
    if "lease_until" not in batch_columns:
        hosted.db.execute("ALTER TABLE firecrawl_batch_jobs ADD COLUMN lease_until REAL")
    if "lease_token" not in batch_columns:
        hosted.db.execute("ALTER TABLE firecrawl_batch_jobs ADD COLUMN lease_token TEXT")
    hosted.db.execute("CREATE INDEX IF NOT EXISTS firecrawl_batch_claim ON firecrawl_batch_jobs(status,lease_until,created)")
    def error(message, code="unsupported_option", status=400):
        return web.json_response({"success": False, "error": message, "code": code}, status=status)

    def sdk_default_warnings(body):
        """Accept current SDK's benign defaults, but disclose options Apex doesn't implement."""
        warnings = []
        for key, default, label in (
                ("blockAds", True, "ad blocking"),
                ("removeBase64Images", True, "base64 image removal"),
                ("storeInCache", True, "Firecrawl cache controls"),
                ("fastMode", False, "fast mode"),
                ("mobile", False, "mobile rendering")):
            if key not in body:
                continue
            value = body[key]
            if type(value) is not bool:
                return None, f"{key} must be a boolean"
            if value != default:
                return None, f"{key}={str(value).lower()} is not supported by Apex"
            if default:
                warnings.append(f"{label} is not implemented by Apex; Apex's own rendering and cache rules apply")
        origin = body.get("origin")
        if origin is not None:
            if not isinstance(origin, str) or not 1 <= len(origin) <= 128:
                return None, "origin must be 1–128 characters"
            warnings.append("Firecrawl SDK origin attribution is not recorded by Apex")
        return ("; ".join(warnings) if warnings else None), None

    def unpack(response):
        try:
            return json.loads(response.body.decode("utf-8"))
        except (AttributeError, UnicodeDecodeError, json.JSONDecodeError):
            return {}

    def forwarded(response, data):
        if response.status < 200 or response.status >= 300:
            return error(data.get("error", "Request failed"), data.get("code", "request_failed"), response.status)
        return None

    def page_data(result, credits_used=None, change=None, screenshot=False):
        metadata = dict(result.get("metadata") or {})
        receipt = result.get("receipt") or {}
        metadata.update({
            "title": result.get("title") or metadata.get("title"),
            "sourceURL": receipt.get("final_url") or result.get("url"),
            "url": result.get("url") or receipt.get("final_url"),
            "statusCode": receipt.get("status") or result.get("status"),
            "contentType": result.get("kind"),
        })
        if isinstance(credits_used, int):
            metadata["apexCreditsUsed"] = credits_used
        data = {"metadata": {k: v for k, v in metadata.items() if v is not None}}
        if isinstance(result.get("markdown"), str):
            data["markdown"] = result["markdown"]
        if isinstance(result.get("html"), str):
            data["html"] = result["html"]
        if isinstance(result.get("raw_html"), str):
            data["rawHtml"] = result["raw_html"]
        extracted = result.get("extracted")
        if isinstance(extracted, dict):
            data["json"] = extracted.get("data")
            if not extracted.get("valid", True):
                data["jsonValidation"] = {"valid": False, "errors": extracted.get("errors", [])}
                data["warning"] = "Apex extraction did not satisfy the requested JSON Schema; inspect jsonValidation."
        if isinstance(result.get("links"), list):
            data["links"] = [link.get("url") if isinstance(link, dict) else link
                              for link in result["links"] if isinstance(link, (str, dict))]
        if isinstance(result.get("images"), list):
            data["images"] = [image for image in result["images"] if isinstance(image, str)]
        if isinstance(result.get("attributes"), list):
            data["attributes"] = result["attributes"]
        if screenshot and result.get("screenshot_png_base64"):
            data["screenshot"] = "data:image/png;base64," + result["screenshot_png_base64"]
        if result.get("warning"):
            data["warning"] = result["warning"]
        if result.get("archive"):
            data["warning"] = "Apex returned an Internet Archive copy; it is not the live page."
            data["metadata"]["archive"] = result["archive"]
        if change:
            data["changeTracking"] = {"changeStatus": change.get("status"),
                                      "diff": change.get("diff"),
                                      "previousScrapeAt": change.get("previous_check")}
        return data

    def translate_actions(actions):
        """Translate the safe subset of Firecrawl's declarative actions."""
        if not isinstance(actions, list) or len(actions) > 8:
            raise ValueError("actions must be a list of at most 8 items")
        translated = []
        for action in actions:
            if not isinstance(action, dict):
                raise ValueError("each action must be an object")
            kind = action.get("type")
            if kind in {"click", "wait"} and set(action) == {"type", "selector"}:
                translated.append(action)
            elif kind == "wait" and set(action) == {"type", "milliseconds"}:
                milliseconds = action["milliseconds"]
                if isinstance(milliseconds, bool) or not isinstance(milliseconds, int) or not 0 <= milliseconds <= 5000:
                    raise ValueError("wait milliseconds must be an integer from 0 to 5000")
                translated.append({"type": "wait", "ms": milliseconds})
            elif kind == "scroll" and set(action) <= {"type", "direction", "amount"}:
                direction, amount = action.get("direction", "down"), action.get("amount", 1)
                if direction not in {"up", "down"} or isinstance(amount, bool) or not isinstance(amount, int) or not 1 <= amount <= 10:
                    raise ValueError("scroll direction must be up/down and amount must be 1–10")
                translated.append({"type": "scroll", "direction": direction, "amount": amount})
            else:
                raise ValueError(f"Firecrawl action {kind!r} is not supported by Apex's bounded read-only browser actions")
        return translated

    def translate_wait_for(actions, wait_for):
        """Map Firecrawl's initial wait to Apex's bounded rendered-page action."""
        if isinstance(wait_for, bool) or not isinstance(wait_for, int) or not 0 <= wait_for <= 5000:
            raise ValueError("waitFor must be an integer from 0 to 5000 milliseconds")
        translated = [] if actions is None else translate_actions(actions)
        if wait_for:
            if len(translated) >= 8:
                raise ValueError("waitFor plus actions may contain at most 8 browser actions")
            translated.insert(0, {"type": "wait", "ms": wait_for})
        return translated

    def json_extract_spec(item):
        """Translate Firecrawl's JSON format to Apex's provider-backed schema extractor."""
        if not isinstance(item, dict) or item.get("type") != "json":
            return None
        extra = set(item) - {"type", "schema", "prompt"}
        if extra:
            raise ValueError("JSON format supports only type, schema and prompt in this adapter")
        schema, prompt = item.get("schema"), item.get("prompt")
        if schema is None:
            if not isinstance(prompt, str) or not prompt.strip():
                raise ValueError("JSON format needs a schema or a non-empty prompt")
            schema = {"type": "object"}
        if not isinstance(schema, dict):
            raise ValueError("JSON format schema must be an object")
        if prompt is not None and (not isinstance(prompt, str) or len(prompt) > 2000):
            raise ValueError("JSON format prompt must be text up to 2000 characters")
        spec = {"schema": schema, **({"prompt": prompt} if prompt is not None else {})}
        from .llm import validate_extract_spec
        from jsonschema.exceptions import SchemaError
        try:
            return validate_extract_spec(spec)
        except (TypeError, ValueError, RecursionError, SchemaError) as exc:
            raise ValueError("Invalid JSON extraction schema or prompt: " + str(exc)[:300]) from exc

    def extraction_admission(account):
        """Reject JSON jobs before enqueueing when hosted extraction cannot run."""
        from .llm import providers
        if not providers():
            return error("Model extraction is not configured on this hosted service.", "not_available_hosted", 400)
        if hosted.billing.limits(account)["price"] == 0:
            return error("JSON extraction requires a paid plan.", "paid_plan_required", 402)
        if hosted.billing.remaining(account) < 153:
            return error("JSON extraction needs up to 153 credits for one auto-rendered page.",
                         "insufficient_credits", 402)
        day = time.strftime("%Y-%m-%d", time.gmtime())
        used = hosted.db.execute("SELECT COALESCE(SUM(count),0) FROM funnel WHERE day=? AND event='extract'", (day,)).fetchone()[0]
        if used >= int(os.environ.get("APEX_EXTRACT_DAILY_CAP", "3000")):
            return error("AI extraction has reached today's service limit. Retry tomorrow or use a CSS recipe.",
                         "extract_daily_limit", 429)
        return None

    async def scrape(request):
        if request.content_type != "application/json":
            return error("Content-Type must be application/json", "invalid_request", 415)
        body = await request.json()
        if not isinstance(body, dict):
            return error("Request body must be a JSON object")
        allowed = {"url", "formats", "onlyMainContent", "maxAge", "minAge", "lockdown", "screenshot", "actions", "waitFor",
                   "origin", "blockAds", "removeBase64Images", "storeInCache", "fastMode", "mobile"}
        extra = set(body) - allowed
        if extra:
            return error("Unsupported Apex compatibility options: " + ", ".join(sorted(extra)))
        compat_warning, compat_error = sdk_default_warnings(body)
        if compat_error:
            return error(compat_error)
        url = body.get("url")
        if not isinstance(url, str) or not url:
            return error("url is required")
        if body.get("onlyMainContent", True) is not True:
            return error("onlyMainContent:false is not supported; Apex returns the main content")
        formats = body.get("formats", ["markdown"])
        if not isinstance(formats, list) or not formats:
            return error("formats must be a non-empty list")
        parsed, extract_spec, attribute_selectors = [], None, None
        for item in formats:
            kind = item if isinstance(item, str) else item.get("type") if isinstance(item, dict) else None
            if kind == "json":
                if extract_spec is not None or isinstance(item, str):
                    return error("JSON output needs one object format with a schema or prompt")
                try:
                    extract_spec = json_extract_spec(item)
                except ValueError as exc:
                    return error(str(exc))
                parsed.append("json")
                continue
            if kind == "attributes":
                if attribute_selectors is not None or not isinstance(item, dict) or set(item) != {"type", "selectors"}:
                    return error("Attributes output needs one object format with selectors")
                try:
                    attribute_selectors = validate_attribute_selectors(item["selectors"])
                except ValueError as exc:
                    return error(str(exc))
                parsed.append("attributes")
                continue
            if isinstance(item, dict) and set(item) - {"type"}:
                return error("Format-specific options are not supported by this compatibility subset")
            if kind not in {"markdown", "html", "rawHtml", "links", "images", "screenshot"}:
                return error(f"Format {kind!r} is not supported; supported formats are markdown, html, rawHtml, links, images, screenshot")
            parsed.append("raw_html" if kind == "rawHtml" else kind)
        if "maxAge" in body and (isinstance(body["maxAge"], bool) or not isinstance(body["maxAge"], int)
                                 or not 0 <= body["maxAge"] <= 31536000000):
            return error("maxAge must be milliseconds from 0 to 31536000000")
        if "minAge" in body and (isinstance(body["minAge"], bool) or not isinstance(body["minAge"], int)
                                 or not 0 <= body["minAge"] <= 31536000000):
            return error("minAge must be milliseconds from 0 to 31536000000")
        if "lockdown" in body and not isinstance(body["lockdown"], bool):
            return error("lockdown must be a boolean")
        cache_only = "minAge" in body or body.get("lockdown", False)
        if cache_only and (extract_spec or "screenshot" in parsed or body.get("screenshot", False)
                           or body.get("actions") or body.get("waitFor", 0)):
            return error("minAge/lockdown cache-only requests do not support JSON extraction, screenshots, or browser actions")
        if "screenshot" in body and not isinstance(body["screenshot"], bool):
            return error("screenshot must be a boolean")
        apex_body = {"url": url, "render": "never" if cache_only else "auto"}
        if cache_only:
            apex_body["cache_only"] = True
            if "minAge" in body:
                apex_body["min_age_ms"] = body["minAge"]
            if "maxAge" in body:
                apex_body["cache_max_age_ms"] = body["maxAge"]
            elif body.get("lockdown"):
                # Firecrawl's lockdown mode serves cache only, with a two-year
                # upper age bound when maxAge is not explicitly supplied.
                apex_body["cache_max_age_ms"] = 63_072_000_000
        apex_outputs = {"raw_html" if field == "raw_html" else field for field in parsed
                        if field in {"markdown", "html", "raw_html", "links", "images", "attributes", "screenshot"}}
        if apex_outputs:
            apex_body["outputs"] = sorted(apex_outputs)
        if attribute_selectors is not None:
            apex_body["attribute_selectors"] = attribute_selectors
        if extract_spec:
            apex_body["extract"] = extract_spec
        if "maxAge" in body:
            apex_body["max_age"] = body["maxAge"]
        if body.get("actions") is not None or "waitFor" in body:
            try:
                actions = translate_wait_for(body.get("actions"), body.get("waitFor", 0))
                if actions:
                    apex_body["actions"] = actions
            except ValueError as exc:
                return error(str(exc))
        want_screenshot = "screenshot" in parsed or body.get("screenshot", False)
        if want_screenshot:
            apex_body["screenshot"] = True
        try:
            response = await perform(request["account"], apex_body)
        except ScrapeError as exc:
            if cache_only and exc.code == "cache_miss":
                lockdown_miss = body.get("lockdown", False)
                return error(
                    "No cached response is available for this URL; no network request was made.",
                    "SCRAPE_LOCKDOWN_CACHE_MISS" if lockdown_miss else "SCRAPE_NO_CACHED_DATA", 404)
            raise
        payload = unpack(response)
        if response.status == 400 and payload.get("code") == "cache_miss" and cache_only:
            lockdown_miss = body.get("lockdown", False)
            return error(
                "No cached response is available for this URL; no network request was made.",
                "SCRAPE_LOCKDOWN_CACHE_MISS" if lockdown_miss else "SCRAPE_NO_CACHED_DATA", 404)
        failure = forwarded(response, payload)
        if failure:
            return failure
        result = payload.get("result") or {}
        data = page_data(result, payload.get("credits_used"), payload.get("change"), want_screenshot)
        if compat_warning:
            data["warning"] = "; ".join(filter(None, [data.get("warning"), compat_warning]))
        selected = {}
        if "markdown" in parsed and "markdown" in data:
            selected["markdown"] = data["markdown"]
        if "html" in parsed and "html" in data:
            selected["html"] = data["html"]
        if "raw_html" in parsed and "rawHtml" in data:
            selected["rawHtml"] = data["rawHtml"]
        if "json" in parsed and "json" in data:
            selected["json"] = data["json"]
        if "links" in parsed and "links" in data:
            selected["links"] = data["links"]
        if "images" in parsed and "images" in data:
            selected["images"] = data["images"]
        if "attributes" in parsed and "attributes" in data:
            selected["attributes"] = data["attributes"]
        selected["metadata"] = data["metadata"]
        if want_screenshot and "screenshot" in data:
            selected["screenshot"] = data["screenshot"]
        if data.get("warning"):
            selected["warning"] = data["warning"]
        if data.get("changeTracking"):
            selected["changeTracking"] = data["changeTracking"]
        return web.json_response({"success": True, "data": selected,
                                 "creditsUsed": payload.get("credits_used", 0)})

    parse_formats = [
        {"format": "pdf", "kind": "document", "extensions": [".pdf"],
         "mimeTypes": ["application/pdf"], "available": True},
        {"format": "docx", "kind": "document", "extensions": [".docx"],
         "mimeTypes": ["application/vnd.openxmlformats-officedocument.wordprocessingml.document"], "available": True},
        {"format": "html", "kind": "document", "extensions": [".html", ".htm"],
         "mimeTypes": ["text/html"], "available": True},
        {"format": "text", "kind": "document", "extensions": [".txt"],
         "mimeTypes": ["text/plain"], "available": True},
        {"format": "json", "kind": "document", "extensions": [".json"],
         "mimeTypes": ["application/json"], "available": True},
        {"format": "xml", "kind": "document", "extensions": [".xml"],
         "mimeTypes": ["application/xml", "text/xml"], "available": True},
    ]
    parse_format_names = {"markdown", "text", "links", "html", "rawHtml", "pages", "metadata"}
    max_parse_bytes = 12 * 1024 * 1024

    async def read_part_limited(part, limit):
        chunks, total = [], 0
        while True:
            chunk = await part.read_chunk(size=64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                return None
            chunks.append(chunk)
        return b"".join(chunks)

    async def parse_document(request):
        if request.content_type != "multipart/form-data":
            return error("Content-Type must be multipart/form-data with a file field", "invalid_request", 415)
        if request.content_length is not None and request.content_length > max_parse_bytes + 131072:
            return error("Upload exceeds the 12 MiB file limit", "upload_too_large", 413)
        reader = await request.multipart()
        file_body, filename, content_type, option_text = None, None, None, None
        while True:
            part = await reader.next()
            if part is None:
                break
            if part.name == "file":
                if file_body is not None:
                    return error("Upload exactly one file", "invalid_request")
                filename = ((part.filename or "upload").replace("\\", "/").rsplit("/", 1)[-1][:255]
                            or "upload")
                content_type = (part.headers.get("Content-Type") or "application/octet-stream").split(";", 1)[0].lower()
                file_body = await read_part_limited(part, max_parse_bytes)
                if file_body is None:
                    return error("Upload exceeds the 12 MiB file limit", "upload_too_large", 413)
            elif part.name == "options":
                if option_text is not None:
                    return error("Provide options once", "invalid_request")
                raw_options = await read_part_limited(part, 65536)
                if raw_options is None:
                    return error("options exceeds the 64 KiB limit", "invalid_request", 413)
                try:
                    option_text = raw_options.decode("utf-8")
                except UnicodeDecodeError:
                    return error("options must be UTF-8 JSON", "invalid_request")
            else:
                return error("Only file and options multipart fields are supported", "unsupported_option")
        if file_body is None or not file_body:
            return error("A non-empty file field is required", "invalid_request")
        try:
            options = json.loads(option_text) if option_text is not None else {}
        except json.JSONDecodeError:
            return error("options must contain valid JSON", "invalid_request")
        if not isinstance(options, dict):
            return error("options must be a JSON object", "invalid_request")
        extra = set(options) - {"formats", "onlyMainContent"}
        if extra:
            return error("Unsupported parse options: " + ", ".join(sorted(extra)), "unsupported_option")
        if options.get("onlyMainContent", True) is not True:
            return error("onlyMainContent:false is not supported by the isolated document parser", "unsupported_option")
        formats = options.get("formats", ["markdown"])
        if not isinstance(formats, list) or not formats:
            return error("formats must be a non-empty list", "invalid_request")
        requested = []
        for item in formats:
            kind = item if isinstance(item, str) else item.get("type") if isinstance(item, dict) else None
            if isinstance(item, dict) and set(item) != {"type"}:
                return error("Parse formats accept only a type field", "unsupported_option")
            if kind not in parse_format_names:
                return error("Supported parse outputs are " + ", ".join(sorted(parse_format_names)),
                             "unsupported_option")
            if kind not in requested:
                requested.append(kind)

        if file_body.startswith(b"%PDF-"):
            content_type = "application/pdf"
        elif file_body.startswith(b"PK\x03\x04") and (filename.lower().endswith(".docx") or "wordprocessingml" in content_type):
            content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        account = request["account"]
        try:
            hosted.billing.charge(account, "page")
        except Exception as exc:
            if hasattr(exc, "needed"):
                raise ScrapeError("insufficient_credits", str(exc)) from exc
            raise
        try:
            from .parser import parse as parse_bytes
            parsed = await parse_bytes(file_body, "https://upload.invalid/" + filename,
                                       content_type, include_html="html" in requested,
                                       include_raw_html="rawHtml" in requested)
        except BaseException:
            hosted.billing.refund(account, 1)
            raise

        metadata = {"filename": filename, "contentType": content_type,
                    "bytes": len(file_body), "sourceSha256": parsed.get("source_sha256")}
        if parsed.get("title"):
            metadata["title"] = parsed["title"]
        if parsed.get("pages") is not None:
            metadata["numPages"] = len(parsed["pages"])
        data = {"metadata": metadata}
        if "markdown" in requested and parsed.get("markdown") is not None:
            data["markdown"] = parsed["markdown"]
        if "text" in requested and parsed.get("text") is not None:
            data["text"] = parsed["text"]
        if "links" in requested:
            data["links"] = [item.get("url") if isinstance(item, dict) else item
                              for item in parsed.get("links", [])]
        if "html" in requested and parsed.get("html") is not None:
            data["html"] = parsed["html"]
        if "rawHtml" in requested and parsed.get("raw_html") is not None:
            data["rawHtml"] = parsed["raw_html"]
        if "pages" in requested and parsed.get("pages") is not None:
            data["pages"] = [{"pageNumber": page["page"], "markdown": page.get("text", "")}
                              for page in parsed["pages"]]
        return web.json_response({"success": True, "data": data, "creditsUsed": 1})

    async def parse_format_list(request):
        return web.json_response({"success": True, "data": {"formats": parse_formats}})

    async def credit_usage(request):
        account = request["account"]
        plan = hosted.billing.limits(account)
        row = hosted.db.execute("SELECT period_start FROM accounts WHERE id=?", (account,)).fetchone()
        order = hosted.db.execute("SELECT period_end FROM orders WHERE account=?", (account,)).fetchone()
        to_iso = lambda stamp: datetime.fromtimestamp(stamp, timezone.utc).isoformat().replace("+00:00", "Z") if stamp else None
        return web.json_response({"success": True, "data": {
            "remainingCredits": hosted.billing.remaining(account),
            "planCredits": plan["credits"],
            "billingPeriodStart": to_iso(row[0] if row else None),
            "billingPeriodEnd": to_iso(order[0] if order else None)}})

    async def historical_credit_usage(request):
        by_api_key = request.query.get("byApiKey", "false")
        if by_api_key not in {"true", "false"}:
            return error("byApiKey must be true or false", "invalid_request")
        if by_api_key == "true":
            rows = hosted.db.execute(
                "SELECT strftime('%Y-%m',u.ts,'unixepoch'),u.api_key_hash,k.label,SUM(u.credits) "
                "FROM usage u LEFT JOIN keys k ON k.hash=u.api_key_hash WHERE u.account=? "
                "GROUP BY 1,2,3 ORDER BY 1,2", (request["account"],)).fetchall()
        else:
            rows = hosted.db.execute(
                "SELECT strftime('%Y-%m',ts,'unixepoch'),NULL,NULL,SUM(credits) FROM usage "
                "WHERE account=? GROUP BY 1 ORDER BY 1", (request["account"],)).fetchall()
        periods = []
        for month, key_hash, key_label, credits in rows:
            if not month:
                continue
            start = datetime.strptime(month, "%Y-%m").replace(tzinfo=timezone.utc)
            end = (start.replace(year=start.year + 1, month=1) if start.month == 12
                   else start.replace(month=start.month + 1)) - timedelta(seconds=1)
            period = {"startDate": start.isoformat().replace("+00:00", "Z"),
                      "endDate": end.isoformat().replace("+00:00", "Z"),
                      "totalCredits": credits or 0}
            if by_api_key == "true":
                period["apiKeyId"] = public_usage_key_id(key_hash)
                period["apiKeyLabel"] = key_label
            periods.append(period)
        return web.json_response({"success": True, "periods": periods})

    async def map_route(request):
        body = await request.json()
        if not isinstance(body, dict):
            return error("Request body must be a JSON object")
        allowed = {"url", "search", "limit", "sitemap"}
        extra = set(body) - allowed
        if extra:
            return error("Unsupported Apex compatibility options: " + ", ".join(sorted(extra)))
        if body.get("sitemap", "include") != "include":
            return error("Only sitemap='include' is supported")
        request_body = {key: body[key] for key in ("url", "search", "limit") if key in body}
        response = await map_for(request["account"], request_body)
        payload = unpack(response)
        failure = forwarded(response, payload)
        if failure:
            return failure
        found = payload.get("result") or {}
        links = []
        for item in found.get("urls", []):
            if isinstance(item, str):
                links.append({"url": item})
            elif isinstance(item, dict) and isinstance(item.get("url"), str):
                links.append({key: item[key] for key in ("url", "title", "description") if item.get(key) is not None})
        reply = {"success": True, "links": links}
        if found.get("truncated"):
            reply["warning"] = "Apex's map limit truncated this result. Increase plan capacity or refine the search."
        return web.json_response(reply)

    async def search(request):
        body = await request.json()
        if not isinstance(body, dict):
            return error("Request body must be a JSON object")
        allowed = {"query", "limit", "sources", "includeDomains", "excludeDomains", "origin", "scrapeOptions", "categories"}
        extra = set(body) - allowed
        if extra:
            return error("Unsupported Apex compatibility options: " + ", ".join(sorted(extra)))
        sources = body.get("sources", ["web"])
        source_types = []
        if isinstance(sources, list):
            for source in sources:
                if isinstance(source, str):
                    source_types.append(source)
                elif isinstance(source, dict) and set(source) == {"type"}:
                    source_types.append(source["type"])
                else:
                    return error("sources must contain 'web', 'news', or 'images' (string or {type: ...})")
        if not source_types or any(source not in ("web", "news", "images") for source in source_types):
            return error("sources must contain one or more of web, news, or images")
        if len(set(source_types)) != len(source_types):
            return error("sources must not contain duplicate source types")
        configured_provider = provider_name()
        if configured_provider is not None and "images" in source_types and configured_provider != "brave":
            return error("Image search requires the configured Brave provider's dedicated image index")
        if configured_provider == "brave" and "news" in source_types:
            return error("The configured Brave provider does not expose a dedicated news source; use Tavily or Exa")
        categories = body.get("categories", [])
        if not isinstance(categories, list) or len(categories) > 1:
            return error("This adapter supports at most one search category: pdf")
        if categories and isinstance(categories[0], dict):
            if set(categories[0]) != {"type"}:
                return error("Category objects must contain only type")
            categories = [categories[0]["type"]]
        if any(category != "pdf" for category in categories):
            return error("This adapter currently supports only the pdf search category")
        pdf_category = categories == ["pdf"]
        if pdf_category and source_types != ["web"]:
            return error("The pdf category can only be used with web as the sole source")
        scrape_options = body.get("scrapeOptions")
        scrape_actions = []
        if scrape_options is not None:
            if "images" in source_types:
                return error("scrapeOptions is supported for web and news search results, not when images are requested")
            if not isinstance(scrape_options, dict):
                return error("scrapeOptions must be an object")
            extra_scrape = set(scrape_options) - {"formats", "onlyMainContent", "waitFor", "actions"}
            if extra_scrape:
                return error("Unsupported search scrapeOptions: " + ", ".join(sorted(extra_scrape)))
            if scrape_options.get("onlyMainContent", True) is not True:
                return error("onlyMainContent:false is not supported; Apex returns the main content")
            formats = scrape_options.get("formats", ["markdown"])
            if not isinstance(formats, list) or not formats:
                return error("scrapeOptions.formats must be a non-empty list")
            normalized_formats = []
            for item in formats:
                kind = item if isinstance(item, str) else item.get("type") if isinstance(item, dict) else None
                if isinstance(item, dict) and set(item) != {"type"}:
                    return error("Search scrape format options are not supported by this compatibility subset")
                if kind not in {"markdown", "html", "rawHtml", "links", "images", "screenshot"}:
                    return error("Search scrape formats support markdown, html, rawHtml, links, images, screenshot")
                normalized_formats.append("raw_html" if kind == "rawHtml" else kind)
            try:
                scrape_actions = translate_wait_for(scrape_options.get("actions"), scrape_options.get("waitFor", 0))
            except ValueError as exc:
                return error(str(exc))
        origin = body.get("origin")
        if origin is not None and (not isinstance(origin, str) or len(origin) > 200):
            return error("origin must be a string no longer than 200 characters")
        try:
            include_domains, exclude_domains = normalize_domain_filters(
                body.get("includeDomains"), body.get("excludeDomains"))
        except ValueError as exc:
            return error(str(exc), code="invalid_search_filter")
        data = {}
        total_credits = 0
        source_errors = {}
        first_source_failure = None
        for source_type in source_types:
            topic = source_type if source_type in ("news", "images") else "general"
            request_body = {key: body[key] for key in ("query", "limit") if key in body}
            if pdf_category:
                query = request_body.get("query")
                if not isinstance(query, str) or len(query) > 288:
                    return error("query must be at most 288 characters when using categories=['pdf']")
                request_body["query"] = query + " filetype:pdf"
            if topic != "general":
                request_body["topic"] = topic
            if include_domains is not None:
                request_body["include_domains"] = include_domains
            if exclude_domains is not None:
                request_body["exclude_domains"] = exclude_domains
            try:
                response = await websearch_for(request["account"], request_body)
            except Exception:
                source_errors[source_type] = {
                    "statusCode": 503,
                    "error": "Search provider request failed",
                    "code": "search_failed",
                }
                continue
            payload = unpack(response)
            failure = forwarded(response, payload)
            if failure:
                source_errors[source_type] = {
                    "statusCode": response.status,
                    "error": payload.get("error", "Search source failed"),
                    "code": payload.get("code", "request_failed"),
                }
                first_source_failure = first_source_failure or failure
                continue
            provider_credits = payload.get("credits_used", 0)
            if isinstance(provider_credits, int) and not isinstance(provider_credits, bool):
                total_credits += provider_credits
            provider_results = payload.get("results", [])
            if source_type == "news":
                data["news"] = [{"url": item.get("url"), "title": item.get("title"),
                                 "snippet": item.get("snippet"), "date": item.get("date"), "position": index}
                                for index, item in enumerate(provider_results, start=1)]
            elif source_type == "images":
                data["images"] = [{"title": item.get("title"), "imageUrl": item.get("imageUrl"),
                                   "imageWidth": item.get("imageWidth"), "imageHeight": item.get("imageHeight"),
                                   "url": item.get("url"), "position": index}
                                  for index, item in enumerate(provider_results, start=1)]
            else:
                if pdf_category:
                    provider_results = [item for item in provider_results if isinstance(item, dict)
                                        and isinstance(item.get("url"), str)
                                        and urlsplit(item["url"]).path.lower().endswith(".pdf")]
                    if isinstance(body.get("limit", 10), int) and not isinstance(body.get("limit", 10), bool):
                        provider_results = provider_results[:body.get("limit", 10)]
                data["web"] = [{"url": item.get("url"), "title": item.get("title"),
                                "description": item.get("snippet"), "position": index,
                                **({"category": "pdf"} if pdf_category else {})}
                               for index, item in enumerate(provider_results, start=1)]
        if not data and source_errors:
            return first_source_failure or error("Search provider request failed", "search_failed", 503)
        if scrape_options is not None:
            semaphore = asyncio.Semaphore(3)

            async def scrape_result(item):
                nonlocal total_credits
                url = item.get("url")
                if not isinstance(url, str) or not url:
                    item["error"] = "Search result has no scrapeable page URL"
                    item["statusCode"] = 400
                    return item
                selected_outputs = [value for value in normalized_formats if value != "screenshot"]
                apex_body = {"url": url, "render": "auto"}
                if selected_outputs:
                    apex_body["outputs"] = selected_outputs
                if "screenshot" in normalized_formats:
                    apex_body["screenshot"] = True
                    if "outputs" in apex_body:
                        apex_body["outputs"].append("screenshot")
                if scrape_actions:
                    apex_body["actions"] = scrape_actions
                async with semaphore:
                    try:
                        response = await perform(request["account"], apex_body)
                    except (ScrapeError, ValueError) as exc:
                        item["error"] = str(exc)[:500]
                        item["statusCode"] = getattr(exc, "status", 400)
                        return item
                page_payload = unpack(response)
                credits = page_payload.get("credits_used", 0)
                if isinstance(credits, int):
                    total_credits += credits
                if response.status < 200 or response.status >= 300:
                    item["error"] = page_payload.get("error", "Page scrape failed")
                    item["statusCode"] = response.status
                    return item
                page = page_payload.get("result") or {}
                data = page_data(page, credits, screenshot="screenshot" in normalized_formats)
                item["metadata"] = {**(item.get("metadata") or {}), **data.pop("metadata", {})}
                item.update(data)
                return item

            for result_key in ("web", "news"):
                if result_key in data:
                    data[result_key] = await asyncio.gather(*(scrape_result(item) for item in data[result_key]))
        reply = {"success": True, "data": data, "creditsUsed": total_credits}
        if source_errors:
            reply["sourceErrors"] = source_errors
        if origin:
            reply["warning"] = "Search SDK origin telemetry is accepted but not recorded by Apex."
        return web.json_response(reply)

    async def crawl(request):
        body = await request.json()
        if not isinstance(body, dict):
            return error("Request body must be a JSON object")
        allowed = {"url", "limit", "maxDiscoveryDepth", "includePaths", "excludePaths",
                   "regexOnFullURL", "ignoreQueryParameters", "ignoreRobotsTxt", "allowExternalLinks",
                   "allowSubdomains", "sitemap", "scrapeOptions"}
        extra = set(body) - allowed
        if extra:
            return error("Unsupported Apex compatibility options: " + ", ".join(sorted(extra)))
        if body.get("ignoreRobotsTxt", False) is not False:
            return error("Apex always obeys robots.txt")
        if body.get("allowExternalLinks", False) is not False or body.get("allowSubdomains", False) is not False:
            return error("This adapter is same-site only; external links and subdomains are not enabled")
        if body.get("sitemap", "include") != "include":
            return error("Only sitemap='include' is supported")
        scrape_options = body.get("scrapeOptions", {})
        if not isinstance(scrape_options, dict):
            return error("scrapeOptions must be an object")
        supported_scrape = {"formats", "onlyMainContent", "waitFor", "actions"}
        extra_scrape = set(scrape_options) - supported_scrape
        if extra_scrape:
            return error("Unsupported scrapeOptions: " + ", ".join(sorted(extra_scrape)))
        if scrape_options.get("onlyMainContent", True) is not True:
            return error("onlyMainContent:false is not supported")
        try:
            crawl_actions = translate_wait_for(scrape_options.get("actions"), scrape_options.get("waitFor", 0))
        except ValueError as exc:
            return error(str(exc))
        formats = scrape_options.get("formats", [{"type": "markdown"}])
        if not isinstance(formats, list) or not formats:
            return error("Crawl formats must be a non-empty list")
        crawl_formats, extract_spec, attribute_selectors = [], None, None
        for item in formats:
            kind = item if isinstance(item, str) else item.get("type") if isinstance(item, dict) else None
            if kind == "json":
                if extract_spec is not None or isinstance(item, str):
                    return error("JSON output needs one object format with a schema or prompt")
                try:
                    extract_spec = json_extract_spec(item)
                except ValueError as exc:
                    return error(str(exc))
                crawl_formats.append("json")
                continue
            if kind == "attributes":
                if attribute_selectors is not None or not isinstance(item, dict) or set(item) != {"type", "selectors"}:
                    return error("Attributes output needs one object format with selectors")
                try:
                    attribute_selectors = validate_attribute_selectors(item["selectors"])
                except ValueError as exc:
                    return error(str(exc))
                crawl_formats.append("attributes")
                continue
            if isinstance(item, dict) and set(item) - {"type"}:
                return error("Format-specific options are not supported by this compatibility subset")
            if kind not in {"markdown", "html", "rawHtml", "links", "images", "screenshot"}:
                return error(f"Crawl format {kind!r} is not supported; supported formats are markdown, html, rawHtml, links, images, screenshot")
            crawl_formats.append(kind)
        outputs = sorted({"raw_html" if item == "rawHtml" else item for item in crawl_formats
                          if item in {"markdown", "html", "rawHtml", "links", "images", "attributes", "screenshot"}})
        start_body = {"url": body.get("url"), "limit": body.get("limit", 25), "render": "auto",
                      "outputs": outputs, "max_depth": body.get("maxDiscoveryDepth", 3)}
        if attribute_selectors is not None:
            start_body["attribute_selectors"] = attribute_selectors
        if crawl_actions:
            start_body["actions"] = crawl_actions
        for source, target in (("includePaths", "include_paths"), ("excludePaths", "exclude_paths"),
                               ("regexOnFullURL", "regex_on_full_url"),
                               ("ignoreQueryParameters", "ignore_query_parameters")):
            if source in body:
                start_body[target] = body[source]
        if extract_spec:
            admission = extraction_admission(request["account"])
            if admission:
                return admission
            start_body["extract"] = extract_spec
        response = await crawl_start_for(request["account"], start_body)
        payload = unpack(response)
        failure = forwarded(response, payload)
        if failure:
            return failure
        return web.json_response({"success": True, "id": payload["id"], "url": payload["url"]})

    async def crawl_status(request):
        offset_raw = request.query.get("offset", "0")
        if not offset_raw.isdigit():
            return error("offset must be a non-negative integer")
        response = await crawl_status_for(request["account"], {"id": request.match_info["job"],
                                                                 "offset": int(offset_raw)})
        payload = unpack(response)
        failure = forwarded(response, payload)
        if failure:
            return failure
        state = payload.get("status")
        status = {"running": "scraping", "completed": "completed", "cancelled": "cancelled",
                  "failed": "failed", "stopped": "failed", "interrupted": "failed"}.get(state, "failed")
        created = payload.get("created")
        finished = payload.get("finished")
        now = time.time()
        iso = lambda stamp: datetime.fromtimestamp(stamp, timezone.utc).isoformat().replace("+00:00", "Z") if stamp else None
        data = []
        for page in payload.get("results", []):
            result = page.get("result") or {}
            result.setdefault("url", page.get("url"))
            data.append(page_data(result, page.get("credits_used"),
                                  screenshot=bool(result.get("screenshot_png_base64"))))
        total = (payload.get("pages_done", 0) + payload.get("pages_failed", 0))
        reply = {"status": status, "total": total, "completed": payload.get("pages_done", 0),
                 "creditsUsed": payload.get("credits_used", 0), "createdAt": iso(created),
                 "completedAt": iso(finished), "duration": max(0, (finished or now) - created) if created else 0,
                 "data": data}
        next_offset = payload.get("next_offset")
        reply["next"] = f"/v2/crawl/{request.match_info['job']}?offset={next_offset}" if next_offset is not None else None
        if payload.get("note"):
            reply["warning"] = payload["note"]
        return web.json_response(reply)

    async def crawl_active(request):
        rows = hosted.db.execute(
            "SELECT id,url,page_limit,created,done,failed,credits,options FROM crawl_jobs "
            "WHERE account=? AND status='running' ORDER BY created DESC LIMIT 100",
            (request["account"],)).fetchall()
        crawls = []
        for ident, url, limit, created, done, failed, credits, raw_options in rows:
            options = json.loads(raw_options or "{}")
            formats = options.get("outputs") or ["markdown"]
            formats = [{"type": "rawHtml" if item == "raw_html" else item} for item in formats]
            crawls.append({"id": ident, "url": url, "status": "scraping",
                           "createdAt": datetime.fromtimestamp(created, timezone.utc).isoformat().replace("+00:00", "Z"),
                           "completed": done, "failed": failed, "total": limit, "creditsUsed": credits,
                           "options": {"limit": limit,
                                       "scrapeOptions": {"formats": formats, "onlyMainContent": True}}})
        return web.json_response({"success": True, "crawls": crawls})

    async def crawl_cancel(request):
        response = await crawl_cancel_for(request["account"], {"id": request.match_info["job"]})
        payload = unpack(response)
        failure = forwarded(response, payload)
        if failure:
            return failure
        return web.json_response({"success": True, "id": payload.get("id"), "status": payload.get("status")})

    async def crawl_errors(request):
        job = request.match_info["job"]
        row = hosted.db.execute("SELECT created FROM crawl_jobs WHERE id=? AND account=?",
                                (job, request["account"])).fetchone()
        if not row:
            return error("Crawl job not found.", "not_found", 404)
        errors, robots_blocked = [], []
        for seq, url, status, raw, recorded_at in hosted.db.execute(
                "SELECT seq,url,status,payload,recorded_at FROM crawl_pages WHERE job=? AND status!=200 ORDER BY seq", (job,)):
            payload = json.loads(raw)
            code = payload.get("code") or "error"
            if code == "robots_denied":
                robots_blocked.append(url)
            else:
                errors.append({"id": f"{job}:{seq}",
                               "timestamp": datetime.fromtimestamp(recorded_at or row[0], timezone.utc).isoformat().replace("+00:00", "Z"),
                               "url": url, "error": payload.get("error", "Page scrape failed."), "code": code})
        return web.json_response({"errors": errors, "robotsBlocked": robots_blocked})

    async def interact_create(request):
        return web.json_response(await interact.create(request["account"], await request.json()))

    async def interact_list(request):
        return web.json_response(interact.list(request["account"], request.query.get("status")))

    async def interact_execute(request):
        return web.json_response(await interact.execute(request["account"], request.match_info["session"],
                                                        await request.json()))

    async def interact_delete(request):
        return web.json_response(await interact.delete(request["account"], request.match_info["session"]))

    def batch_status(job, account):
        row = hosted.db.execute(
            "SELECT id,status,total,completed,credits,created,finished FROM firecrawl_batch_jobs WHERE id=? AND account=?",
            (job, account)).fetchone()
        if not row:
            raise web.HTTPNotFound(text=json.dumps({"error": "Batch scrape job not found."}), content_type="application/json")
        return row

    async def run_batch(job):
        claimed = _claim_firecrawl_batch(hosted.db, job, time.time())
        if not claimed:
            batch_tasks.pop(job, None)
            return
        lease_token = claimed
        row = hosted.db.execute(
        "SELECT account,urls,options FROM firecrawl_batch_jobs WHERE id=? AND status='scraping' AND lease_token=?",
            (job, lease_token),
        ).fetchone()
        if not row:
            hosted.db.execute("UPDATE firecrawl_batch_jobs SET lease_until=NULL,lease_token=NULL WHERE id=? AND lease_token=?",
                              (job, lease_token))
            batch_tasks.pop(job, None)
            return
        account, raw_urls, raw_options = row
        urls, options = json.loads(raw_urls), json.loads(raw_options)

        async def renew_lease():
            while True:
                await asyncio.sleep(BATCH_LEASE_RENEW_SECONDS)
                renewed = hosted.db.execute(
                    "UPDATE firecrawl_batch_jobs SET lease_until=? WHERE id=? AND status='scraping' AND lease_token=?",
                    (time.time() + BATCH_LEASE_SECONDS, job, lease_token),
                ).rowcount
                if not renewed:
                    return

        heartbeat = asyncio.create_task(renew_lease(), name=f"firecrawl-batch-lease-{job}")

        def save_page(index, url, item):
            status = item.get("status", 502)
            credits = item.get("credits_used", 0) if status == 200 else 0
            result = item.get("result") if status == 200 else None
            if isinstance(result, dict):
                result = page_data(result, credits,
                                   screenshot=options.get("screenshot", False)
                                   or "screenshot" in options.get("_compat_formats", []))
                if options.get("_compat_warning"):
                    result["warning"] = "; ".join(filter(None, [result.get("warning"), options["_compat_warning"]]))
                for field in ("markdown", "html", "rawHtml", "links", "images", "attributes", "screenshot", "json"):
                    if field not in options.get("_compat_formats", []) and not (
                            field == "screenshot" and options.get("screenshot", False)):
                        result.pop(field, None)
            else:
                result = {"metadata": {"url": url, "sourceURL": url, "statusCode": status,
                                       "error": item.get("error", "Scrape failed")}}
            hosted.db.execute("BEGIN IMMEDIATE")
            try:
                saved = hosted.db.execute(
                    "INSERT OR IGNORE INTO firecrawl_batch_pages(job,seq,url,status,payload,credits) "
                    "SELECT ?,?,?,?,?,? WHERE EXISTS (SELECT 1 FROM firecrawl_batch_jobs "
                    "WHERE id=? AND status='scraping' AND lease_token=?)",
                    (job, index, url, status, json.dumps(result), credits, job, lease_token),
                ).rowcount
                if saved:
                    _queue_firecrawl_batch_webhook(hosted.db, job, options.get("_webhook"), "page", [result], page_seq=index)
                    hosted.db.execute(
                        "UPDATE firecrawl_batch_jobs SET "
                        "completed=(SELECT COUNT(*) FROM firecrawl_batch_pages WHERE job=?), "
                        "credits=(SELECT COALESCE(SUM(credits),0) FROM firecrawl_batch_pages WHERE job=?) "
                        "WHERE id=? AND status='scraping' AND lease_token=?",
                        (job, job, job, lease_token),
                    )
                hosted.db.execute("COMMIT")
            except BaseException:
                hosted.db.execute("ROLLBACK")
                raise

        try:
            # Reconcile counters from persisted URL rows before resuming a job
            # that may have stopped between storing a result and updating totals.
            hosted.db.execute(
                "UPDATE firecrawl_batch_jobs SET "
                "completed=(SELECT COUNT(*) FROM firecrawl_batch_pages WHERE job=?), "
                "credits=(SELECT COALESCE(SUM(credits),0) FROM firecrawl_batch_pages WHERE job=?) "
                "WHERE id=? AND status='scraping' AND lease_token=?",
                (job, job, job, lease_token),
            )
            for start in range(0, len(urls), 10):
                state = hosted.db.execute(
                    "SELECT status,lease_token FROM firecrawl_batch_jobs WHERE id=?", (job,)
                ).fetchone()
                if not state or state != ("scraping", lease_token):
                    return
                existing = {row[0] for row in hosted.db.execute(
                    "SELECT seq FROM firecrawl_batch_pages WHERE job=? AND seq>=? AND seq<?",
                    (job, start, min(start + 10, len(urls))),
                )}
                pending = [(index, url) for index, url in enumerate(urls[start:start + 10], start)
                           if index not in existing]
                if not pending:
                    continue
                attempt = 0
                while pending:
                    state = hosted.db.execute(
                        "SELECT status,lease_token FROM firecrawl_batch_jobs WHERE id=?", (job,)
                    ).fetchone()
                    if not state or state != ("scraping", lease_token):
                        return
                    chunk = [url for _, url in pending]
                    call_options = {key: value for key, value in options.items() if not key.startswith("_")}
                    response = await batch_for(
                        account, {"urls": chunk, **call_options},
                        idempotency_keys=[f"firecrawl-batch:{job}:{index}" for index, _ in pending],
                    )
                    payload = unpack(response)
                    if response.status == 429 or response.status >= 500:
                        if attempt < 4:
                            await asyncio.sleep(min(15, 2 ** attempt))
                            attempt += 1
                            continue
                        for index, url in pending:
                            save_page(index, url, {"status": response.status,
                                                   "error": payload.get("error", "Batch service remained unavailable"),
                                                   "code": payload.get("code", "retry_exhausted")})
                        break
                    if response.status != 200:
                        for index, url in pending:
                            save_page(index, url, {"status": response.status,
                                                   "error": payload.get("error", "Batch request failed"),
                                                   "code": payload.get("code", "request_failed")})
                        break
                    entries = payload.get("results", []) if response.status == 200 else []
                    positions = {}
                    for pair in pending:
                        positions.setdefault(pair[1], []).append(pair)
                    retry = []
                    for item in entries:
                        if not isinstance(item, dict):
                            continue
                        candidates = positions.get(item.get("url"), [])
                        if not candidates:
                            continue
                        index, url = candidates.pop(0)
                        if item.get("status") == 429 and attempt < 4:
                            retry.append((index, url))
                        else:
                            save_page(index, url, item)
                    for candidates in positions.values():
                        retry.extend(candidates)
                    if not retry:
                        break
                    if attempt >= 4:
                        for index, url in retry:
                            save_page(index, url, {"status": 429,
                                                   "error": "Batch capacity remained unavailable after retries",
                                                   "code": "retry_exhausted"})
                        break
                    pending = retry
                    await asyncio.sleep(min(15, 2 ** attempt))
                    attempt += 1
            hosted.db.execute("BEGIN IMMEDIATE")
            try:
                finished = time.time()
                completed = hosted.db.execute(
                    "UPDATE firecrawl_batch_jobs SET status='completed',finished=?,lease_until=NULL,lease_token=NULL "
                    "WHERE id=? AND status='scraping' AND lease_token=?",
                    (finished, job, lease_token),
                ).rowcount
                if completed:
                    _queue_firecrawl_batch_webhook(hosted.db, job, options.get("_webhook"), "completed", [])
                hosted.db.execute("COMMIT")
            except BaseException:
                hosted.db.execute("ROLLBACK")
                raise
        except asyncio.CancelledError:
            # Service shutdown releases work for another process. Explicit API
            # cancellation changes status first and therefore remains cancelled.
            hosted.db.execute("UPDATE firecrawl_batch_jobs SET lease_until=NULL,lease_token=NULL WHERE id=? AND status='scraping' AND lease_token=?",
                              (job, lease_token))
            raise
        except Exception:
            logging.exception("Firecrawl-compatible batch failed job=%s", job)
            hosted.db.execute("UPDATE firecrawl_batch_jobs SET status='failed',finished=?,lease_until=NULL,lease_token=NULL WHERE id=? AND status='scraping' AND lease_token=?",
                              (time.time(), job, lease_token))
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            batch_tasks.pop(job, None)

    async def dispatch_batches():
        while True:
            due = hosted.db.execute(
                "SELECT id FROM firecrawl_batch_jobs WHERE status IN ('queued','scraping') "
                "AND (lease_until IS NULL OR lease_until<=?) ORDER BY created LIMIT 8",
                (time.time(),),
            ).fetchall()
            for (job,) in due:
                if len(batch_tasks) >= 4:
                    break
                if job not in batch_tasks:
                    batch_tasks[job] = asyncio.create_task(run_batch(job), name=f"firecrawl-batch-{job}")
            await asyncio.sleep(1)

    async def deliver_batch_webhook(row, client):
        event_id, job, event_type, payload, page_seq = row
        claimed = _claim_firecrawl_batch_webhook(hosted.db, event_id, time.time())
        if not claimed:
            return
        lease_token, attempts = claimed
        target = hosted.db.execute("SELECT account,options FROM firecrawl_batch_jobs WHERE id=?", (job,)).fetchone()
        if not target:
            hosted.db.execute("UPDATE firecrawl_batch_webhook_events SET state='cancelled',lease_until=NULL,lease_token=NULL,last_error='Batch job deleted' WHERE id=? AND lease_token=?",
                              (event_id, lease_token))
            return
        account, raw_options = target
        hook = json.loads(raw_options).get("_webhook")
        if not hook or not isinstance(hook.get("url"), str):
            hosted.db.execute("UPDATE firecrawl_batch_webhook_events SET state='cancelled',lease_until=NULL,lease_token=NULL,last_error='Webhook configuration missing' WHERE id=? AND lease_token=?",
                              (event_id, lease_token))
            return
        event = json.loads(payload)
        if page_seq is not None:
            page_row = hosted.db.execute("SELECT payload FROM firecrawl_batch_pages WHERE job=? AND seq=?",
                                         (job, page_seq)).fetchone()
            if not page_row:
                hosted.db.execute("UPDATE firecrawl_batch_webhook_events SET state='failed',lease_until=NULL,lease_token=NULL,last_error='Batch page result missing' WHERE id=? AND lease_token=?",
                                  (event_id, lease_token))
                return
            event["data"] = [json.loads(page_row[0])]
            body_text = json.dumps(event, separators=(",", ":"))
            if len(body_text.encode("utf-8")) > BATCH_WEBHOOK_MAX_BYTES:
                page = event["data"][0]
                metadata = page.get("metadata", {}) if isinstance(page, dict) else {}
                keep = {key: metadata[key] for key in ("url", "sourceURL", "statusCode", "contentType", "title", "description", "creditsUsed")
                        if key in metadata}
                event["data"] = [{"metadata": keep, "webhookTruncated": True,
                                  "message": "Page payload exceeded the webhook size limit; retrieve this page from batch status."}]
                event["webhookTruncated"] = True
                body_text = json.dumps(event, separators=(",", ":"))
        else:
            body_text = payload
        body = body_text.encode("utf-8")
        timestamp = str(int(time.time()))
        secret = _batch_webhook_secret(hosted, account, job).encode("utf-8")
        signature = hmac.new(secret, timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
        headers = {"Content-Type": "application/json", "X-Apex-Event-Id": event_id,
                   "X-Apex-Signature": f"t={timestamp},v1={signature}"}
        status = None
        try:
            if webhook_sender:
                status = await webhook_sender(hook["url"], body, headers)
            else:
                status = await _post_firecrawl_batch_webhook(client, hook["url"], body, headers)
            if not isinstance(status, int) or not 200 <= status < 300:
                raise aiohttp.ClientResponseError(None, (), status=status or 500)
            hosted.db.execute("UPDATE firecrawl_batch_webhook_events SET state='delivered',lease_until=NULL,lease_token=NULL,http_status=?,last_error=NULL WHERE id=? AND state='pending' AND lease_token=?",
                              (status, event_id, lease_token))
        except Exception as exc:
            if attempts >= 6:
                hosted.db.execute("UPDATE firecrawl_batch_webhook_events SET state='failed',lease_until=NULL,lease_token=NULL,http_status=?,last_error=? WHERE id=? AND state='pending' AND lease_token=?",
                                  (status, type(exc).__name__, event_id, lease_token))
            else:
                delay = min(30 * (2 ** (attempts - 1)), 1800)
                hosted.db.execute("UPDATE firecrawl_batch_webhook_events SET lease_until=NULL,lease_token=NULL,next_attempt=?,http_status=?,last_error=? WHERE id=? AND state='pending' AND lease_token=?",
                                  (time.time() + delay, status, type(exc).__name__, event_id, lease_token))

    async def dispatch_batch_webhooks():
        timeout = aiohttp.ClientTimeout(total=10, connect=4, sock_read=5)
        connector = aiohttp.TCPConnector(resolver=PublicResolver(), ttl_dns_cache=0)
        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as client:
            while True:
                pending = hosted.db.execute(
                    "SELECT id,job,event_type,payload,page_seq FROM firecrawl_batch_webhook_events WHERE state='pending' "
                    "AND next_attempt<=? AND (lease_until IS NULL OR lease_until<=?) ORDER BY next_attempt LIMIT 20",
                    (time.time(), time.time()),
                ).fetchall()
                for offset in range(0, len(pending), 4):
                    await asyncio.gather(*(deliver_batch_webhook(row, client) for row in pending[offset:offset + 4]))
                await asyncio.sleep(1)

    async def batch_worker(app):
        dispatcher = asyncio.create_task(dispatch_batches(), name="firecrawl-batch-dispatcher")
        webhooks = asyncio.create_task(dispatch_batch_webhooks(), name="firecrawl-batch-webhooks")
        yield
        dispatcher.cancel()
        webhooks.cancel()
        await asyncio.gather(dispatcher, webhooks, return_exceptions=True)
        active = list(batch_tasks.values())
        for task in active:
            task.cancel()
        if active:
            await asyncio.gather(*active, return_exceptions=True)

    app.cleanup_ctx.append(batch_worker)

    async def batch_scrape(request):
        body = await request.json()
        if not isinstance(body, dict):
            return error("Request body must be a JSON object", "invalid_request")
        urls = body.get("urls")
        if not isinstance(urls, list) or not urls or any(not isinstance(url, str) or not url.strip() for url in urls):
            return error("urls must be a non-empty array of URL strings", "invalid_request")
        plan = hosted.db.execute("SELECT plan FROM accounts WHERE id=?", (request["account"],)).fetchone()
        from .hosted_crawl import PAGE_LIMITS
        max_urls = PAGE_LIMITS.get(plan[0] if plan else "", 100)
        if len(urls) > max_urls:
            return error(f"This plan supports at most {max_urls} URLs per batch job", "batch_limit", 413)
        allowed = {"urls", "formats", "onlyMainContent", "maxAge", "screenshot", "actions", "waitFor",
                   "ignoreInvalidURLs", "origin", "blockAds", "removeBase64Images", "storeInCache",
                   "fastMode", "mobile", "webhook"}
        extra = set(body) - allowed
        if extra:
            return error("Unsupported Apex compatibility options: " + ", ".join(sorted(extra)))
        compat_warning, compat_error = sdk_default_warnings(body)
        if compat_error:
            return error(compat_error)
        if body.get("ignoreInvalidURLs", False) is not False:
            return error("ignoreInvalidURLs:true is not supported; invalid or unsafe destinations are reported per URL")
        if body.get("onlyMainContent", True) is not True:
            return error("onlyMainContent:false is not supported; Apex returns the main content")
        formats = body.get("formats", ["markdown"])
        if not isinstance(formats, list) or not formats:
            return error("formats must be a non-empty list")
        selected, extract_spec, attribute_selectors = [], None, None
        for item in formats:
            kind = item if isinstance(item, str) else item.get("type") if isinstance(item, dict) else None
            if kind == "json":
                if extract_spec is not None or isinstance(item, str):
                    return error("JSON output needs one object format with a schema or prompt")
                try:
                    extract_spec = json_extract_spec(item)
                except ValueError as exc:
                    return error(str(exc))
                selected.append("json")
                continue
            if kind == "attributes":
                if attribute_selectors is not None or not isinstance(item, dict) or set(item) != {"type", "selectors"}:
                    return error("Attributes output needs one object format with selectors")
                try:
                    attribute_selectors = validate_attribute_selectors(item["selectors"])
                except ValueError as exc:
                    return error(str(exc))
                selected.append("attributes")
                continue
            if isinstance(item, dict) and set(item) - {"type"}:
                return error("Format-specific options are not supported by this compatibility subset")
            if kind not in {"markdown", "html", "rawHtml", "links", "images", "screenshot"}:
                return error(f"Format {kind!r} is not supported; supported formats are markdown, html, rawHtml, links, images, screenshot")
            selected.append(kind)
        if "maxAge" in body and (isinstance(body["maxAge"], bool) or not isinstance(body["maxAge"], int)
                                 or not 0 <= body["maxAge"] <= 31536000000):
            return error("maxAge must be milliseconds from 0 to 31536000000")
        if "screenshot" in body and not isinstance(body["screenshot"], bool):
            return error("screenshot must be a boolean")
        outputs = {"raw_html" if item == "rawHtml" else item for item in selected
                   if item in {"markdown", "html", "rawHtml", "links", "images", "attributes", "screenshot"}}
        if "screenshot" in selected or body.get("screenshot", False):
            outputs.add("screenshot")
        apex_options = {"render": "auto"}
        if attribute_selectors is not None:
            apex_options["attribute_selectors"] = attribute_selectors
        if outputs:
            apex_options["outputs"] = sorted(outputs)
        if extract_spec:
            admission = extraction_admission(request["account"])
            if admission:
                return admission
            apex_options["extract"] = extract_spec
        if "maxAge" in body:
            apex_options["max_age"] = body["maxAge"]
        if body.get("actions") is not None or "waitFor" in body:
            try:
                actions = translate_wait_for(body.get("actions"), body.get("waitFor", 0))
                if actions:
                    apex_options["actions"] = actions
            except ValueError as exc:
                return error(str(exc))
        if "screenshot" in selected or body.get("screenshot", False):
            apex_options["screenshot"] = True
        webhook_config = None
        if "webhook" in body:
            try:
                webhook_config = _normalize_firecrawl_batch_webhook(body["webhook"])
            except (ValueError, ScrapeError) as exc:
                return error(str(exc), "invalid_webhook")
            apex_options["_webhook"] = webhook_config
        apex_options["_compat_formats"] = selected
        if compat_warning:
            apex_options["_compat_warning"] = compat_warning
        hosted.db.execute("DELETE FROM firecrawl_batch_pages WHERE job IN (SELECT id FROM firecrawl_batch_jobs WHERE created<?)",
                          (time.time() - 7 * 86400,))
        hosted.db.execute("DELETE FROM firecrawl_batch_webhook_events WHERE job IN (SELECT id FROM firecrawl_batch_jobs WHERE created<?)",
                          (time.time() - 7 * 86400,))
        hosted.db.execute("DELETE FROM firecrawl_batch_jobs WHERE created<?", (time.time() - 7 * 86400,))
        hosted.billing.db.execute("DELETE FROM idempotent_reservations WHERE created<?", (time.time() - 7 * 86400,))
        job = "batch_" + secrets.token_hex(12)
        created = time.time()
        hosted.db.execute("BEGIN IMMEDIATE")
        try:
            hosted.db.execute("INSERT INTO firecrawl_batch_jobs(id,account,status,urls,options,total,created) VALUES(?,?, 'scraping', ?,?,?,?)",
                              (job, request["account"], json.dumps(urls), json.dumps(apex_options), len(urls), created))
            _queue_firecrawl_batch_webhook(hosted.db, job, webhook_config, "started", [])
            hosted.db.execute("COMMIT")
        except BaseException:
            hosted.db.execute("ROLLBACK")
            raise
        batch_tasks[job] = asyncio.create_task(run_batch(job), name=f"firecrawl-batch-{job}")
        response = {"success": True, "id": job, "url": f"/v2/batch/scrape/{job}", "status": "scraping"}
        if webhook_config:
            response["signingSecret"] = _batch_webhook_secret(hosted, request["account"], job)
            response["signingSecretNotice"] = "Shown once. Verify X-Apex-Signature over the exact webhook request body."
        return web.json_response(response, status=200)

    async def batch_status_route(request):
        job_id = request.match_info["job"]
        row = batch_status(job_id, request["account"])
        total, completed, credits, created, finished = row[2], row[3], row[4], row[5], row[6]
        skip_raw = request.query.get("skip", "0")
        if not skip_raw.isdigit():
            return error("skip must be a non-negative integer", "invalid_request")
        skip = int(skip_raw)
        data = [json.loads(item[0]) for item in hosted.db.execute(
            "SELECT payload FROM firecrawl_batch_pages WHERE job=? AND seq>=? ORDER BY seq LIMIT 50", (job_id, skip))]
        end = skip + len(data)
        more = hosted.db.execute("SELECT 1 FROM firecrawl_batch_pages WHERE job=? AND seq>=? LIMIT 1", (job_id, end)).fetchone()
        now = time.time()
        iso = lambda stamp: datetime.fromtimestamp(stamp, timezone.utc).isoformat().replace("+00:00", "Z") if stamp else None
        next_url = f"/v2/batch/scrape/{job_id}?skip={end}" if more else None
        return web.json_response({"success": True, "status": row[1], "total": total, "completed": completed,
                                  "creditsUsed": credits, "createdAt": iso(created),
                                  "completedAt": iso(finished), "expiresAt": iso(created + 7 * 86400),
                                  "duration": max(0, (finished or now) - created), "next": next_url, "data": data})

    async def batch_errors(request):
        row = batch_status(request.match_info["job"], request["account"])
        records = []
        for seq, url, status, payload in hosted.db.execute(
                "SELECT seq,url,status,payload FROM firecrawl_batch_pages WHERE job=? AND status!=200 ORDER BY seq LIMIT 500",
                (row[0],)):
            item = json.loads(payload)
            records.append({"id": f"{row[0]}-{seq}", "url": url, "status": status,
                            "error": item.get("metadata", {}).get("error", "Scrape failed")})
        return web.json_response({"errors": records})

    async def batch_cancel(request):
        row = batch_status(request.match_info["job"], request["account"])
        if row[1] == "scraping":
            task = batch_tasks.get(row[0])
            hosted.db.execute("UPDATE firecrawl_batch_jobs SET status='cancelled',finished=?,lease_until=NULL,lease_token=NULL WHERE id=? AND status='scraping'",
                              (time.time(), row[0]))
            if task:
                task.cancel()
        return web.json_response({"status": "cancelled" if row[1] == "scraping" else row[1]})

    def wrap(handler):
        async def wrapped(request):
            try:
                return await handler(request)
            except (ValueError, KeyError, TypeError, ScrapeError) as exc:
                code = getattr(exc, "code", None) or "invalid_request"
                status = {"paid_plan_required": 402, "insufficient_credits": 402,
                          "session_capacity": 429, "not_found": 404,
                          "session_expired": 410, "browser_unavailable": 503,
                          "blocked_origin": 403}.get(code, 400)
                return error(str(exc), code, status)
            except web.HTTPException as exc:
                return error(exc.reason, "request_failed", exc.status)
        return wrapped

    app.router.add_post("/v2/scrape", wrap(scrape))
    app.router.add_post("/v2/parse", wrap(parse_document))
    app.router.add_get("/v2/parse/formats", wrap(parse_format_list))
    app.router.add_get("/v2/team/credit-usage", wrap(credit_usage))
    app.router.add_get("/v2/team/credit-usage/historical", wrap(historical_credit_usage))
    app.router.add_post("/v2/map", wrap(map_route))
    app.router.add_post("/v2/search", wrap(search))
    app.router.add_post("/v2/crawl", wrap(crawl))
    app.router.add_get("/v2/crawl/active", wrap(crawl_active))
    app.router.add_get("/v2/crawl/{job}", wrap(crawl_status))
    app.router.add_delete("/v2/crawl/{job}", wrap(crawl_cancel))
    app.router.add_get("/v2/crawl/{job}/errors", wrap(crawl_errors))
    app.router.add_post("/v2/interact", wrap(interact_create))
    app.router.add_get("/v2/interact", wrap(interact_list))
    app.router.add_post("/v2/interact/{session}/execute", wrap(interact_execute))
    app.router.add_delete("/v2/interact/{session}", wrap(interact_delete))
    app.router.add_post("/v2/batch/scrape", wrap(batch_scrape))
    app.router.add_get("/v2/batch/scrape/{job}/errors", wrap(batch_errors))
    app.router.add_get("/v2/batch/scrape/{job}", wrap(batch_status_route))
    app.router.add_delete("/v2/batch/scrape/{job}", wrap(batch_cancel))
