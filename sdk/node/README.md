# Apex Flow Web Scraper for Node.js

Dependency-free Node.js 18+ clients for the hosted API and a self-hosted workspace.

```bash
npm install apex-flow-web-scraper
```

## Hosted API

Create a free key at [webscraper.apexflowlabs.com/trial.html](https://webscraper.apexflowlabs.com/trial.html), then set `APEX_SCRAPER_API_KEY` in your server environment.

```js
import { Apex } from "apex-flow-web-scraper/hosted";

const apex = new Apex();
const { result } = await apex.scrape("https://example.com", { outputs: ["markdown"] });
console.log(result.markdown);

for await (const page of apex.crawl("https://www.sqlite.org/docs.html", 25)) {
  console.log(page.url, page.status);
}
```

See the [hosted API guide](https://webscraper.apexflowlabs.com/docs.html#sdk) for batches, change tracking, extraction, and error handling.

## Self-hosted workspace

Run `rainbo-scrape web` on your machine, then import the local workspace client:

```js
import { Scraper } from "apex-flow-web-scraper";

const scraper = new Scraper();
const page = await scraper.scrape("https://example.com", { render: "never" });
console.log(page.markdown);
```

The hosted client requires an API key. The local client sends jobs to your own workspace at `http://127.0.0.1:8840` by default.
