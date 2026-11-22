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
| Single-page scrape | API/SDK/MCP scrape with Markdown, HTML, links, metadata, screenshots, JSON schema, actions and render controls | `/v1/scrape`; Markdown, text, links, chunks, evidence, schema/CSS extraction, main-content HTML and bounded raw HTML, screenshots, one-request browser actions, source receipt, optional archive fallback; draft `/v2/scrape` supports markdown/html/rawHtml/links/screenshot, provider-backed JSON schema/prompt extraction, and bounded actions | **Partial.** Broaden format semantics, validate against official SDK clients and same permitted fixtures; hosted JSON needs configured provider and paid-plan credits. |
| Crawl and maps | Async crawl jobs/status/cancel/errors/active list; map endpoint and URL filters | `/v1/crawl`, status and cancellation, sitemap-backed map, same-origin policy and plan caps; draft `/v2` create/status/active list/cancel/errors and map adapters; v2 crawl accepts bounded include/exclude regexes, full-URL matching and query-parameter deduplication | **Partial.** Add response pagination parity and same-client lifecycle tests. |
| Batch | Async batch scrape with status and error retrieval | Draft v2 create/status/cancel/error routes, persisted per-URL results, per-URL errors and plan-bounded URL counts; expiring token-fenced SQLite job leases; worker recovery resumes only URLs without persisted results and reconciles completed/credit totals; official Firecrawl Node 4.54.0 and Python 4.57.0 clients exercise batch creation/status | **Partial.** Two SQLite connections race for a single claim and an API restart test verifies saved-page resume. Other SDK versions remain unverified; custom concurrency and webhooks are absent. A crash after an upstream scrape succeeds but before Apex persists its result can cause that URL to be scraped and charged again on recovery; exactly-once charging is not guaranteed. Multi-process load/fault tests and observability remain. |
| Search | General web search; separate research, developer, government and paper indexes | Hosted `/v1/websearch` depends on an optional configured provider; `/v1/search` searches only the customer's own recently saved pages; draft `/v2/search` maps only configured web search | **Major gap.** Deliver a dependable first-party or licensed general-web search path, with rank/source evidence and measured freshness. Do not imply saved-page search searches the web. |
| Structured extraction | JSON/schema modes, multipart document parse, format discovery, and specialized data indexes | CSS recipes and provider-backed JSON Schema extraction; draft `/v2/parse` accepts bounded PDF, DOCX, HTML, text, JSON, and XML uploads, with `/v2/parse/formats` discovery and Markdown/text/link/page/metadata outputs | **Partial.** Apex's upload cap is 12 MiB and one credit per file. Image OCR, raw binary, audio/video, PII redaction, broad format/provider coverage, and typed SDK parity remain gaps. Improve document accuracy and gold-set evidence. |
| Persistent browser and Interact | Browser sandbox sessions, code/actions in session, session list/delete, interact endpoints | Draft `/v2/interact` has short-lived in-memory browser sessions, session list/delete, public-origin allowlists and bounded read-only click/wait/scroll actions; 3-credit create/action charges, two browser slots and one session per account per API process | **Partial.** No CDP/live view, arbitrary code, cookie profiles, signed-in use, or multi-process session affinity; session state is lost on process restart. Needs durable isolated browser workers, authenticated test domain, quotas/load tests and richer safe interactions. |
| Scheduled monitoring | Create/list/update/delete/run monitors and check history | Paid, opt-in monitors with baseline, signed HTTPS events, pause/resume/delete, bounded retries, plan quotas, and expiring token-fenced SQLite leases for check and delivery claims | **Partial parity.** Single-database atomic claim and stale-worker fencing are tested; verify real multi-process contention, crash-after-webhook duplicate behavior, delivery observability/load, and customer value before expanding claims. |
| Credit usage | Current allowance plus historical usage, optionally attributed by API key | Draft `/v2/team/credit-usage` reports current plan allowance and period metadata; `/historical` reports monthly account totals or per-key totals for new metered requests | **Partial.** Rows recorded before key attribution was added remain unattributed; no team-level cross-account billing exists. |
| API key lifecycle | Create, list and revoke account API keys | Draft dashboard and `/v1/keys` list/create/revoke labeled owner-scoped keys; plaintext is returned only on create; the last active key cannot be revoked | **Partial.** All keys share the account's full permissions and credit pool; scoped keys, team roles and key-expiration policies remain unimplemented. |
| Autonomous Agent | Long-running research tasks with run lists/status, trace, snapshot, cancellation and source data | No general autonomous web research job | **Gap.** Add only after task budgets, source citations, stop/cancel, safe tools, run trace and honest limitations are designed. |
| Alexandria / domain providers | Firecrawl Cloud documents access to 550+ data providers plus paper/developer/government indexes | No equivalent hosted provider catalog or broad indexed datasets | **Major gap.** Scope licensed, customer-valued data connectors one at a time; prove provenance, rights, coverage and cost before expanding. |
| Local / self-hosted | Firecrawl Local single binary, MCP installer, local rendering/doc parsing and v2-compatible localhost API; full server can also be self-hosted | Self-hosted Python engine, CLI, local API, MCP and optional local Brave rendering | **Partial.** Broaden binary/one-command installation and support a documented compatibility mode without copying AGPL implementation code. |
| REST/SDK compatibility | Official Python and Node SDKs; current docs list multiple other language/framework SDK integrations | Apex Python and Node clients, REST OpenAPI; draft Firecrawl-style v2 subset documented at `site/firecrawl-compat.html` | **Partial.** Add Firecrawl SDK tests, documented option coverage and broader compatibility before claiming drop-in replacement. |
| MCP and agent installs | Keyless OAuth/browser sign-in or API-key MCP; CLI can auto-detect and install to many clients | API-key hosted MCP and 15 selectable client installer targets, including Codex, Claude Code, Grok and others | **Partial.** Verify each client/version with clean install and real first call; reduce setup friction and publish troubleshooting. Keyless OAuth requires a proper team identity and revocation model. |
| Integrations and distribution | Public catalog spans coding agents, app builders, frameworks, workflow automation, visual builders and data platforms | General REST/MCP works anywhere with those transports; direct examples cover OpenAI, Anthropic, Google ADK, xAI, and Vercel AI SDK 7 via its official MCP client | **Partial.** Add first-party recipes/connectors for high-intent remaining destinations (LangChain/LlamaIndex, n8n/Make, Dify/Flowise, marketplace provisioning), validate supported versions and first-call success, then measure active use. |
| Operations and team plans | Public tiers include recurring free credits, high concurrency, large job queues, pay-as-you-go caps, rollover on some tiers, team and enterprise features, marketplaces and SLA options | Lower listed entry cost in prior snapshot; capped account concurrency, shared service ceiling, no verified team/SLA/marketplace parity | **Gap.** Prove capacity and isolation first; then team accounts, key scopes, spend controls, usage exports, support ownership and any scoped SLA. |

## Priority sequence

1. **Make quality evidence repeatable.** The branch now contains an offline
   signed permissioned gold-set evaluator at `tests/benchmark/`. Next create a
   gold set only for Apex-owned or explicitly authorized sources, run Apex and
   competitors under identical conditions, publish raw data and uncertainty.
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
- [Firecrawl product capabilities](https://docs.firecrawl.dev/features/agent) — Agent, Monitor and the core product navigation.
- [Firecrawl Interact](https://docs.firecrawl.dev/features/interact) and [Monitoring](https://docs.firecrawl.dev/features/monitoring).
- [Firecrawl integrations catalog](https://docs.firecrawl.dev/integrations) and [AI onboarding](https://docs.firecrawl.dev/ai-onboarding).
- [Firecrawl Local/cloud/self-hosted overview](https://github.com/firecrawl/firecrawl).
- [Firecrawl pricing and limits](https://www.firecrawl.dev/pricing) — snapshot includes September 4, 2026 prices; plan limits and feature billing can change.
- Apex branch: `site/openapi.json`, `src/rainbo_scrape/api.py`, `src/rainbo_scrape/hosted_{crawl,search,monitors,mcp}.py`, `sdk/node/hosted.mjs`, `skills/apex-flow-web-scraper/`.
