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

test("hosted web search forwards optional hostname filters", async () => {
  const { apex, calls } = fake([{ data: { results: [] } }]);
  await apex.webSearch("official docs", 7, { includeDomains: ["docs.example.com"] });
  assert.deepEqual(calls[0].body, { query: "official docs", limit: 7, include_domains: ["docs.example.com"] });
});

test("hosted web search can request dedicated news results", async () => {
  const { apex, calls } = fake([{ data: { results: [] } }]);
  await apex.webSearch("market update", 4, { topic: "news" });
  assert.deepEqual(calls[0].body, { query: "market update", limit: 4, topic: "news" });
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

test("store watch posts to its endpoints", async () => {
  const { apex, calls } = fake([
    { data: { credits_used: 1, result: { store: "https://shop.example.com", products: 20, first_snapshot: true } } },
    { data: { credits_used: 0, result: { store: "https://shop.example.com", products: [] } } },
    { data: { stores: [] } },
    { data: { store: "https://shop.example.com", forgotten: true } },
  ]);
  assert.equal((await apex.trackStore("shop.example.com")).result.products, 20);
  await apex.risingProducts("shop.example.com", 14, 5);
  await apex.trackedStores();
  assert.equal((await apex.forgetStore("shop.example.com")).forgotten, true);
  assert.deepEqual(calls.map((c) => [c.method, c.url.replace(/^.*\/v1/, ""), c.body]), [
    ["POST", "/stores/track", { store: "shop.example.com" }],
    ["POST", "/stores/rising", { store: "shop.example.com", days: 14, limit: 5 }],
    ["GET", "/stores", undefined],
    ["POST", "/stores/forget", { store: "shop.example.com" }],
  ]);
});

test("scheduled monitors use the account-scoped REST endpoints", async () => {
  const { apex, calls } = fake([
    { data: { monitor: { id: "mon_1" }, signing_secret: "whsec_fixture" } },
    { data: { monitors: [{ id: "mon_1" }] } },
    { data: { monitor: { id: "mon_1", state: "paused" } } },
    { data: { monitor: { id: "mon_1", state: "active" } } },
    { data: { events: [] } },
    { data: { deleted: "mon_1" } },
  ]);
  const created = await apex.createMonitor("https://example.com", "https://hooks.example.com", 3600, true);
  assert.equal(created.signing_secret, "whsec_fixture");
  await apex.monitors();
  await apex.pauseMonitor("mon_1");
  await apex.resumeMonitor("mon_1");
  await apex.monitorEvents("mon_1", 5);
  await apex.deleteMonitor("mon_1");
  assert.deepEqual(calls.map((c) => [c.method, c.url.replace(/^.*\/v1/, ""), c.body]), [
    ["POST", "/monitors/create", { url: "https://example.com", webhook_url: "https://hooks.example.com", interval_seconds: 3600, confirm_recurring: true }],
    ["GET", "/monitors/list", undefined],
    ["POST", "/monitors/pause", { id: "mon_1" }],
    ["POST", "/monitors/resume", { id: "mon_1" }],
    ["POST", "/monitors/events", { id: "mon_1", limit: 5 }],
    ["POST", "/monitors/delete", { id: "mon_1" }],
  ]);
});
