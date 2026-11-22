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
    const normalizedItem = { url: item.url, title: item.title || "", snippet };
    const date = item.publishedDate || item.published_date || item.date;
    if (typeof date === "string") normalizedItem.date = date;
    normalized.push(normalizedItem);
    if (normalized.length >= limit) break;
  }
  return normalized;
}

function normalizeDomainFilters(includeDomains, excludeDomains) {
  if (includeDomains !== undefined && excludeDomains !== undefined) throw new Error("includeDomains and excludeDomains cannot be used together");
  const normalize = (domains, name) => {
    if (domains === undefined) return undefined;
    if (!Array.isArray(domains) || domains.length < 1 || domains.length > 50) throw new Error(`${name} must be a list of 1–50 hostnames`);
    return [...new Set(domains.map((domain) => {
      if (typeof domain !== "string" || domain !== domain.trim() || !domain || /[\/:?#@]/.test(domain)) {
        throw new Error(`${name} entries must be hostnames without protocol or path`);
      }
      let parsed;
      try { parsed = new URL(`https://${domain}`); } catch { throw new Error(`${name} contains an invalid hostname`); }
      if (parsed.port || parsed.username || parsed.password || parsed.pathname !== "/" || parsed.search || parsed.hash) {
        throw new Error(`${name} entries must be hostnames without protocol or path`);
      }
      return parsed.hostname.toLowerCase().replace(/\.$/, "");
    }))];
  };
  return { includeDomains: normalize(includeDomains, "includeDomains"), excludeDomains: normalize(excludeDomains, "excludeDomains") };
}

function matchesDomain(item, includeDomains, excludeDomains) {
  let hostname;
  try { hostname = new URL(item.url).hostname.toLowerCase().replace(/\.$/, ""); } catch { return false; }
  if (includeDomains && !includeDomains.some((domain) => hostname === domain || hostname.endsWith(`.${domain}`))) return false;
  if (excludeDomains && excludeDomains.some((domain) => hostname === domain || hostname.endsWith(`.${domain}`))) return false;
  return true;
}

export async function localByokSearch(query, limit = 10, { env = process.env, fetchImpl = globalThis.fetch } = {}, filters = {}) {
  if (typeof query !== "string" || query.length < 1 || query.length > 300) throw new Error("query must be 1–300 characters");
  if (!Number.isInteger(limit) || limit < 1 || limit > 20) throw new Error("limit must be an integer from 1 to 20");
  const { includeDomains, excludeDomains } = normalizeDomainFilters(filters.includeDomains, filters.excludeDomains);
  const topic = filters.topic ?? "general";
  if (!["general", "news", "images"].includes(topic)) throw new Error("topic must be 'general', 'news', or 'images'");
  const provider = selectLocalSearchProvider(env);
  if (!provider) throw new Error("Configure exactly one local provider key (BRAVE_SEARCH_API_KEY, TAVILY_API_KEY, or EXA_API_KEY); set APEX_SEARCH_PROVIDER when more than one is present.");
  if (topic === "news" && provider === "brave") throw new Error("The configured Brave provider does not expose a dedicated news source; use Tavily or Exa");
  if (topic === "images" && provider !== "brave") throw new Error("Image search requires the configured Brave provider's dedicated image index");
  const timeout = AbortSignal.timeout(15_000);
  let response;
  let body;
  if (provider === "brave") {
    if (topic === "images") {
      const url = new URL("https://api.search.brave.com/res/v1/images/search");
      const scopedQuery = includeDomains ? `${query} (${includeDomains.map((domain) => `site:${domain}`).join(" OR ")})` : query;
      url.searchParams.set("q", excludeDomains ? `${scopedQuery} ${excludeDomains.map((domain) => `-site:${domain}`).join(" ")}` : scopedQuery);
      url.searchParams.set("count", String(limit));
      url.searchParams.set("safesearch", "strict");
      const response = await fetchImpl(url, { headers: { "X-Subscription-Token": env[providerKeys.brave], Accept: "application/json" }, signal: timeout, redirect: "error" });
      if (!response.ok) throw new Error(`Brave Image Search returned HTTP ${response.status}`);
      const body = await response.json();
      const results = (body.results ?? []).flatMap((item) => {
        const imageUrl = item.properties?.url ?? item.thumbnail?.src;
        const pageUrl = item.url;
        try {
          const image = new URL(imageUrl), page = new URL(pageUrl);
          if (!["http:", "https:"].includes(image.protocol) || !["http:", "https:"].includes(page.protocol)) return [];
          if (!matchesDomain({ url: pageUrl }, includeDomains, excludeDomains)) return [];
          return [{ title: item.title ?? "", imageUrl, imageWidth: item.properties?.width ?? item.thumbnail?.width ?? null,
            imageHeight: item.properties?.height ?? item.thumbnail?.height ?? null, url: pageUrl }];
        } catch { return []; }
      }).slice(0, limit);
      return { provider, results, note: "Search was sent directly to your selected provider; Apex did not receive your provider key. Pass a result url to scrape_page to read it." };
    }
    const url = new URL("https://api.search.brave.com/res/v1/web/search");
    const scopedQuery = includeDomains ? `${query} (${includeDomains.map((domain) => `site:${domain}`).join(" OR ")})` : query;
    url.searchParams.set("q", excludeDomains ? `${scopedQuery} ${excludeDomains.map((domain) => `-site:${domain}`).join(" ")}` : scopedQuery);
    url.searchParams.set("count", String(limit));
    response = await fetchImpl(url, { headers: { "X-Subscription-Token": env[providerKeys.brave], Accept: "application/json" }, signal: timeout, redirect: "error" });
    if (!response.ok) throw new Error(`Brave Search returned HTTP ${response.status}`);
    body = await response.json();
    return { provider, results: normalize(body.web?.results, "description", limit).filter((item) => matchesDomain(item, includeDomains, excludeDomains)).slice(0, limit),
      note: "Search was sent directly to your selected provider; Apex did not receive your provider key. Pass a result url to scrape_page to read it." };
  }
  if (provider === "tavily") {
    response = await fetchImpl("https://api.tavily.com/search", { method: "POST", headers: { Authorization: `Bearer ${env[providerKeys.tavily]}`, "Content-Type": "application/json" },
      body: JSON.stringify({ query, topic, search_depth: "basic", max_results: limit, include_answer: false, include_raw_content: false,
        ...(includeDomains ? { include_domains: includeDomains } : {}), ...(excludeDomains ? { exclude_domains: excludeDomains } : {}) }), signal: timeout, redirect: "error" });
    if (!response.ok) throw new Error(`Tavily Search returned HTTP ${response.status}`);
    body = await response.json();
    return { provider, results: normalize(body.results, "content", limit).filter((item) => matchesDomain(item, includeDomains, excludeDomains)).slice(0, limit),
      note: "Search was sent directly to your selected provider; Apex did not receive your provider key. Pass a result url to scrape_page to read it." };
  }
  response = await fetchImpl("https://api.exa.ai/search", { method: "POST", headers: { "x-api-key": env[providerKeys.exa], "Content-Type": "application/json" },
    body: JSON.stringify({ query, type: "auto", numResults: limit, contents: { highlights: true }, ...(topic === "news" ? { category: "news" } : {}),
      ...(includeDomains ? { includeDomains } : {}), ...(excludeDomains ? { excludeDomains } : {}) }), signal: timeout, redirect: "error" });
  if (!response.ok) throw new Error(`Exa Search returned HTTP ${response.status}`);
  body = await response.json();
  return { provider, results: normalize(body.results, "highlights", limit).filter((item) => matchesDomain(item, includeDomains, excludeDomains)).slice(0, limit),
    note: "Search was sent directly to your selected provider; Apex did not receive your provider key. Pass a result url to scrape_page to read it." };
}

const byokTool = {
  name: "web_search_byok",
  description: "Search directly through your provider key in this local MCP process, optionally restricting or excluding hostname sets. Requires BRAVE_SEARCH_API_KEY, TAVILY_API_KEY, or EXA_API_KEY in the process environment. The provider bills you directly and your key is never sent to Apex. Pass result URLs to scrape_page; scraping still uses Apex credits.",
  inputSchema: { type: "object", properties: { query: { type: "string", minLength: 1, maxLength: 300 }, limit: { type: "integer", minimum: 1, maximum: 20, default: 10 }, topic: { type: "string", enum: ["general", "news", "images"], default: "general" },
    includeDomains: { type: "array", minItems: 1, maxItems: 50, items: { type: "string", format: "hostname" } },
    excludeDomains: { type: "array", minItems: 1, maxItems: 50, items: { type: "string", format: "hostname" } } }, required: ["query"], additionalProperties: false },
};

export async function startMcpBridge({
  apiKey = process.env.APEX_SCRAPER_API_KEY,
  remoteUrl = process.env.APEX_SCRAPER_MCP_URL || "https://webscraper.apexflowlabs.com/v1/mcp",
  stderr = process.stderr,
  byokSearch = (query, limit, filters) => localByokSearch(query, limit, undefined, filters),
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
      const { query, limit = 10, includeDomains, excludeDomains, topic = "general" } = request.params.arguments || {};
      try {
        const result = await byokSearch(query, limit, { includeDomains, excludeDomains, topic });
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
