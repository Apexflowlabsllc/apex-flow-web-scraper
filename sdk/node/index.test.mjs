import test from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import { Scraper, ScrapeError } from "./index.mjs";

function server(handler) {
  return new Promise((resolve) => {
    const s = http.createServer(handler).listen(0, "127.0.0.1", () => resolve(s));
  });
}
const send = (res, code, obj) => { res.writeHead(code, { "content-type": "application/json" }); res.end(JSON.stringify(obj)); };

test("submits a job, polls until done, returns the first result", async () => {
  let polls = 0, posted;
  const s = await server((req, res) => {
    if (req.method === "POST") { let b = ""; req.on("data", (c) => (b += c)); req.on("end", () => { posted = JSON.parse(b); send(res, 200, { id: "j1" }); }); return; }
    if (req.url.endsWith("/results")) return send(res, 200, [{ result: { markdown: "# Hi" } }]);
    send(res, 200, { state: ++polls < 2 ? "running" : "done" });
  });
  const out = await new Scraper({ baseUrl: `http://127.0.0.1:${s.address().port}`, pollMs: 5 }).scrape("https://example.com", { render: "never" });
  s.close();
  assert.equal(out.markdown, "# Hi");
  assert.deepEqual(posted.urls, ["https://example.com"]);
  assert.equal(posted.render, "never");
});

test("surfaces server errors and failed pages", async () => {
  const s = await server((req, res) => (req.method === "POST" ? send(res, 400, { error: "bad url" }) : send(res, 200, {})));
  const c = new Scraper({ baseUrl: `http://127.0.0.1:${s.address().port}` });
  await assert.rejects(c.scrape("x"), (e) => e instanceof ScrapeError && e.message === "bad url");
  s.close();
  const s2 = await server((req, res) => {
    if (req.method === "POST") return send(res, 200, { id: "j" });
    if (req.url.endsWith("/results")) return send(res, 200, [{ result: null, error: { message: "blocked by robots.txt" } }]);
    send(res, 200, { state: "done" });
  });
  await assert.rejects(new Scraper({ baseUrl: `http://127.0.0.1:${s2.address().port}`, pollMs: 5 }).scrape("y"), /blocked by robots/);
  s2.close();
});
