// Run the hosted remote MCP server as a local stdio server. This lets MCP
// clients that only support stdio use the same hosted tools without storing
// the API key in a client configuration file.
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { CallToolRequestSchema, ListToolsRequestSchema } from "@modelcontextprotocol/sdk/types.js";
import { pathToFileURL } from "node:url";

export async function startMcpBridge({
  apiKey = process.env.APEX_SCRAPER_API_KEY,
  remoteUrl = process.env.APEX_SCRAPER_MCP_URL || "https://webscraper.apexflowlabs.com/v1/mcp",
  stderr = process.stderr,
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
  local.setRequestHandler(ListToolsRequestSchema, async () => remote.listTools());
  local.setRequestHandler(CallToolRequestSchema, async (request) => remote.callTool(request.params));
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
