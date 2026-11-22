"""A deliberately partial Firecrawl v2 wire adapter over Apex's hosted features.

This preserves Apex billing and source-safety rules. Unsupported input is
rejected instead of being accepted and silently ignored. See the site guide for
the exact supported subset; this is not a claim of complete SDK compatibility.
"""
import asyncio
import json
import logging
import os
import secrets
import time
from datetime import datetime, timedelta, timezone

from aiohttp import web
from .billing import public_usage_key_id
from .policy import ScrapeError

INTERACT_SERVICE_KEY = web.AppKey("interact_service", object)


def install(app, perform, map_for, websearch_for, crawl_start_for, crawl_status_for, crawl_cancel_for,
            batch_for, hosted):
    from .interact import InteractService
    interact = InteractService(hosted)
    app[INTERACT_SERVICE_KEY] = interact
    app.on_cleanup.append(interact.close_all)
    batch_tasks = {}
    hosted.db.executescript("""
        CREATE TABLE IF NOT EXISTS firecrawl_batch_jobs(
            id TEXT PRIMARY KEY, account TEXT NOT NULL, status TEXT NOT NULL, urls TEXT NOT NULL,
            options TEXT NOT NULL, total INTEGER NOT NULL, completed INTEGER NOT NULL DEFAULT 0,
            credits INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL, finished REAL);
        CREATE TABLE IF NOT EXISTS firecrawl_batch_pages(
            job TEXT NOT NULL, seq INTEGER NOT NULL, url TEXT NOT NULL, status INTEGER NOT NULL,
            payload TEXT NOT NULL, credits INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(job,seq));
        CREATE INDEX IF NOT EXISTS firecrawl_batch_account ON firecrawl_batch_jobs(account,created);
    """)
    hosted.db.execute("UPDATE firecrawl_batch_jobs SET status='failed',finished=? WHERE status='scraping'", (time.time(),))
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
        allowed = {"url", "formats", "onlyMainContent", "maxAge", "screenshot", "actions",
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
        parsed, extract_spec = [], None
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
            if isinstance(item, dict) and set(item) - {"type"}:
                return error("Format-specific options are not supported by this compatibility subset")
            if kind not in {"markdown", "html", "rawHtml", "links", "screenshot"}:
                return error(f"Format {kind!r} is not supported; supported formats are markdown, html, rawHtml, links, screenshot")
            parsed.append("raw_html" if kind == "rawHtml" else kind)
        if "maxAge" in body and (isinstance(body["maxAge"], bool) or not isinstance(body["maxAge"], int)
                                 or not 0 <= body["maxAge"] <= 31536000000):
            return error("maxAge must be milliseconds from 0 to 31536000000")
        if "screenshot" in body and not isinstance(body["screenshot"], bool):
            return error("screenshot must be a boolean")
        apex_body = {"url": url, "render": "auto"}
        apex_outputs = {"raw_html" if field == "raw_html" else field for field in parsed
                        if field in {"markdown", "html", "raw_html", "links", "screenshot"}}
        if apex_outputs:
            apex_body["outputs"] = sorted(apex_outputs)
        if extract_spec:
            apex_body["extract"] = extract_spec
        if "maxAge" in body:
            apex_body["max_age"] = body["maxAge"]
        if body.get("actions") is not None:
            try:
                apex_body["actions"] = translate_actions(body["actions"])
            except ValueError as exc:
                return error(str(exc))
        want_screenshot = "screenshot" in parsed or body.get("screenshot", False)
        if want_screenshot:
            apex_body["screenshot"] = True
        response = await perform(request["account"], apex_body)
        payload = unpack(response)
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
        allowed = {"query", "limit", "sources"}
        extra = set(body) - allowed
        if extra:
            return error("Unsupported Apex compatibility options: " + ", ".join(sorted(extra)))
        sources = body.get("sources", [{"type": "web"}])
        if not isinstance(sources, list) or not sources or any(not isinstance(s, dict) or s.get("type") != "web" or set(s) != {"type"} for s in sources):
            return error("This adapter supports only the Firecrawl web search source")
        request_body = {key: body[key] for key in ("query", "limit") if key in body}
        response = await websearch_for(request["account"], request_body)
        payload = unpack(response)
        failure = forwarded(response, payload)
        if failure:
            return failure
        return web.json_response({"success": True,
                                 "data": {"web": [{"url": item.get("url"), "title": item.get("title"),
                                                    "description": item.get("snippet")}
                                                   for item in payload.get("results", [])]}})

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
        supported_scrape = {"formats", "onlyMainContent"}
        extra_scrape = set(scrape_options) - supported_scrape
        if extra_scrape:
            return error("Unsupported scrapeOptions: " + ", ".join(sorted(extra_scrape)))
        if scrape_options.get("onlyMainContent", True) is not True:
            return error("onlyMainContent:false is not supported")
        formats = scrape_options.get("formats", [{"type": "markdown"}])
        if not isinstance(formats, list) or not formats:
            return error("Crawl formats must be a non-empty list")
        crawl_formats, extract_spec = [], None
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
            if isinstance(item, dict) and set(item) - {"type"}:
                return error("Format-specific options are not supported by this compatibility subset")
            if kind not in {"markdown", "html", "rawHtml", "links", "screenshot"}:
                return error(f"Crawl format {kind!r} is not supported; supported formats are markdown, html, rawHtml, links, screenshot")
            crawl_formats.append(kind)
        outputs = sorted({"raw_html" if item == "rawHtml" else item for item in crawl_formats
                          if item in {"markdown", "html", "rawHtml", "links", "screenshot"}})
        start_body = {"url": body.get("url"), "limit": body.get("limit", 25), "render": "auto",
                      "outputs": outputs, "max_depth": body.get("maxDiscoveryDepth", 3)}
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

    async def run_batch(job, account, urls, options):
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
                for field in ("markdown", "html", "rawHtml", "links", "screenshot", "json"):
                    if field not in options.get("_compat_formats", []) and not (
                            field == "screenshot" and options.get("screenshot", False)):
                        result.pop(field, None)
            else:
                result = {"metadata": {"url": url, "sourceURL": url, "statusCode": status,
                                       "error": item.get("error", "Scrape failed")}}
            hosted.db.execute("INSERT OR REPLACE INTO firecrawl_batch_pages VALUES(?,?,?,?,?,?)",
                              (job, index, url, status, json.dumps(result), credits))
            hosted.db.execute("UPDATE firecrawl_batch_jobs SET completed=completed+1,credits=credits+? WHERE id=?",
                              (credits, job))

        try:
            for start in range(0, len(urls), 10):
                state = hosted.db.execute("SELECT status FROM firecrawl_batch_jobs WHERE id=?", (job,)).fetchone()
                if not state or state[0] != "scraping":
                    return
                pending = [(start + offset, url) for offset, url in enumerate(urls[start:start + 10])]
                attempt = 0
                while pending:
                    state = hosted.db.execute("SELECT status FROM firecrawl_batch_jobs WHERE id=?", (job,)).fetchone()
                    if not state or state[0] != "scraping":
                        return
                    chunk = [url for _, url in pending]
                    call_options = {key: value for key, value in options.items() if not key.startswith("_")}
                    response = await batch_for(account, {"urls": chunk, **call_options})
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
            hosted.db.execute("UPDATE firecrawl_batch_jobs SET status='completed',finished=? WHERE id=? AND status='scraping'",
                              (time.time(), job))
        except asyncio.CancelledError:
            hosted.db.execute("UPDATE firecrawl_batch_jobs SET status='cancelled',finished=? WHERE id=? AND status='scraping'",
                              (time.time(), job))
            raise
        except Exception:
            logging.exception("Firecrawl-compatible batch failed job=%s", job)
            hosted.db.execute("UPDATE firecrawl_batch_jobs SET status='failed',finished=? WHERE id=? AND status='scraping'",
                              (time.time(), job))
        finally:
            batch_tasks.pop(job, None)

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
        allowed = {"urls", "formats", "onlyMainContent", "maxAge", "screenshot", "actions",
                   "ignoreInvalidURLs", "origin", "blockAds", "removeBase64Images", "storeInCache",
                   "fastMode", "mobile"}
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
        selected, extract_spec = [], None
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
            if isinstance(item, dict) and set(item) - {"type"}:
                return error("Format-specific options are not supported by this compatibility subset")
            if kind not in {"markdown", "html", "rawHtml", "links", "screenshot"}:
                return error(f"Format {kind!r} is not supported; supported formats are markdown, html, rawHtml, links, screenshot")
            selected.append(kind)
        if "maxAge" in body and (isinstance(body["maxAge"], bool) or not isinstance(body["maxAge"], int)
                                 or not 0 <= body["maxAge"] <= 31536000000):
            return error("maxAge must be milliseconds from 0 to 31536000000")
        if "screenshot" in body and not isinstance(body["screenshot"], bool):
            return error("screenshot must be a boolean")
        outputs = {"raw_html" if item == "rawHtml" else item for item in selected
                   if item in {"markdown", "html", "rawHtml", "links", "screenshot"}}
        if "screenshot" in selected or body.get("screenshot", False):
            outputs.add("screenshot")
        apex_options = {"render": "auto"}
        if outputs:
            apex_options["outputs"] = sorted(outputs)
        if extract_spec:
            admission = extraction_admission(request["account"])
            if admission:
                return admission
            apex_options["extract"] = extract_spec
        if "maxAge" in body:
            apex_options["max_age"] = body["maxAge"]
        if body.get("actions") is not None:
            try:
                apex_options["actions"] = translate_actions(body["actions"])
            except ValueError as exc:
                return error(str(exc))
        if "screenshot" in selected or body.get("screenshot", False):
            apex_options["screenshot"] = True
        apex_options["_compat_formats"] = selected
        if compat_warning:
            apex_options["_compat_warning"] = compat_warning
        hosted.db.execute("DELETE FROM firecrawl_batch_pages WHERE job IN (SELECT id FROM firecrawl_batch_jobs WHERE created<?)",
                          (time.time() - 7 * 86400,))
        hosted.db.execute("DELETE FROM firecrawl_batch_jobs WHERE created<?", (time.time() - 7 * 86400,))
        job = "batch_" + secrets.token_hex(12)
        created = time.time()
        hosted.db.execute("INSERT INTO firecrawl_batch_jobs(id,account,status,urls,options,total,created) VALUES(?,?, 'scraping', ?,?,?,?)",
                          (job, request["account"], json.dumps(urls), json.dumps(apex_options), len(urls), created))
        batch_tasks[job] = asyncio.create_task(run_batch(job, request["account"], urls, apex_options))
        return web.json_response({"success": True, "id": job,
                                  "url": f"/v2/batch/scrape/{job}",
                                  "status": "scraping"}, status=200)

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
            hosted.db.execute("UPDATE firecrawl_batch_jobs SET status='cancelled',finished=? WHERE id=? AND status='scraping'",
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
