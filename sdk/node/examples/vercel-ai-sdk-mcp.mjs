#!/usr/bin/env node
// Server-side Vercel AI SDK 7 + Apex Flow Web Scraper over Streamable HTTP MCP.
// See https://vercel.com/docs/mcp/integrations/ai-sdk for the current client pattern.
import { createMCPClient } from "@ai-sdk/mcp";
import { generateText, isStepCount } from "ai";

const apiKey = process.env.APEX_SCRAPER_API_KEY;
const model = process.env.AI_GATEWAY_MODEL;
if (!apiKey) throw new Error("Set APEX_SCRAPER_API_KEY in the server environment.");
if (!model) throw new Error("Set AI_GATEWAY_MODEL to a model enabled for your Vercel AI Gateway account.");

const mcpUrl = process.env.APEX_SCRAPER_MCP_URL ?? "https://webscraper.apexflowlabs.com/v1/mcp";
const prompt = process.argv.slice(2).join(" ") ||
  "Scrape https://example.com and summarize the page in one sentence.";
const client = await createMCPClient({
  transport: {
    type: "http",
    url: mcpUrl,
    headers: { Authorization: `Bearer ${apiKey}` },
  },
});

try {
  const tools = await client.tools();
  const result = await generateText({
    model,
    instructions: "Use the Apex Flow Web Scraper tools to read the requested public page. Cite its URL in your answer.",
    tools,
    prompt,
    stopWhen: isStepCount(5),
  });
  console.log(result.text);
} finally {
  await client.close();
}
