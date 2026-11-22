# Apex Flow Web Scraper for Node.js

Node.js 22+ clients for the hosted API, AI-client setup, and a self-hosted workspace.

## Add the hosted scraper to AI clients

After the npm package is published, run the interactive installer to add a local
stdio MCP bridge to detected clients. It supports Codex CLI, Claude Code,
Claude Desktop, Grok CLI, Cursor, Gemini CLI, Windsurf, GitHub Copilot CLI and VS Code,
Cline, Roo Code (workspace config), Continue (workspace config), OpenCode,
Amazon Q Developer (legacy MCP config), JetBrains Air, Zed, and Kiro IDE/CLI. Cline CLI can be selected with
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
npx -y apex-flow-web-scraper setup --client kiro
npx -y apex-flow-web-scraper setup --all --dry-run
npx -y apex-flow-web-scraper doctor
```

Grok CLI connects to the hosted server directly over remote MCP; the remaining
stdio targets use the local bridge. The installer backs up edited files, preserves existing JSONC settings, refuses
to overwrite a conflicting server unless `--force` is passed, and never writes
your API key into client settings. Set `APEX_SCRAPER_API_KEY` in the environment
inherited by the AI client, then restart it. The bridge connects to the hosted
Streamable HTTP MCP endpoint; scraping is still run by Apex Flow's hosted service.
`npm install apex-flow-web-scraper` is not available until the package release.

### Install from the public source repository

The source installer currently lives on draft PR #34. To try it before that
PR is merged, clone its branch below; after merge, use the default branch.

You can use the prepared installer before the npm release by running it from a
local clone. Configured AI clients launch the bridge from that checkout, so
keep the checkout at the same path. The installer backs up settings, preserves
unrelated servers, prompts before writing, and stores only environment-variable
references for credentials.

```bash
APEX_FLOW_SCRAPER_CHECKOUT=/path/to/apex-flow-web-scraper
git clone --branch codex/handoff-2026-11-21-scraper --single-branch https://github.com/Apexflowlabsllc/apex-flow-web-scraper.git "$APEX_FLOW_SCRAPER_CHECKOUT"
npm ci --prefix "$APEX_FLOW_SCRAPER_CHECKOUT/sdk/node"
node "$APEX_FLOW_SCRAPER_CHECKOUT/sdk/node/bin/apex-flow-web-scraper.mjs" setup --source --client claude-code
```

Use `setup --source` without `--client` to detect supported clients, or add
`--all` to configure every listed target. Set `APEX_SCRAPER_API_KEY` in each
AI client's environment before launching it. For direct web search, also set
exactly one of `BRAVE_SEARCH_API_KEY`, `TAVILY_API_KEY`, or `EXA_API_KEY` in
the environment the bridge receives. If multiple provider keys are present,
set `APEX_SEARCH_PROVIDER=brave|tavily|exa`. Grok and Kiro can also run the
source bridge over local stdio; Kiro requires approval for each environment
variable reference used by the server. For Roo Code or Continue workspace
settings, run the installer command from the target project directory so it
writes config there. The provider key stays on the local machine and search is
billed by that provider. Scraping result pages uses Apex credits.

Kiro uses its native remote MCP endpoint. To let Kiro expand `${APEX_SCRAPER_API_KEY}`
from your environment, approve that variable in Kiro's **Mcp Approved Env Vars**
setting; Kiro may show a warning until it is approved. See the
[Kiro MCP configuration guide](https://kiro.dev/docs/mcp/configuration/).

`doctor` checks the key in the current shell, authenticates to the MCP endpoint,
and confirms that `scrape_page` is available. It only lists tools: it does not
scrape a page or spend credits. Run it in the same environment where the API key
is available; an AI client launched with a different environment may still need
its own key setup.

### Use the Vercel AI SDK

The repository includes a server-side integration using Vercel's official
`@ai-sdk/mcp` client. It discovers the hosted Apex tools over Streamable HTTP,
lets AI SDK's `generateText` select and call them, and closes the MCP client
afterward. The example is tested against a local MCP fixture without an AI
provider key or a production request.

```bash
cd sdk/node
npm install
export APEX_SCRAPER_API_KEY="your-apex-key"
export AI_GATEWAY_MODEL="provider/model-enabled-for-your-account"
export AI_GATEWAY_API_KEY="your-vercel-ai-gateway-key"
node examples/vercel-ai-sdk-mcp.mjs "Scrape https://example.com and summarize it"
```

On Vercel, use the AI Gateway authentication available to the project instead
of placing a key in source. The Apex key and Gateway credentials stay in the
server environment; never expose them to browser code. The model call is billed
by the configured model provider, and scrape calls use the Apex plan and
credits. See the [official AI SDK MCP integration guide](https://vercel.com/docs/mcp/integrations/ai-sdk).

The local bridge also provides `web_search_byok`. Set exactly one of
`BRAVE_SEARCH_API_KEY`, `TAVILY_API_KEY`, or `EXA_API_KEY` in the environment
that launches the AI client (or set `APEX_SEARCH_PROVIDER` when setting more
than one). This tool calls that provider directly from the local bridge; the
provider bills your account and the key is not sent to Apex. Use its result
URLs with `scrape_page`; those page reads still use Apex credits. Both hosted
`webSearch()` and local `web_search_byok` accept optional `includeDomains` or
`excludeDomains` hostname arrays (mutually exclusive, up to 50 values). This local
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
