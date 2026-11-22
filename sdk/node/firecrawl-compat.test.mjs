import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { Firecrawl } from "firecrawl";

test("official Firecrawl SDK can target the Apex v2 compatibility API", async () => {
  const requests = [];
  const server = createServer(async (req, res) => {
    let raw = "";
    for await (const part of req) raw += part;
    const body = raw ? JSON.parse(raw) : undefined;
    requests.push({ method: req.method, url: req.url, authorization: req.headers.authorization, body });
    const json = (value) => {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify(value));
    };
    if (req.url === "/v2/scrape") return json({ success: true, data: { markdown: "# Apex", metadata: { sourceURL: body.url } } });
    if (req.url === "/v2/map") return json({ success: true, id: "map_fixture", links: [{ url: "https://example.com/docs" }] });
    if (req.url === "/v2/search") return json({ success: true, data: { web: [{ url: "https://example.com/result", title: "Result", description: "Found" }] } });
    if (req.url === "/v2/batch/scrape") return json({ success: true, id: "batch_fixture", url: "/v2/batch/scrape/batch_fixture" });
    if (req.url === "/v2/batch/scrape/batch_fixture") return json({
      success: true, status: "completed", total: 1, completed: 1, creditsUsed: 1, next: null,
      data: [{ markdown: "# Batched", metadata: { sourceURL: "https://example.com/batch" } }],
    });
    res.writeHead(404, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ success: false, error: `Unexpected ${req.method} ${req.url}` }));
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  const app = new Firecrawl({ apiKey: "apex-fixture-key", apiUrl: `http://127.0.0.1:${address.port}`, maxRetries: 1 });
  try {
    const scraped = await app.scrape("https://example.com");
    assert.equal(scraped.markdown, "# Apex");

    const mapped = await app.map("https://example.com");
    assert.equal(mapped.links[0].url, "https://example.com/docs");

    const searched = await app.search("example query", { limit: 1 });
    assert.equal(searched.web[0].url, "https://example.com/result");

    const started = await app.startBatchScrape(["https://example.com/batch"], { formats: ["markdown"] });
    assert.equal(started.id, "batch_fixture");
    const batch = await app.getBatchScrapeStatus(started.id);
    assert.equal(batch.status, "completed");
    assert.equal(batch.data[0].markdown, "# Batched");

    assert.deepEqual(requests.map(({ method, url }) => `${method} ${url}`), [
      "POST /v2/scrape", "POST /v2/map", "POST /v2/search", "POST /v2/batch/scrape", "GET /v2/batch/scrape/batch_fixture",
    ]);
    assert.ok(requests.every((request) => request.authorization === "Bearer apex-fixture-key"));
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
});
