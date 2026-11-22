import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { join } from "node:path";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { CallToolRequestSchema, ListToolsRequestSchema } from "@modelcontextprotocol/sdk/types.js";
import { startMcpBridge } from "./mcp-bridge.mjs";

test("stdio bridge requires its API key in the process environment", async () => {
  await assert.rejects(
    startMcpBridge({ apiKey: "", stderr: { write() {} } }),
    /Set APEX_SCRAPER_API_KEY/,
  );
});

test("stdio bridge forwards list and tool calls to authenticated remote MCP", async () => {
  const authHeaders = [];
  const http = createServer(async (req, res) => {
    authHeaders.push(req.headers.authorization);
    let raw = "";
    for await (const part of req) raw += part;
    const body = raw ? JSON.parse(raw) : undefined;
    const remoteServer = new Server({ name: "mock-apex", version: "1.0.0" }, { capabilities: { tools: {} } });
    remoteServer.setRequestHandler(ListToolsRequestSchema, async () => ({
      tools: [{ name: "scrape_page", description: "Mock page scraper", inputSchema: { type: "object", properties: { url: { type: "string" } }, required: ["url"] } }],
    }));
    remoteServer.setRequestHandler(CallToolRequestSchema, async (request) => ({
      content: [{ type: "text", text: `read:${request.params.arguments.url}` }],
    }));
    const transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined });
    await remoteServer.connect(transport);
    await transport.handleRequest(req, res, body);
    res.on("close", () => { void transport.close(); void remoteServer.close(); });
  });
  await new Promise((resolve) => http.listen(0, "127.0.0.1", resolve));
  const address = http.address();
  const bridgePath = join(process.cwd(), "bin", "apex-flow-web-scraper.mjs");
  const bridge = new Client({ name: "installer-e2e-test", version: "1.0.0" });
  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [bridgePath, "mcp"],
    env: { ...process.env, APEX_SCRAPER_API_KEY: "test-key", APEX_SCRAPER_MCP_URL: `http://127.0.0.1:${address.port}/mcp` },
  });
  try {
    await bridge.connect(transport);
    const listed = await bridge.listTools();
    assert.deepEqual(listed.tools.map((tool) => tool.name), ["scrape_page"]);
    const result = await bridge.callTool({ name: "scrape_page", arguments: { url: "https://example.com" } });
    assert.equal(result.content[0].text, "read:https://example.com");
    assert.ok(authHeaders.length >= 3);
    assert.ok(authHeaders.every((header) => header === "Bearer test-key"));
  } finally {
    await bridge.close();
    await new Promise((resolve) => http.close(resolve));
  }
});
