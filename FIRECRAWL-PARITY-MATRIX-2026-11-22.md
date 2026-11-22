# Apex Flow Web Scraper / Firecrawl parity matrix

**Snapshot: 22 November 2026.** Based on Firecrawl's public API index, product
documentation, pricing page, repository README and integrations catalog, plus
the current Apex branch source and docs. This maps documented surfaces; it is
not a test of hidden Firecrawl internals or proof that Apex branch features are
live in production. Statuses describe the strongest evidence found in source,
not a sales claim.

## Product and API coverage

| Area | Firecrawl documented surface | Apex branch evidence | Status and next build |
|---|---|---|---|
| Single-page scrape | API/SDK/MCP scrape with Markdown, HTML, links, metadata, screenshots, JSON schema, actions and render controls | `/v1/scrape`; Markdown, text, links, chunks, evidence, schema/CSS extraction, main-content HTML and bounded raw HTML, screenshots, one-request browser actions, source receipt, optional archive fallback; draft `/v2/scrape` supports markdown/html/rawHtml/links/images/screenshot, provider-backed JSON schema/prompt extraction, bounded actions, Firecrawl `waitFor` mapped to a rendered wait capped at 5 seconds, and `minAge`/`lockdown` cache-only reads with 404 cache-miss codes. Image URLs are resolved, deduplicated and capped at 2,000 per page; bytes are not downloaded and no OCR is performed. Cache-only requests make no target request and require a recent cached robots.txt policy; JSON extraction, screenshots and browser actions are rejected. The same image and attributes formats are supported by batch and crawl, and the same wait mapping is accepted on batch pages and crawl `scrapeOptions` | **Partial.** Broaden format semantics, validate against official SDK clients and same permitted fixtures; hosted JSON needs configured provider and paid-plan credits. |
| Crawl and maps | Async crawl jobs/status/cancel/errors/active list; map endpoint and URL filters | `/v1/crawl`, status and cancellation, sitemap-backed map, same-origin policy and plan caps; draft `/v2` create/status/active list/cancel/errors and map adapters; v2 crawl accepts bounded include/exclude regexes, full-URL matching and query-parameter deduplication | **Partial.** Add response pagination parity and same-client lifecycle tests. |
| Batch | Async batch scrape with status, errors, cancellation, and started/page/completed lifecycle webhooks | Draft v2 create/status/cancel/error routes, persisted per-URL results, per-URL errors and plan-bounded URL counts; expiring token-fenced SQLite job leases; worker recovery resumes only URLs without persisted results and reconciles completed/credit totals; durable per-job/per-URL billing idempotency; optional signed lifecycle callbacks with event filters, metadata, HTTPS-only delivery, SSRF-resistant DNS resolution, stable event IDs and bounded retries; official Firecrawl Node 4.54.0 and Python 4.57.0 clients exercise batch creation/status | **Partial.** Tests cover two-connection claim fencing, restart recovery with single-charge billing, signed callbacks, event filtering and retry delivery. Upstream fetch and webhook delivery are at-least-once; page callback payloads over 128 KiB are summarized and must be retrieved from batch status. Other SDK versions remain unverified; custom concurrency and multi-process load/fault observability remain. |
| Search | General web, news, images, Alexandria tools and separate research, developer, government and paper indexes | Hosted `/v1/websearch`, local Python/Node BYOK, MCP and n8n search expose general web, dedicated Tavily/Exa news, and Brave dedicated image search; images use strict SafeSearch and return source-page URL, direct image URL and available dimensions; draft `/v2/search` supports multiple distinct web/news/images sources per request in Firecrawl string/object forms, returning one array per source with per-source limits, hostname filters, and a `pdf` category using the provider's `filetype:pdf` query operator plus a `.pdf` URL-path post-filter (not a dedicated PDF index). Web/news can use `scrapeOptions` to scrape each result through Apex safety checks and metering, with supported markdown/html/rawHtml/links/images/screenshot outputs, bounded waits/actions, per-result errors, and aggregate credits; image-result scraping, JSON extraction and summary formats remain unsupported. `/v1/search` searches only the customer's own recently saved pages; all whole-web search still requires a configured provider | **Partial.** Search-result scraping and PDF discovery close useful Firecrawl workflows. Alexandria and dedicated developer/government indexes remain gaps. Crossref-backed DOI metadata search/read works without additional configuration. Setting `OPENALEX_API_KEY` switches paper search/read to the OpenAlex scholarly corpus and adds citation counts plus bounded reference/related-work IDs and retraction status; provider errors or empty DOI results fall back to Crossref and are labeled by source. Abstracts remain omitted, and the `/related` and `/citations` routes return bounded DOI records; full multi-hop graph traversal remains a gap. The research category is a curated academic-domain filter. Provider quality/freshness and full source-option parity need live permitted benchmarks. Do not imply saved-page search searches the web. |
| Structured extraction | JSON/schema modes, multipart document parse, format discovery, and specialized data indexes | CSS recipes and provider-backed JSON Schema extraction; draft `/v2/parse` accepts bounded PDF, DOCX, HTML, text, JSON, and XML uploads, with `/v2/parse/formats` discovery and Markdown/text/link/page/metadata outputs | **Partial.** Apex's upload cap is 12 MiB and one credit per file. Image OCR, raw binary, audio/video, PII redaction, broad format/provider coverage, and typed SDK parity remain gaps. Improve document accuracy and gold-set evidence. |
| Persistent browser and Interact | Browser sandbox sessions, code/actions in session, session list/delete, interact endpoints | Draft `/v2/interact` has short-lived in-memory browser sessions, session list/delete, public-origin allowlists and bounded read-only click/wait/scroll actions; 3-credit create/action charges, two browser slots and one session per account per API process | **Partial.** No CDP/live view, arbitrary code, cookie profiles, signed-in use, or multi-process session affinity; session state is lost on process restart. Needs durable isolated browser workers, authenticated test domain, quotas/load tests and richer safe interactions. |
| Scheduled monitoring | Create/list/update/delete/run monitors and check history | Paid, opt-in monitors with baseline, signed HTTPS events, pause/resume/delete, bounded retries, plan quotas, and expiring token-fenced SQLite leases for check and delivery claims | **Partial parity.** Single-database atomic claim and stale-worker fencing are tested; verify real multi-process contention, crash-after-webhook duplicate behavior, delivery observability/load, and customer value before expanding claims. |
| Credit usage | Current allowance plus historical usage, optionally attributed by API key | Draft `/v2/team/credit-usage` reports current plan allowance and period metadata; `/historical` reports monthly account totals or per-key totals for new metered requests | **Partial.** Rows recorded before key attribution was added remain unattributed; no team-level cross-account billing exists. |
| API key lifecycle | Create, list and revoke account API keys | Draft dashboard and `/v1/keys` list/create/revoke labeled owner-scoped keys; plaintext is returned only on create; the last active key cannot be revoked | **Partial.** All keys share the account's full permissions and credit pool; scoped keys, team roles and key-expiration policies remain unimplemented. |
| Autonomous Agent | Long-running research tasks with run lists/status, trace, snapshot, cancellation and source data | No general autonomous web research job | **Gap.** Add only after task budgets, source citations, stop/cancel, safe tools, run trace and honest limitations are designed. |
| Alexandria / domain providers | Firecrawl Cloud documents access to 550+ data providers plus paper/developer/government indexes | No equivalent hosted provider catalog or broad indexed datasets | **Major gap.** Scope licensed, customer-valued data connectors one at a time; prove provenance, rights, coverage and cost before expanding. |
| Local / self-hosted | Firecrawl Local single binary, MCP installer, local rendering/doc parsing and v2-compatible localhost API; full server can also be self-hosted | Self-hosted Python engine, CLI, local API, MCP and optional local Brave rendering | **Partial.** Broaden binary/one-command installation and support a documented compatibility mode without copying AGPL implementation code. |
| REST/SDK compatibility | Official Python and Node SDKs; current docs list multiple other language/framework SDK integrations | Apex Python and Node clients, REST OpenAPI; draft Firecrawl-style v2 subset documented at `site/firecrawl-compat.html` | **Partial.** Add Firecrawl SDK tests, documented option coverage and broader compatibility before claiming drop-in replacement. |
| MCP and agent installs | Keyless OAuth/browser sign-in or API-key MCP; CLI can auto-detect and install to many clients | API-key hosted MCP and 16 selectable client installer targets, including Codex, Claude Code, Grok and others | **Partial.** Verify each client/version with clean install and real first call; reduce setup friction and publish troubleshooting. Keyless OAuth requires a proper team identity and revocation model. |
| Integrations and distribution | Public catalog spans coding agents, app builders, frameworks, workflow automation, visual builders and data platforms | General REST/MCP works anywhere with those transports; direct examples cover OpenAI, Anthropic, Google ADK, xAI, Vercel AI SDK 7 via its official MCP client, LangChain's current `MCPAdapter` (isolated client discovers and calls the Apex fixture), and LlamaIndex `McpToolSpec` (isolated `llama-index-tools-mcp` 0.6.0 client discovers, wraps and invokes the fixture tool); draft n8n community package provides native scrape/map/search/crawl/batch operations | **Partial.** LangChain marks its MCP namespace beta. LlamaIndex's MCP client currently requires MCP SDK 2.x while the Apex server package depends on MCP 1.x, so the first-party example and compatibility check keep the two environments isolated. The n8n package is not published or tested in a live n8n instance. Add Make and Dify/Flowise recipes, validate supported versions and first-call success, then measure active use. |
| Operations and team plans | Public tiers include recurring free credits, high concurrency, large job queues, pay-as-you-go caps, rollover on some tiers, team and enterprise features, marketplaces and SLA options | Lower listed entry cost in prior snapshot; capped account concurrency, shared service ceiling, no verified team/SLA/marketplace parity | **Gap.** Prove capacity and isolation first; then team accounts, key scopes, spend controls, usage exports, support ownership and any scoped SLA. |

