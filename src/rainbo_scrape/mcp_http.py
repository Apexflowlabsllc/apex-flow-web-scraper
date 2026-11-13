"""Stateless JSON responses for the MCP Streamable HTTP transport."""
import json

from aiohttp import web
from mcp.server.fastmcp.exceptions import ToolError
from .hosted_mcp import local_dispatch, mcp

VERSION = "2025-06-18"


def install(app, billing, operations, site):
    async def handle(request):
        if request.headers.get("Origin") not in (None, site):
            raise web.HTTPForbidden()
        if request.headers.get("MCP-Protocol-Version", VERSION) != VERSION:
            raise web.HTTPBadRequest(reason="Unsupported protocol version")
        if request.content_type != "application/json":
            raise web.HTTPUnsupportedMediaType()
        message = await request.json()
        ident = message.get("id") if isinstance(message, dict) else None

        def reply(result=None, code=None, error=None):
            data = {"jsonrpc": "2.0", "id": ident}
            if code is None:
                data["result"] = result
            else:
                data["error"] = {"code": code, "message": error}
            return web.json_response(data)

        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return reply(code=-32600, error="Invalid JSON-RPC request")
        method = message.get("method")
        if ident is None:
            return web.Response(status=202)
        params = message.get("params", {})
        if not isinstance(params, dict):
            return reply(code=-32602, error="Expected an object for params")
        if method == "initialize":
            return reply({"protocolVersion": VERSION, "capabilities": {"tools": {}},
                          "serverInfo": {"name": "Apex Flow 3D Scraper", "version": "0.9.0"},
                          "instructions": mcp.instructions})
        if method == "ping":
            return reply({})
        if method == "tools/list":
            from .hosted_search import configured
            return reply({"tools": [tool.model_dump(mode="json", exclude_none=True) for tool in await mcp.list_tools()
                                    if tool.name != "web_search" or configured() or "/websearch_enabled" in operations]})
        if method != "tools/call":
            return reply(code=-32601, error="Unknown method")
        if not isinstance(params.get("name"), str) or not isinstance(params.get("arguments", {}), dict):
            return reply(code=-32602, error="Expected a tool name and arguments object")

        async def dispatch(path, payload):
            response = await operations[path](request["account"], payload)
            result = json.loads(response.body)
            if response.status >= 400:
                # Agents get the same stable code and next step that REST clients do.
                detail = {k: result[k] for k in ("code", "retryable", "retry_after", "credits_left", "buy_url", "hint") if k in result}
                raise ValueError((result.get("error") or "Scraper request failed") + (" " + json.dumps(detail) if detail else ""))
            return result

        token = local_dispatch.set(dispatch)
        try:
            blocks = await mcp.call_tool(params["name"], params.get("arguments", {}))
            return reply({"content": [b.model_dump(mode="json", exclude_none=True) for b in blocks]})
        except (ToolError, ValueError) as exc:
            return reply({"isError": True, "content": [{"type": "text", "text": str(exc)[:600]}]})
        finally:
            local_dispatch.reset(token)

    app.router.add_post("/v1/mcp", handle)
