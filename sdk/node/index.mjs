// Client for the local workspace: run `rainbo-scrape web` first (default http://127.0.0.1:8840).
export class ScrapeError extends Error {}

export class Scraper {
  constructor({ baseUrl = "http://127.0.0.1:8840", pollMs = 300, timeoutMs = 120000 } = {}) {
    this.base = baseUrl.replace(/\/$/, "");
    this.pollMs = pollMs;
    this.timeoutMs = timeoutMs;
  }

  async #call(path, body) {
    const res = await fetch(this.base + "/api/" + path, body === undefined ? {} : {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new ScrapeError(data.error || `HTTP ${res.status}`);
    return data;
  }

  async scrape(url, options = {}) {
    const job = await this.#call("jobs", { urls: [url], max_pages: 1, max_depth: 0, render: "auto", ...options });
    const deadline = Date.now() + this.timeoutMs;
    for (;;) {
      const status = await this.#call(`jobs/${job.id}`);
      if (!["queued", "running"].includes(status.state)) break;
      if (Date.now() > deadline) throw new ScrapeError("timed out waiting for the job");
      await new Promise((r) => setTimeout(r, this.pollMs));
    }
    const [row] = await this.#call(`jobs/${job.id}/results`);
    if (!row || row.error || !row.result) throw new ScrapeError(row?.error?.message || "no result for " + url);
    return row.result; // { markdown, text, title, links, ... }
  }
}

export const scrape = (url, options, client) => new Scraper(client).scrape(url, options);
