import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { join } from "node:path";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { CallToolRequestSchema, ListToolsRequestSchema } from "@modelcontextprotocol/sdk/types.js";
import { runDoctor } from "./bin/apex-flow-web-scraper.mjs";
import { localByokSearch, selectLocalSearchProvider, startMcpBridge } from "./mcp-bridge.mjs";

test("stdio bridge requires its API key in the process environment", async () => {
  await assert.rejects(
    startMcpBridge({ apiKey: "", stderr: { write() {} } }),
    /Set APEX_SCRAPER_API_KEY/,
  );
});

test("doctor verifies authenticated MCP discovery without scraping or exposing the key", async () => {
  const key = "private-doctor-key";
  const authHeaders = [];
  const http = createServer(async (req, res) => {
    authHeaders.push(req.headers.authorization);
    let raw = "";
    for await (const part of req) raw += part;
    const body = raw ? JSON.parse(raw) : undefined;
    const server = new Server({ name: "mock-apex", version: "1.0.0" }, { capabilities: { tools: {} } });
    server.setRequestHandler(ListToolsRequestSchema, async () => ({
      tools: [{ name: "scrape_page", description: "Mock scraper", inputSchema: { type: "object", properties: {} } }],
    }));
    const transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined });
    await server.connect(transport);
    await transport.handleRequest(req, res, body);
    res.on("close", () => { void transport.close(); void server.close(); });
  });
  await new Promise((resolve) => http.listen(0, "127.0.0.1", resolve));
  const address = http.address();
  let output = "";
  try {
    const result = await runDoctor({ apiKey: key, remoteUrl: `http://127.0.0.1:${address.port}/mcp`,
      stderr: { write: (text) => { output += text; } } });
    assert.equal(result.toolCount, 1);
    assert.match(output, /tool discovery succeeded/);
    assert.match(output, /No scrape was run and no credits were used/);
    assert.ok(authHeaders.length >= 2);
    assert.ok(authHeaders.every((header) => header === `Bearer ${key}`));
    assert.equal(output.includes(key), false);
  } finally {
    await new Promise((resolve) => http.close(resolve));
  }
});

test("doctor rejects missing keys and non-HTTPS remote endpoints", async () => {
  await assert.rejects(runDoctor({ apiKey: "", stderr: { write() {} } }), /Set APEX_SCRAPER_API_KEY/);
  await assert.rejects(runDoctor({ apiKey: "secret", remoteUrl: "http://example.com/mcp", stderr: { write() {} } }), /must use HTTPS/);
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
    assert.deepEqual(listed.tools.map((tool) => tool.name), ["scrape_page", "web_search_byok"]);
    const result = await bridge.callTool({ name: "scrape_page", arguments: { url: "https://example.com" } });
    assert.equal(result.content[0].text, "read:https://example.com");
    const localSearch = await bridge.callTool({ name: "web_search_byok", arguments: { query: "example" } });
    assert.equal(localSearch.isError, true);
    assert.match(localSearch.content[0].text, /Configure exactly one local provider key/);
    assert.ok(authHeaders.length >= 3);
    assert.ok(authHeaders.every((header) => header === "Bearer test-key"));
  } finally {
    await bridge.close();
    await new Promise((resolve) => http.close(resolve));
  }
});

test("BYOK search selects one local provider and sends no key to Apex", async () => {
  const env = { BRAVE_SEARCH_API_KEY: "local-secret" };
  assert.equal(selectLocalSearchProvider(env), "brave");
  assert.equal(selectLocalSearchProvider({ ...env, EXA_API_KEY: "another" }), null);
  assert.equal(selectLocalSearchProvider({ ...env, EXA_API_KEY: "another", APEX_SEARCH_PROVIDER: "exa" }), "exa");
  let request;
  const result = await localByokSearch("example query", 2, {
    env,
    fetchImpl: async (url, options) => {
      request = { url: String(url), options };
      return { ok: true, json: async () => ({ web: { results: [
        { url: "https://example.com", title: "Example", description: "Direct result" },
        { url: "javascript:alert(1)", title: "Invalid", description: "Drop" },
      ] } }) };
    },
  }, { includeDomains: ["example.com"] });
  assert.equal(request.url, "https://api.search.brave.com/res/v1/web/search?q=example+query+%28site%3Aexample.com%29&count=2");
  assert.equal(request.options.headers["X-Subscription-Token"], "local-secret");
  assert.equal(request.options.redirect, "error");
  assert.deepEqual(result.results, [{ url: "https://example.com", title: "Example", snippet: "Direct result" }]);
  assert.match(result.note, /Apex did not receive your provider key/);
});

test("BYOK Tavily and Exa requests use direct vendor contracts", async () => {
  for (const [provider, envKey, expectedUrl, payload, expectedHeader, expectedValue] of [
    ["tavily", "TAVILY_API_KEY", "https://api.tavily.com/search", { results: [{ url: "https://t.example/", title: "T", content: "Tavily" }] }, "Authorization", "Bearer local-tavily"],
    ["exa", "EXA_API_KEY", "https://api.exa.ai/search", { results: [{ url: "https://e.example/", title: "E", highlights: ["Exa", "result"] }] }, "x-api-key", "local-exa"],
  ]) {
    const env = { [envKey]: `local-${provider}` };
    let request;
    const result = await localByokSearch("search", 3, { env, fetchImpl: async (url, options) => {
      request = { url: String(url), options };
      return { ok: true, json: async () => payload };
    } }, { excludeDomains: ["blocked.example"] });
    assert.equal(request.url, expectedUrl);
    assert.equal(request.options.headers[expectedHeader], expectedValue);
    assert.equal(request.options.redirect, "error");
    const sent = JSON.parse(request.options.body);
    assert.deepEqual(sent.exclude_domains || sent.excludeDomains, ["blocked.example"]);
    assert.equal(result.provider, provider);
    assert.equal(result.results[0].snippet, provider === "tavily" ? "Tavily" : "Exa result");
    assert.deepEqual(Object.keys(request.options.headers).sort(), ["Content-Type", expectedHeader].sort());
  }
});

test("BYOK Brave image search uses the image index and returns source metadata", async () => {
  const env = { BRAVE_SEARCH_API_KEY: "local-secret" };
  let request;
  const result = await localByokSearch("apex logo", 2, {
    env,
    fetchImpl: async (url, options) => {
      request = { url: String(url), options };
      return { ok: true, json: async () => ({ results: [{
        title: "Apex logo", url: "https://example.com/about",
        properties: { url: "https://cdn.example.com/logo.png", width: 640, height: 320 },
      }] }) };
    },
  }, { topic: "images", includeDomains: ["example.com"] });
  assert.match(request.url, /^https:\/\/api\.search\.brave\.com\/res\/v1\/images\/search\?/);
  assert.match(request.url, /safesearch=strict/);
  assert.equal(request.options.headers["X-Subscription-Token"], "local-secret");
  assert.deepEqual(result.results, [{ title: "Apex logo", imageUrl: "https://cdn.example.com/logo.png",
    imageWidth: 640, imageHeight: 320, url: "https://example.com/about" }]);
  await assert.rejects(localByokSearch("apex", 2, { env: { TAVILY_API_KEY: "t" }, fetchImpl: async () => {} }, { topic: "images" }), /requires the configured Brave provider/);
});
