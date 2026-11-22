# Claude Code handoff: Apex Flow Web Scraper

**Prepared:** 2026-11-22 (Pacific)
**Project:** `E:\apex\work\codex-brief-20261121\scraper`  
**Branch:** `codex/handoff-2026-11-21-scraper`  
**Latest committed product-code commit:** `a7caa11` (`Validate n8n node with official tooling`)
**Pull request:** [#34 — Upgrade Web Scraper hero and AI distribution](https://github.com/Apexflowlabsllc/apex-flow-web-scraper/pull/34)

## Assignment

Review the current branch as an experienced product and systems engineer. Help turn Apex Flow Web Scraper into a dependable, profitable developer product that competes credibly with Firecrawl. Check the actual code, current PR and existing parity documents before recommending changes. Be specific about what is working, what is incomplete, and what the next best implementation is. Do not claim “number one” or full Firecrawl parity without comparative evidence.

The user wants a visually distinctive, highly animated crawler product, broader access from AI tools, customer growth and revenue. Keep the website concise and useful; the user has specifically objected to empty space and vague “crawling the business” imagery. The hero should communicate the page-to-data extraction process.

## Current verified state

- PR #34 is **open and draft**; the latest product commit on this branch is `ed51fba`. Handoff-only documentation commits may follow. GitHub reports **no checks** for the branch. Keep it draft until independently reviewed.
- No production deployment or package publication has occurred. These branch changes are not live in production.
- Full Python suite: **209 passed, 23 warnings** at `4b86a26`; focused Firecrawl compatibility suite **23 passed**. Root Node package suite: **28 passed** at `4b86a26`, including the authenticated MCP doctor fixture, Vercel AI SDK MCP client loop, and official Firecrawl Node SDK 4.54.0 contract test. The n8n package at `a7caa11` passes official CLI 0.57.4 lint/build, TypeScript typecheck, **8 contract tests**, `npm audit --omit=dev` (zero production vulnerabilities), and `npm pack --dry-run` (12 files). The complete development dependency tree reports 35 npm advisories (19 high, 16 moderate), rooted in the CLI/tooling tree; these are excluded from the package tarball. An isolated local n8n 2.48.7 runtime was launched with `n8n-node dev` and a temporary user profile under `E:\...\work`; `/healthz` returned 200 and authenticated `/types/nodes.json` returned 200 with the Apex node, `apexFlowApi` credential, and scrape/batch actions present. The preview was stopped. This confirms catalog loading but not visual editor rendering, workflow execution, credential testing against the hosted API, or other n8n versions. OpenAPI parses as JSON with 43 top-level paths, including all 17 `/v2/*` operations.
- Current committed changes include dashboard API-key controls; create/list/revoke REST endpoints; one-time key display; a 25-active-key limit; a replacement-key guard before revoking the last active one; and a gateway-protected, aggregate funnel report at `/api/internal/funnel`.
- Credit usage now supports account and per-key historical views. New usage is attributed using an opaque stable key ID; old ledger entries are preserved and returned as unattributed.
- Whole-web search now has Brave Search, Tavily Search, and Exa Search adapters behind the existing normalized API contract. One provider is selected per query; a single configured key auto-selects, while multiple configured keys require `APEX_SEARCH_PROVIDER=brave|tavily|exa`. Ambiguous or invalid setup keeps search disabled and hides the MCP tool. Successful searches are charged 130 credits with Brave, 210 with Tavily basic, or 180 with Exa for up to 10 requested results plus 25 per requested result above 10; successful empty searches are billed because providers charge requests. Failed provider calls refund their reservation. Defaults target about 2x current public direct search API costs at the Scale plan's lowest per-credit rate, before hosting, fees and support; this is not a net-margin guarantee. Costs can be tuned with integer credit env vars. Adapter tests use mocked HTTP responses; no live provider credential was available for end-to-end testing. Operator setup and sources are in `ops/SEARCH-PROVIDERS.md`.
- Local MCP users can call `web_search_byok` through either the Python stdio server or npm stdio bridge. It sends search directly from the local process to Brave, Tavily, or Exa using a provider key in that process environment; the provider bills the customer directly and the key is not sent to Apex. Multiple keys require explicit provider selection. Result URLs can then be passed to `scrape_page`, which uses Apex credits. The hosted HTTP MCP tool set does not register this local-only tool. Provider requests are mock-tested, not live-tested.
- The compatibility `/v2/batch/scrape/{id}` status response includes the `success` flag required by the current official Firecrawl Node SDK. Node contract tests exercise official SDK `scrape`, `map`, configured `search`, batch creation and batch status against v2 fixtures. The official Firecrawl Python SDK 4.57.0 test runs against the in-process API for scrape and batch start/status, returns explicit warnings for SDK defaults Apex cannot honor, and rejects unsupported non-default options. Python compatibility changes are committed. This is selected contract evidence, not full SDK compatibility or a live production call. The migration guide documents both clients.
- Monitor workers now use expiring SQLite lease tokens for check and webhook claims; terminal writes are fenced by the token. A two-connection contention test proves a single claim wins and a recovered lease rejects the stale owner. Webhook delivery remains at-least-once across a crash after remote acceptance; receivers should deduplicate using `X-Apex-Event-Id`. Real deployment multi-worker/load behavior still needs validation.
- Firecrawl-compatible batch jobs now support optional `batch_scrape.started`, `.page`, and `.completed` callbacks with event filters, JSON metadata, public HTTPS port-443 validation, DNS-based SSRF checks, no redirects, HMAC-SHA256 signatures, stable event IDs, fenced delivery leases, and bounded retries. Signing secret is returned once. Delivery is at-least-once; receivers must deduplicate. Page payloads over 128 KiB are summarized with a batch-status retrieval hint. Coverage includes success/retry/filter/signature, private destination rejection, truncation, and OpenAPI route shape. Multi-process load/fault testing is still required.
- A server-side Vercel AI SDK 7 example uses the official `@ai-sdk/mcp` Streamable HTTP client to discover and call Apex tools through `generateText`. Its end-to-end test runs a mock AI model through an authenticated local MCP fixture, verifies `tools/call`, and requires no model credentials or production request. No live AI Gateway/model call or end-user adoption is measured.
- The Node installer now has a no-cost `doctor` command that authenticates to MCP and verifies `scrape_page` discovery without scraping. It validates the current shell environment; it cannot prove a separate AI client inherited the same key. The test confirms bearer auth, no scrape call, key-free output, and rejection of non-HTTPS remote endpoints.
- A draft n8n community-node package at `integrations/n8n/` adds native scrape, map, web search, crawl start/status/cancel, and batch-start operations. Typed render/output controls and newline-separated batch URLs replace the open-ended JSON text inputs. n8n stores the API key as a masked credential and tests it against `/v1/usage`; batch requests use the correct `/v2` host root. The package has no runtime dependencies, is marked private to prevent publication, and includes node catalog metadata and themed icons in its tarball. Official CLI lint/build, local contract tests, and a local n8n 2.48.7 catalog-load check pass. Workflow execution, visual editor rendering, and the live credential test remain unverified. It is neither published nor listed.
- Firecrawl-compatible batch jobs persist worker leases and resume after an API restart from only URL rows that are not already saved. Worker claims are token-fenced across SQLite connections; completed and credit totals are reconciled from persisted rows. Per-job/per-URL idempotent reservations are committed with the usage ledger, so a successful upstream scrape can be repeated after a crash before result persistence without charging that URL twice. Tests cover reservation reuse, settlement/refund idempotency, and restart recovery after a simulated successful charge. The upstream fetch remains at-least-once; production multi-worker load/fault testing remains.
- Earlier work on this branch includes Firecrawl-style document parsing, crawl active listing, v2 credit usage, schema-based extraction, bounded browser sessions, crawl filters and errors, SDK/CLI/Agent Skill work, and the animated product hero. See the existing parity matrix and PR body for the full list and limits.
- The dashboard key panel is collapsed by default and keeps the key in the current tab's memory. A new secret is shown once and can be copied by an explicit user action.
- Production Vercel environment variable **names only** were inspected without decrypting values. The current production list showed `SCRAPER_GATEWAY_SECRET` and `STRIPE_SECRET_KEY`; no search-provider key was present. No provider was configured during this review. Do not request or reveal secret values.
- The newest Vercel deployment previously found for this branch was READY at older commit `16a7d76`; there was no preview for the prior branch head. That older preview redirects unauthenticated visits to Vercel SSO. This turn did not recheck Vercel. GitHub reports no checks as of the prior read. Production remains unchanged; this turn did not deploy. The latest product commit is `9d8e5f5`; this handoff documentation and package refresh follows.
- A read-only Claude Opus review was attempted through the available Claude Flow route, but execution stopped because no LLM provider is configured in this environment. No Claude review findings are available yet.
- Vercel Web Analytics queries returned `web_analytics_not_enabled`. The branch now includes a private first-party daily funnel report using existing aggregate events; the report describes event-count ratios, not unique people or cohort conversion. See `ops/FUNNEL-REPORT.md`.

## Highest-value gaps

1. **Search operations, pricing and economics:** branch adapters support Brave, Tavily and Exa, but no provider key is configured in production and there is no live upstream quality/latency test. Current published Firecrawl pricing is 2 credits per 10 results. Apex's Scale plan allocation makes a 10-result Brave search about $0.0104 in Apex credits versus about $0.0015 on Firecrawl Scale; this is a material price gap, though provider and plan credit units are not identical. Sources checked 2026-11-22: [Firecrawl pricing](https://www.firecrawl.dev/pricing), [Brave pricing](https://api-dashboard.search.brave.com/documentation/pricing), [Tavily pricing](https://www.tavily.com/pricing), and [Exa pricing](https://exa.ai/pricing). Before enabling paid traffic, confirm current vendor terms/account rates and compare same-workload quality. Consider a licensed low-cost search index, wholesale partnership, or local bring-your-own-key flow that keeps customer keys on the customer's machine. Search provider costs are separate from Apex credits. Do not enable automatic provider failover: a timeout can leave upstream billing ambiguous.
2. **AI reach:** 15 MCP installer targets and Python/Node clients exist, including local BYOK web search, a tested Vercel AI SDK 7 integration, and a no-credit authenticated discovery doctor. Official Firecrawl Node 4.54.0 and Python 4.57.0 clients have selected contract coverage against the migration API; other SDK versions and additional clients remain unverified. Prioritize clean-install and first-call tests for the remaining clients and framework recipes, then measure first-call success and retention before adding distribution listings.
3. **Browser capability:** interactive sessions are short-lived and process-local, limited to two browser slots per process, and do not expose CDP/live streaming, persistent profiles or authenticated login. Durable multi-worker session affinity is still missing.
4. **Research and extraction breadth:** autonomous agent runs/traces, research/developer/government indexes, broad provider catalogs, image OCR, audio/video extraction, and PII redaction are not implemented.
5. **Product readiness:** there is no proven head-to-head quality result, production load/uptime evidence, public package release, or verified revenue from this branch. The current deployment has not been updated from this PR.

## Review these changes closely

- `src/rainbo_scrape/billing.py`: additive SQLite migration for key IDs and per-key usage; context-local request attribution.
- `src/rainbo_scrape/api.py`: auth middleware, key create/list/revoke routes, account ownership and last-key protection.
- `src/rainbo_scrape/hosted_firecrawl_compat.py`: current and historical usage response shapes.
- `src/rainbo_scrape/hosted_features.py`: owner-only funnel report and existing aggregate event ledger.
- `src/rainbo_scrape/hosted_search.py`: Brave/Tavily/Exa adapters, deterministic provider selection, normalized result URLs/snippets and conditional MCP exposure.
- `src/rainbo_scrape/hosted_mcp.py`, `sdk/node/mcp-bridge.mjs`: local-only BYOK search tool; Python registers it only in local stdio mode, while Node intercepts it before forwarding calls to hosted MCP.
- `src/rainbo_scrape/billing.py`, `src/rainbo_scrape/api.py`, `src/rainbo_scrape/hosted_firecrawl_compat.py`: per-job/per-URL durable billing idempotency and persisted batch recovery; focused test run: 29 passed.
- `sdk/node/firecrawl-compat.test.mjs`: selected official Firecrawl Node 4.54.0 and Python 4.57.0 contract coverage, batch-status response, and persisted batch restart recovery.
- `sdk/node/examples/vercel-ai-sdk-mcp.mjs`, `sdk/node/vercel-ai-sdk-mcp.test.mjs`: server-side Vercel AI SDK 7 MCP integration and authenticated local tool-call test.
- `site/dashboard.html`, `site/dashboard.js`, `site/app.css`: collapsed key panel, one-time secret display and revoke flow.
- `site/openapi.json`, `site/docs.html`, `site/firecrawl-compat.html`: public contract and guidance.
- `src/rainbo_scrape/hosted_firecrawl_compat.py`, `tests/test_firecrawl_compat.py`: batch lifecycle webhook queue, signature/retry delivery, and contract tests added at `4b86a26`.
- `integrations/n8n/`: private unpublished n8n package; official CLI lint/build and a local 2.48.7 node-catalog load pass, while visual editor rendering and workflow execution remain outstanding. Its CLI tooling dependency tree has 35 development-only npm advisories; production dependency audit is clean.
- Tests: `tests/test_billing.py`, `tests/test_firecrawl_compat.py`, and `tests/test_parity.py` (including provider selection, mocked vendor requests and normalized result handling).

In particular, check cross-account isolation, legacy database upgrades, context propagation to background jobs, refunds, revoked-key visibility, last-key handling, escaping of labels, and that bearer secrets are never returned by list routes or written to logs.

## Hard constraints

- Keep project files and scratch work on **E:**. Do not copy project artifacts to C:.
- `design/` is user-created and untracked. Do not edit it, delete it, or stage it. Stage named files only; do not use `git add .`.
- The handoff brief requires **$0 spend**, no package publishing, and no production deployment unless Brian types exactly **“yes.”** Keep PR #34 in draft until independently reviewed; do not merge or publish.
- Preserve the product name **Apex Flow Web Scraper**. Respect robots.txt and access controls; do not add CAPTCHA, bot-wall or paywall bypasses.
- Never expose API keys, billing credentials, or Vercel secret values.
- For any browser or visual QA, use Brian's existing Brave session only and follow the project's computer-use preflight. Do not use Chrome, Edge, or an in-app browser.

## Useful commands and documents

```powershell
Set-Location E:\apex\work\codex-brief-20261121\scraper
.\.venv\Scripts\python.exe -m pytest -q
node --check site/dashboard.js
.\.venv\Scripts\python.exe -c "import json; json.load(open('site/openapi.json', encoding='utf-8'))"
git status --short
```

- `FIRECRAWL-PARITY-MATRIX-2026-11-22.md` — feature-by-feature comparison and honest gaps.
- `COMPETITIVE-BATTLEPLAN-2026-11-22.md` — positioning, proof requirements and build order.
- `CAPABILITY-ROADMAP.md` — technical roadmap and acceptance evidence.
- `site/firecrawl-compat.html` — compatibility contract and limitations.

Return prioritized findings with file/line references, then propose one concrete next patch and its proof. Do not perform production or paid actions.
