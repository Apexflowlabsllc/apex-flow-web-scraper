// Run the hosted remote MCP server as a local stdio server. This lets MCP
// clients that only support stdio use the same hosted tools without storing
// the API key in a client configuration file.
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { CallToolRequestSchema, ListToolsRequestSchema } from "@modelcontextprotocol/sdk/types.js";
import { pathToFileURL } from "node:url";

const providerKeys = { brave: "BRAVE_SEARCH_API_KEY", tavily: "TAVILY_API_KEY", exa: "EXA_API_KEY" };

export function selectLocalSearchProvider(env = process.env) {
  const available = Object.entries(providerKeys).filter(([, key]) => env[key]).map(([name]) => name);
  const selected = (env.APEX_SEARCH_PROVIDER || "").trim().toLowerCase();
  const provider = selected || (available.length === 1 ? available[0] : "");
  return providerKeys[provider] && available.includes(provider) ? provider : null;
}

function normalize(results, snippetField, limit) {
  const normalized = [];
  for (const item of Array.isArray(results) ? results : []) {
    let url;
    try { url = new URL(item.url); } catch { continue; }
    if (!["http:", "https:"].includes(url.protocol) || !url.hostname) continue;
    const value = item[snippetField];
    const snippet = Array.isArray(value) ? value.filter((part) => typeof part === "string").join(" ")
      : typeof value === "string" ? value : item.text || item.summary || "";
    normalized.push({ url: item.url, title: item.title || "", snippet });
    if (normalized.length >= limit) break;
  }
  return normalized;
}

export async function localByokSearch(query, limit = 10, { env = process.env, fetchImpl = globalThis.fetch } = {}) {
  if (typeof query !== "string" || query.length < 1 || query.length > 300) throw new Error("query must be 1–300 characters");
  if (!Number.isInteger(limit) || limit < 1 || limit > 20) throw new Error("limit must be an integer from 1 to 20");
  const provider = selectLocalSearchProvider(env);
  if (!provider) throw new Error("Configure exactly one local provider key (BRAVE_SEARCH_API_KEY, TAVILY_API_KEY, or EXA_API_KEY); set APEX_SEARCH_PROVIDER when more than one is present.");
  const timeout = AbortSignal.timeout(15_000);
  let response;
  let body;
  if (provider === "brave") {
    const url = new URL("https://api.search.brave.com/res/v1/web/search");
    url.searchParams.set("q", query); url.searchParams.set("count", String(limit));
    response = await fetchImpl(url, { headers: { "X-Subscription-Token": env[providerKeys.brave], Accept: "application/json" }, signal: timeout, redirect: "error" });
    if (!response.ok) throw new Error(`Brave Search returned HTTP ${response.status}`);
    body = await response.json();
    return { provider, results: normalize(body.web?.results, "description", limit),
      note: "Search was sent directly to your selected provider; Apex did not receive your provider key. Pass a result url to scrape_page to read it." };
  }
  if (provider === "tavily") {
    response = await fetchImpl("https://api.tavily.com/search", { method: "POST", headers: { Authorization: `Bearer ${env[providerKeys.tavily]}`, "Content-Type": "application/json" },
      body: JSON.stringify({ query, topic: "general", search_depth: "basic", max_results: limit, include_answer: false, include_raw_content: false }), signal: timeout, redirect: "error" });
    if (!response.ok) throw new Error(`Tavily Search returned HTTP ${response.status}`);
    body = await response.json();
    return { provider, results: normalize(body.results, "content", limit),
      note: "Search was sent directly to your selected provider; Apex did not receive your provider key. Pass a result url to scrape_page to read it." };
  }
  response = await fetchImpl("https://api.exa.ai/search", { method: "POST", headers: { "x-api-key": env[providerKeys.exa], "Content-Type": "application/json" },
    body: JSON.stringify({ query, type: "auto", numResults: limit, contents: { highlights: true } }), signal: timeout, redirect: "error" });
  if (!response.ok) throw new Error(`Exa Search returned HTTP ${response.status}`);
  body = await response.json();
  return { provider, results: normalize(body.results, "highlights", limit),
    note: "Search was sent directly to your selected provider; Apex did not receive your provider key. Pass a result url to scrape_page to read it." };
}

const byokTool = {
  name: "web_search_byok",
  description: "Search directly through your provider key in this local MCP process. Requires BRAVE_SEARCH_API_KEY, TAVILY_API_KEY, or EXA_API_KEY in the process environment. The provider bills you directly and your key is never sent to Apex. Pass result URLs to scrape_page; scraping still uses Apex credits.",
  inputSchema: { type: "object", properties: { query: { type: "string", minLength: 1, maxLength: 300 }, limit: { type: "integer", minimum: 1, maximum: 20, default: 10 } }, required: ["query"], additionalProperties: false },
};

export async function startMcpBridge({
  apiKey = process.env.APEX_SCRAPER_API_KEY,
  remoteUrl = process.env.APEX_SCRAPER_MCP_URL || "https://webscraper.apexflowlabs.com/v1/mcp",
  stderr = process.stderr,
  byokSearch = (query, limit) => localByokSearch(query, limit),
} = {}) {
  if (!apiKey) throw new Error("Set APEX_SCRAPER_API_KEY before starting the Apex Flow MCP bridge.");

  const remote = new Client({ name: "apex-flow-web-scraper-stdio-bridge", version: "0.11.0" });
  const remoteTransport = new StreamableHTTPClientTransport(new URL(remoteUrl), {
    requestInit: { headers: { Authorization: `Bearer ${apiKey}` } },
  });
  const local = new Server(
    { name: "apex-flow-web-scraper", version: "0.11.0" },
    { capabilities: { tools: { listChanged: false } } },
  );

  await remote.connect(remoteTransport);
  local.setRequestHandler(ListToolsRequestSchema, async () => {
    const tools = await remote.listTools();
    return { ...tools, tools: [...tools.tools.filter((tool) => tool.name !== byokTool.name), byokTool] };
  });
  local.setRequestHandler(CallToolRequestSchema, async (request) => {
    if (request.params.name === byokTool.name) {
      const { query, limit = 10 } = request.params.arguments || {};
      try {
        const result = await byokSearch(query, limit);
        return { content: [{ type: "text", text: JSON.stringify(result) }] };
      } catch (error) {
        return { isError: true, content: [{ type: "text", text: error.message }] };
      }
    }
    return remote.callTool(request.params);
  });
  await local.connect(new StdioServerTransport());

  let closing;
  const close = () => {
    if (!closing) closing = Promise.allSettled([local.close(), remote.close()]);
    return closing;
  };
  const onSignal = () => { void close(); };
  process.once("SIGINT", onSignal);
  process.once("SIGTERM", onSignal);
  stderr.write("Apex Flow Web Scraper MCP bridge connected.\n");
  return { close };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  startMcpBridge().catch((error) => {
    process.stderr.write(`Apex Flow MCP bridge: ${error.message}\n`);
    process.exitCode = 1;
  });
}