## Priority sequence

1. **Make quality evidence repeatable.** The branch contains an offline,
   signed permissioned gold-set evaluator and a sequential Apex/Firecrawl API
   comparison runner at `tests/benchmark/`. The runner validates the signed
   host scope, saves per-engine requests/responses, requires an explicit flag
   for billable calls, and does not infer USD from different credit systems.
   The repository now adds six synthetic, project-owned fixture pages across
   product (two layouts), article, job, event, and directory formats. A test
   runs Apex's production extractor and scores 23 expected fields using the
   signed evaluator; it makes no network calls. This is a parser regression
   gate only. Next obtain a broader gold set from Apex-owned or explicitly
   authorized live sources, run both APIs under identical conditions, and
   publish raw data and uncertainty. No valid head-to-head result exists yet.
2. **Win migration and reach.** The branch has a documented, tested Firecrawl
   v2 adapter subset for `scrape`, `map`, configured `search`, `crawl`, and
   asynchronous `batch/scrape` create/status/cancel/errors. Node tests exercise
   the official `firecrawl` 4.54.0 client and Python tests exercise official
   `firecrawl` 4.57.0 against selected v2 request/response shapes. Expand
   version coverage and the adapter against the full official schema before any
   broader compatibility claim.
3. **Make JavaScript access useful across steps.** Draft `/v2/interact` now
   keeps a guarded public-site browser context alive across calls, with origin
   binding, expiry, revocation, resource ceilings and bounded declarative
   actions. Next prove it in a browser-enabled environment, then design
   durable isolated workers and customer-owned authenticated profiles.
