# Apex Flow Web Scraper for Node.js

Node.js 18+ clients for the hosted API, AI-client setup, and a self-hosted workspace.

## Add the hosted scraper to AI clients

After the npm package is published, run the interactive installer to add a local
stdio MCP bridge to detected clients. It supports Codex CLI, Claude Code,
Claude Desktop, Cursor, Gemini CLI, Windsurf, GitHub Copilot CLI, Cline,
Roo Code (workspace config), and Zed. Cline CLI can be selected with
`--client cline`; the Cline IDE extension also supports a direct remote setup
from its MCP Servers panel. Each installer target is individually selectable.

```bash
npx -y apex-flow-web-scraper setup
npx -y apex-flow-web-scraper setup --client cursor
npx -y apex-flow-web-scraper setup --client cline
npx -y apex-flow-web-scraper setup --client roo-code
npx -y apex-flow-web-scraper setup --client zed
npx -y apex-flow-web-scraper setup --all --dry-run
```

The installer backs up edited files, preserves existing JSONC settings, refuses
to overwrite a conflicting server unless `--force` is passed, and never writes
your API key into client settings. Set `APEX_SCRAPER_API_KEY` in the environment
inherited by the AI client, then restart it. The bridge connects to the hosted
Streamable HTTP MCP endpoint; scraping is still run by Apex Flow's hosted service.
`npm install apex-flow-web-scraper` is not available until the package release.

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
