"""Owner workspace, loopback only; no public unauthenticated scraping endpoint."""
import csv
import io
import json
from pathlib import Path
from aiohttp import web
from .service import Service
from .policy import ScrapeError


def app(service=None):
    service = service or Service()
    @web.middleware
    async def boundary(request, handler):
        host = request.host.split(":")[0]
        if host not in {"127.0.0.1", "localhost"}:
            raise web.HTTPForbidden(text="Local workspace only")
        origin = request.headers.get("Origin")
        if origin and origin not in {f"http://127.0.0.1:{request.url.port}", f"http://localhost:{request.url.port}"}:
            raise web.HTTPForbidden(text="Cross-origin access denied")
        if request.method == "POST" and request.content_type != "application/json":
            raise web.HTTPUnsupportedMediaType()
        try:
            response = await handler(request)
        except (ValueError, KeyError, TypeError, ScrapeError) as exc:
            response = web.json_response({"error": str(exc)}, status=400)
        response.headers.update({"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer", "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'"})
        return response
    application = web.Application(middlewares=[boundary], client_max_size=50000)
    async def api(request):
        path = request.match_info["path"]
        data = await request.json() if request.method == "POST" else {}
        if path == "health":
            result = service.health()
        elif path == "jobs" and request.method == "POST":
            result = service.submit(data)
        elif path == "jobs":
            result = service.store.list_jobs(30)
        elif path == "recipes" and request.method == "POST":
            result = service.store.put_recipe(data["name"], data["recipe"])
        elif path == "recipes":
            result = service.store.recipes()
        elif path == "search":
            result = service.store.search(request.query.get("q", ""))
        elif path == "compare":
            result = service.compare(request.query["before"], request.query["after"])
        elif path.startswith("jobs/"):
            parts = path.split("/")
            ident = parts[1]
            action = parts[2] if len(parts) > 2 else "status"
            if action == "cancel" and request.method == "POST":
                result = service.store.cancel(ident)
            elif action == "results":
                result = service.store.results(ident, int(request.query.get("offset", 0)), 20)
            elif action == "export":
                rows = []
                for offset in range(0, service.store.status(ident)["total"], 50):
                    rows.extend(service.store.results(ident, offset, 50))
                fmt = request.query.get("format", "json")
                if fmt == "csv":
                    stream = io.StringIO()
                    fields = ["url", "state", "title", "text", "data", "source_sha256"]
                    writer = csv.DictWriter(stream, fieldnames=fields)
                    writer.writeheader()
                    for row in rows:
                        result = row.get("result") or {}
                        item = {k: row.get(k, result.get(k, "")) for k in fields}
                        item["data"] = json.dumps(item["data"], ensure_ascii=False)
                        # Prevent spreadsheet formula execution from scraped content.
                        for k, v in item.items():
                            if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@")):
                                item[k] = "'" + v
                        writer.writerow(item)
                    return web.Response(text=stream.getvalue(), content_type="text/csv",
                                        headers={"Content-Disposition": f'attachment; filename="{ident}.csv"'})
                return web.Response(text=json.dumps(rows, ensure_ascii=False, indent=2), content_type="application/json",
                                    headers={"Content-Disposition": f'attachment; filename="{ident}.json"'})
            else:
                result = service.store.status(ident)
        else:
            raise web.HTTPNotFound()
        return web.json_response(result)
    application.router.add_route("GET", "/api/{path:.*}", api)
    application.router.add_route("POST", "/api/{path:.*}", api)
    assets = Path(__file__).parent / "ui"
    async def index(request):
        return web.FileResponse(assets / "index.html")
    application.router.add_get("/", index)
    application.router.add_static("/assets/", assets)
    return application


def run(port=8840):
    web.run_app(app(), host="127.0.0.1", port=port, access_log=None)