4. **Close discovery and data breadth.** Select a lawful search/index provider
   path and vertical datasets based on customer demand, freshness, provenance,
   access rights and measured cost per correct answer.
5. **Expand distribution with proof.** Test each installer and SDK against
   supported client versions, add the missing framework/automation recipes,
   and report install-to-first-success and retention rather than number of
   logos or config files.
6. **Earn service scale.** Load test worker queues, fairness, regional
   availability, tenant isolation, billing/refunds, backups and incident
   recovery before raising concurrency, selling teams, or publishing an SLA.

“Give it every bit of access” means more useful, authorized routes and
integrations, not bypassing robots rules, CAPTCHAs, paywalls, or target-site
access controls. For signed-in sites, the safe design is an isolated profile
controlled by the account owner, with narrow domain scope and explicit
revocation.

## Evidence sources

- [Firecrawl API endpoint index](https://docs.firecrawl.dev/api-reference/endpoint/scrape) — scrape, batch, interact, research indexes, map, parse, crawl, monitor, agent and feedback.
- [Firecrawl Batch Scrape API](https://docs.firecrawl.dev/api-reference/endpoint/batch-scrape) and [webhook events](https://docs.firecrawl.dev/webhooks/events) — request fields and lifecycle payload references checked 22 November 2026.
- [Firecrawl product capabilities](https://docs.firecrawl.dev/features/agent) — Agent, Monitor and the core product navigation.
- [Firecrawl Interact](https://docs.firecrawl.dev/features/interact) and [Monitoring](https://docs.firecrawl.dev/features/monitoring).
- [Firecrawl integrations catalog](https://docs.firecrawl.dev/integrations) and [AI onboarding](https://docs.firecrawl.dev/ai-onboarding).
- [Firecrawl Local/cloud/self-hosted overview](https://github.com/firecrawl/firecrawl).
- [Firecrawl pricing and limits](https://www.firecrawl.dev/pricing) — snapshot includes September 4, 2026 prices; plan limits and feature billing can change.
- Apex branch: `site/openapi.json`, `src/rainbo_scrape/api.py`, `src/rainbo_scrape/hosted_{crawl,search,monitors,mcp}.py`, `sdk/node/hosted.mjs`, `skills/apex-flow-web-scraper/`.
