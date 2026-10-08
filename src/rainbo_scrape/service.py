import difflib
import json
import time
from .engine import validate_request
from .settings import Settings, brave_path
from .store import Store
from .worker import ensure_worker


class Service:
    def __init__(self, settings=None):
        self.settings = settings or Settings()
        self.store = Store(self.settings)

    def submit(self, request):
        options = validate_request(request, self.settings)
        result = self.store.create(options)
        ensure_worker(self.settings)
        return result

    def health(self):
        heartbeat = self.settings.data_dir / "worker-heartbeat.json"
        age = round(time.time()-heartbeat.stat().st_mtime, 2) if heartbeat.exists() else None
        return {"product": "Apex Flow Web Scraper", "version": "0.1.0", "database": "ready",
                "brave_available": bool(brave_path()), "worker_heartbeat_age_seconds": age,
                "worker_alive": age is not None and age < 10, "hosted_scraping_services": False,
                "proxy_configured": bool(self.settings.proxy_url),
                "formats": ["HTML", "Markdown", "JSON", "CSV export", "RSS/Atom", "sitemap", "text PDF"],
                "limits": {"pages_per_job": 1000, "concurrency": 4, "renderers": 1},
                "not_supported": ["CAPTCHA bypass", "private accounts", "OCR", "first-party residential proxy network", "global search index"]}

    def compare(self, before, after):
        def rows(ident):
            output = {}
            for offset in range(0, self.store.status(ident)["total"], 50):
                for item in self.store.results(ident, offset, 50):
                    if item["result"]:
                        output[item["url"]] = item["result"]
            return output
        old, new = rows(before), rows(after)
        changes = []
        for url in sorted(set(old) | set(new)):
            a, b = old.get(url), new.get(url)
            state = "added" if a is None else "removed" if b is None else "unchanged" if a["content_sha256"] == b["content_sha256"] and a.get("data") == b.get("data") else "changed"
            change = {"url": url, "change": state}
            if state == "changed":
                change["fields"] = {k: {"before": a.get("data", {}).get(k), "after": b.get("data", {}).get(k)}
                                    for k in set(a.get("data", {})) | set(b.get("data", {}))
                                    if a.get("data", {}).get(k) != b.get("data", {}).get(k)}
                change["text_diff"] = "\n".join(list(difflib.unified_diff(a["text"].splitlines(), b["text"].splitlines(), n=2))[:100])[:10000]
            changes.append(change)
        return {"before": before, "after": after, "changes": changes,
                "note": "Missing results can reflect crawl failure or scope changes; removed does not prove source deletion."}


def compact_results(rows, max_chars=18000):
    output, used = [], 0
    for row in rows:
        r = dict(row)
        if r.get("result"):
            result = dict(r["result"])
            result["text"] = result.get("text", "")[:2500]
            result["markdown"] = result.get("markdown", "")[:2500]
            result["links"] = result.get("links", [])[:25]
            result["tables"] = result.get("tables", [])[:5]
            r["result"] = result
        size = len(json.dumps(r))
        if used+size > max_chars:
            output.append({"url": r["url"], "state": r["state"], "summary_omitted": "Use dashboard JSON export for full result"})
        else:
            output.append(r)
            used += size
    return {"items": output, "view": "bounded agent summary; dashboard export contains complete stored results"}
