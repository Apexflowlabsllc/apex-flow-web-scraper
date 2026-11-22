// Client for the hosted Apex Flow Web Scraper API. No dependencies; Node 22+.
//   import { Apex } from "./hosted.mjs";
//   const apex = new Apex();                       // reads APEX_SCRAPER_API_KEY
//   const { result } = await apex.scrape("https://example.com", { outputs: ["markdown"] });
export class ApexError extends Error {
  constructor(status, body) {
    super(body.error || `HTTP ${status}`);
    this.status = status;
    this.body = body;
    for (const name of ["code", "retryable", "retry_after", "buy_url", "hint", "credits_left"]) this[name] = body[name];
  }
}

export class Apex {
  constructor({ apiKey = process.env.APEX_SCRAPER_API_KEY, baseUrl = "https://webscraper.apexflowlabs.com/v1", retries = 2, fetchImpl = fetch } = {}) {
    if (!apiKey) throw new Error("Pass apiKey or set APEX_SCRAPER_API_KEY. Get a key at https://webscraper.apexflowlabs.com/trial.html");
    this.key = apiKey;
    this.base = baseUrl.replace(/\/$/, "");
    this.retries = retries;
    this.fetch = fetchImpl;
  }

  async #call(method, path, body) {
    for (let attempt = 0; ; attempt++) {
      const res = await this.fetch(this.base + path, {
        method,
        headers: { Authorization: "Bearer " + this.key, "Content-Type": "application/json", "User-Agent": "apex-scraper-node/0.11.0" },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok) return data;
      const error = new ApexError(res.status, data);
      if (res.status === 429 && attempt < this.retries) {
        // The API says how long to wait.
        await new Promise((r) => setTimeout(r, Math.min(30, error.retry_after || Number(res.headers.get("retry-after")) || 5) * 1000));
        continue;
      }
      throw error;
    }
  }

  // One page. Options: render, outputs, recipe, chunks, max_tokens, page, fallback: "archive", max_age, extract, wait_selector, scrolls (0–10), actions (up to 8 click/wait/scroll steps; single request, no retained login state).
  scrape(url, options = {}) { return this.#call("POST", "/scrape", { url, ...options }); }

  // Every part of a long page, joined. Follows pagination for you.
  async scrapeAll(url, options = {}) {
    const first = await this.scrape(url, options);
    const result = first.result;
    let page = first.pagination?.next_page;
    while (page) {
      const more = await this.scrape(url, { ...options, page });
      for (const field of ["markdown", "text"]) if (field in result && field in more.result) result[field] += more.result[field];
      page = more.pagination.next_page;
    }
    return result;
  }

  batch(urls, options = {}) { return this.#call("POST", "/batch", { urls, ...options }); }
  // What changed since this account last read each URL, without page bodies.
  changes(urls, render = "never") { return this.batch(urls, { render, outputs: ["diff"] }); }
  createMonitor(url, webhookUrl, intervalSeconds = 86400, confirmRecurring = false) {
    return this.#call("POST", "/monitors/create", { url, webhook_url: webhookUrl, interval_seconds: intervalSeconds, confirm_recurring: confirmRecurring });
  }
  monitors() { return this.#call("GET", "/monitors/list"); }
  pauseMonitor(id) { return this.#call("POST", "/monitors/pause", { id }); }
  resumeMonitor(id) { return this.#call("POST", "/monitors/resume", { id }); }
  deleteMonitor(id) { return this.#call("POST", "/monitors/delete", { id }); }
  monitorEvents(id, limit = 20) { return this.#call("POST", "/monitors/events", { id, limit }); }
  map(url, limit = 1000, search) { return this.#call("POST", "/map", search ? { url, limit, search } : { url, limit }); }
  search(query, limit = 10) { return this.#call("POST", "/search", { query, limit }); }
  webSearch(query, limit = 10, { includeDomains, excludeDomains } = {}) {
    return this.#call("POST", "/websearch", { query, limit,
      ...(includeDomains === undefined ? {} : { include_domains: includeDomains }),
      ...(excludeDomains === undefined ? {} : { exclude_domains: excludeDomains }) });
  }
  usage() { return this.#call("GET", "/usage"); }
  // Data packages: answers from a publisher's own public data. 1 credit an answer.
  packages() { return this.#call("GET", "/packages"); }
  housePrices(region, quarters = 8) { return this.#call("POST", "/packages/housing", { region, quarters }); }
  nprNews(topic = "news", limit = 10) { return this.#call("POST", "/packages/npr", { topic, limit }); }
  // Store watch: snapshot a Shopify store's public catalog and rank what changed. 1 credit per 250 products; ranking is free.
  trackStore(store) { return this.#call("POST", "/stores/track", { store }); }
  risingProducts(store, days = 7, limit = 20) { return this.#call("POST", "/stores/rising", { store, days, limit }); }
  trackedStores() { return this.#call("GET", "/stores"); }
  forgetStore(store) { return this.#call("POST", "/stores/forget", { store }); }
  startCrawl(url, limit = 25, options = {}) { return this.#call("POST", "/crawl", { url, limit, ...options }); }
  crawlStatus(id, offset = 0) { return this.#call("GET", `/crawl/${id}?offset=${Number(offset) || 0}`); }
  cancelCrawl(id) { return this.#call("DELETE", `/crawl/${id}`); }

  // Crawl one site and yield each page as it becomes available.
  async *crawl(url, limit = 25, options = {}, pollMs = 2000) {
    const job = await this.startCrawl(url, limit, options);
    let offset = 0;
    while (offset !== null) {
      const status = await this.crawlStatus(job.id, offset);
      yield* status.results;
      if (status.next_offset === offset && status.status === "running") await new Promise((r) => setTimeout(r, pollMs));
      offset = status.next_offset;
    }
  }
}
