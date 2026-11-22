import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { createMCPClient } from "@ai-sdk/mcp";
import { generateText, isStepCount } from "ai";
import { MockLanguageModelV4 } from "ai/test";
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { CallToolRequestSchema, ListToolsRequestSchema } from "@modelcontextprotocol/sdk/types.js";

test("Vercel AI SDK discovers and calls Apex MCP tools over authenticated HTTP", async () => {
  const requests = [];
  const http = createServer(async (req, res) => {
    let raw = "";
    for await (const part of req) raw += part;
    const body = raw ? JSON.parse(raw) : undefined;
    requests.push({ method: body?.method, authorization: req.headers.authorization });

    const remote = new Server({ name: "apex-fixture", version: "1.0.0" }, { capabilities: { tools: {} } });
    remote.setRequestHandler(ListToolsRequestSchema, async () => ({
      tools: [{
        name: "scrape_page",
        description: "Read a public web page and return its Markdown.",
        inputSchema: { type: "object", properties: { url: { type: "string" } }, required: ["url"] },
      }],
    }));
    remote.setRequestHandler(CallToolRequestSchema, async (request) => ({
      content: [{ type: "text", text: `# Example\nSource: ${request.params.arguments.url}` }],
    }));
    const transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined });
    await remote.connect(transport);
    await transport.handleRequest(req, res, body);
    res.on("close", () => { void transport.close(); void remote.close(); });
  });
  await new Promise((resolve) => http.listen(0, "127.0.0.1", resolve));
  const address = http.address();
  const apiKey = "apex-fixture-key";
  const client = await createMCPClient({
    transport: {
      type: "http",
      url: `http://127.0.0.1:${address.port}/v1/mcp`,
      headers: { Authorization: `Bearer ${apiKey}` },
    },
  });
  let modelStep = 0;
  const model = new MockLanguageModelV4({
    doGenerate: async () => {
      modelStep += 1;
      return {
        content: modelStep === 1
          ? [{ type: "tool-call", toolCallId: "call_scrape", toolName: "scrape_page", input: JSON.stringify({ url: "https://example.com" }) }]
          : [{ type: "text", text: "The page is an example page." }],
        finishReason: { unified: modelStep === 1 ? "tool-calls" : "stop", raw: modelStep === 1 ? "tool_calls" : "stop" },
        usage: {
          inputTokens: { total: 0, noCache: 0, cacheRead: 0, cacheWrite: 0 },
          outputTokens: { total: 0, text: 0, reasoning: 0 },
        },
        warnings: [],
      };
    },
  });

  try {
    const result = await generateText({
      model,
      tools: await client.tools(),
      prompt: "Read example.com.",
      stopWhen: isStepCount(3),
    });
    assert.equal(result.text, "The page is an example page.");
    assert.equal(modelStep, 2);
    assert.ok(requests.some((request) => request.method === "tools/call"));
    assert.ok(requests.every((request) => request.authorization === `Bearer ${apiKey}`));
  } finally {
    await client.close();
    await new Promise((resolve) => http.close(resolve));
  }
});
