# Apex Flow Web Scraper for Node.js

Node.js 18+ clients for the hosted API, AI-client setup, and a self-hosted workspace.

## Add the hosted scraper to AI clients

After the npm package is published, run the interactive installer to add a local
stdio MCP bridge to detected clients. It supports Codex CLI, Claude Code,
Claude Desktop, Grok CLI, Cursor, Gemini CLI, Windsurf, GitHub Copilot CLI and VS Code,
Cline, Roo Code (workspace config), Continue (workspace config), OpenCode,
Amazon Q Developer (legacy MCP config), JetBrains Air, and Zed. Cline CLI can be selected with
`--client cline`; the Cline IDE extension also supports a direct remote setup
from its MCP Servers panel. Each installer target is individually selectable.

```bash
npx -y apex-flow-web-scraper setup
npx -y apex-flow-web-scraper setup --client cursor
npx -y apex-flow-web-scraper setup --client grok
npx -y apex-flow-web-scraper setup --client cline
npx -y apex-flow-web-scraper setup --client roo-code
npx -y apex-flow-web-scraper setup --client continue
npx -y apex-flow-web-scraper setup --client opencode
npx -y apex-flow-web-scraper setup --client amazon-q
npx -y apex-flow-web-scraper setup --client jetbrains-air
npx -y apex-flow-web-scraper setup --client zed
npx -y apex-flow-web-scraper setup --all --dry-run
```

Grok CLI connects to the hosted server directly over remote MCP; the remaining
stdio targets use the local bridge. The installer backs up edited files, preserves existing JSONC settings, refuses
to overwrite a conflicting server unless `--force` is passed, and never writes
your API key into client settings. Set `APEX_SCRAPER_API_KEY` in the environment
inherited by the AI client, then restart it. The bridge connects to the hosted
Streamable HTTP MCP endpoint; scraping is still run by Apex Flow's hosted service.
`npm install apex-flow-web-scraper` is not available until the package release.

The local bridge also provides `web_search_byok`. Set exactly one of
`BRAVE_SEARCH_API_KEY`, `TAVILY_API_KEY`, or `EXA_API_KEY` in the environment
that launches the AI client (or set `APEX_SEARCH_PROVIDER` when setting more
than one). This tool calls that provider directly from the local bridge; the
provider bills your account and the key is not sent to Apex. Use its result
URLs with `scrape_page`; those page reads still use Apex credits. This local
option is separate from hosted `web_search`, which uses Apex's configured
provider and credits.

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

// Public dynamic pages: bounded click/wait/scroll actions in one request.
const dynamic = await apex.scrape("https://example.com/catalog", {
  actions: [
    { type: "click", selector: "button.load-more" },
    { type: "wait", selector: "article.product" },
  ],
});
console.log(dynamic.result.markdown);

for await (const page of apex.crawl("https://www.sqlite.org/docs.html", 25)) {
  console.log(page.url, page.status);
}

// Recurring checks require explicit credit consent and an HTTPS webhook.
const monitor = await apex.createMonitor(
  "https://example.com/pricing",
  "https://your-service.example/webhooks/apex",
  86400,
  true,
);
console.log(monitor.estimated_max_credits_per_30_days);
// Save monitor.signing_secret securely; it is returned only at creation.
```

See the [hosted API guide](https://webscraper.apexflowlabs.com/docs.html#sdk) and [signed monitoring guide](https://webscraper.apexflowlabs.com/monitoring.html) for batches, change tracking, extraction, webhooks, and error handling.

### Reuse the official Firecrawl SDK

For migration tests, install Firecrawl's official SDK and point its `apiUrl` to Apex's API origin. The documented Firecrawl v2 adapter subset is listed in the [compatibility contract](https://webscraper.apexflowlabs.com/firecrawl-compat.html); unsupported options fail explicitly, and defaults that Apex cannot honor add a response warning.

```js
import { Firecrawl } from "firecrawl";

const scraper = new Firecrawl({
  apiKey: process.env.APEX_SCRAPER_API_KEY,
  apiUrl: "https://webscraper.apexflowlabs.com",
});

const result = await scraper.scrape("https://example.com", { formats: ["markdown"] });
console.log(result.markdown);
```

This bridge is a tested migration path, not a claim of full feature equivalence. The SDK's official configuration is documented in the [Firecrawl Node SDK](https://github.com/firecrawl/firecrawl/tree/main/apps/js-sdk/firecrawl); the Apex package's own clients remain available when you want Apex-specific methods.

## Self-hosted workspace

Run `rainbo-scrape web` on your machine, then import the local workspace client:

```js
import { Scraper } from "apex-flow-web-scraper";

const scraper = new Scraper();
const page = await scraper.scrape("https://example.com", { render: "never" });
console.log(page.markdown);
```

The hosted client requires an API key. The local client sends jobs to your own workspace at `http://127.0.0.1:8840` by default.
