import test from "node:test";
import assert from "node:assert/strict";
import { Apex, ApexError } from "./hosted.mjs";

function fake(replies) {
  const calls = [];
  const fetchImpl = async (url, init) => {
    calls.push({ url, method: init.method, body: init.body ? JSON.parse(init.body) : undefined, auth: init.headers.Authorization });
    const { status = 200, data, headers = {} } = replies.shift();
    return { ok: status < 400, status, json: async () => data, headers: { get: (n) => headers[n] ?? null } };
  };
  return { calls, apex: new Apex({ apiKey: "rs_live_fixture", fetchImpl }) };
}

test("scrapeAll joins every part of a long page", async () => {
  const { apex, calls } = fake([
    { data: { result: { markdown: "one\n" }, pagination: { page: 1, pages: 2, next_page: 2 } } },
    { data: { result: { markdown: "two\n" }, pagination: { page: 2, pages: 2, next_page: null } } },
  ]);
  const result = await apex.scrapeAll("https://example.com", { outputs: ["markdown"] });
  assert.equal(result.markdown, "one\ntwo\n");
  assert.deepEqual(calls.map((c) => c.body.page), [undefined, 2]);
  assert.equal(calls[0].auth, "Bearer rs_live_fixture");
});

test("errors expose the code, buy link and hint", async () => {
  const { apex } = fake([{ status: 402, data: { error: "needs 1", code: "insufficient_credits", retryable: false, buy_url: "https://webscraper.apexflowlabs.com/#pricing" } }]);
  await assert.rejects(apex.scrape("https://example.com"), (e) => e instanceof ApexError && e.code === "insufficient_credits" && e.buy_url.endsWith("#pricing"));
});

test("crawl yields pages until the job reports no more", async () => {
  const { apex, calls } = fake([
    { data: { id: "crawl_1", status: "running" } },
    { data: { status: "running", results: [{ url: "a" }], next_offset: 1 } },
    { data: { status: "completed", results: [{ url: "b" }], next_offset: null } },
  ]);
  const pages = [];
  for await (const page of apex.crawl("https://example.com", 2, {}, 1)) pages.push(page.url);
  assert.deepEqual(pages, ["a", "b"]);
  assert.equal(calls[2].url.endsWith("/crawl/crawl_1?offset=1"), true);
});

test("data packages post to their endpoints", async () => {
  const { apex, calls } = fake([
    { data: { credits_used: 1, result: { region: "Sacramento-Roseville-Folsom, CA", latest: { period: "2026-Q2", index: 363.31 } } } },
    { data: { credits_used: 1, result: { topic: "business", count: 1, stories: [{ title: "t", url: "https://www.npr.org/a" }] } } },
  ]);
  const market = await apex.housePrices("Sacramento, CA", 12);
  const news = await apex.nprNews("business", 1);
  assert.equal(market.result.latest.index, 363.31);
  assert.equal(news.result.count, 1);
  assert.deepEqual(calls.map((c) => [c.method, c.url.replace(/^.*\/v1/, ""), c.body]), [
    ["POST", "/packages/housing", { region: "Sacramento, CA", quarters: 12 }],
    ["POST", "/packages/npr", { topic: "business", limit: 1 }],
  ]);
});
