# Apex Flow Web Scraper: capability plan

**Decision record, 8 October 2026 · updated 22 November 2026 · repository package 0.11.0**

The aim is broad, dependable capability under customer control. “Beat every
competitor” is a direction, not a measured result. We do not describe a feature
as ready until it runs, has a named security boundary and passes a published
acceptance gate. We do not claim every site can be accessed or every field can
be recovered.

## What users can do today

This roadmap covers both the self-hosted engine and the hosted service. The
repository's API source, `site/openapi.json`, and `site/docs.html` describe a
hosted product with authenticated page and batch scraping, JavaScript
rendering, sitemap discovery, crawl jobs, change diffs, CSS recipes, paid JSON
Schema extraction, account-scoped saved-page search, PDF/DOCX parsing, Shopify
catalog change signals, and remote MCP access. A source-tree description does
not prove that the latest branch is deployed; verify the release before
promising a hosted feature or limit.

The self-hosted engine supports public HTTP/HTTPS pages, same-origin crawl
jobs, an optional isolated Brave render, CSS extraction recipes, required-field
validation, source evidence, durable status and cancellation, JSON/CSV exports,
local corpus search, and MCP tools for health, fetch, maps, crawls, recipes,
search, jobs and change comparisons. It can also use a separate local browser
profile that the account owner signs into; that profile remains on the owner's
machine and is not sent to the hosted service. The worker and local website
are single-installation tools, not a public multi-user service.

Neither edition provides CAPTCHA solving, bot-wall evasion, managed proxy
pools, visual workflow recording, OCR, shared cloud accounts, global datasets,
or a contractual extraction SLA. An operator may configure one
customer-controlled HTTP proxy with `RAINBO_SCRAPE_PROXY_URL`; the engine
validates destinations against public-address policy before forwarding and
exposes only a boolean health indicator. This is proxy routing, not a proxy
network or geographic coverage promise. Existing unit tests and smoke scrapes
are not comparative accuracy, coverage or uptime evidence.

## Capability map

### What the four products document about access

This is a review of public product documentation, not a claim about hidden
implementation details. Firecrawl documents a basic-to-enhanced proxy retry for
401/403/429 responses. Apify documents managed proxy groups and persistent
sessions that retain IP/cookie state, and markets a separate Unblocker product
for anti-bot and anti-CAPTCHA handling. Playwright documents browser/context
proxy configuration and isolated contexts; it is an automation library, not an
IP network. Crawl4AI documents block/challenge detection, retries, configured
proxy escalation and an external-fetch fallback hook. Our defensible target is
transparent first-party egress, per-job route policy and customer-visible
challenge control. CAPTCHA solving and guaranteed access are outside the plan.

Sources: [Firecrawl Enhanced Mode](https://docs.firecrawl.dev/features/enhanced-mode),
[Apify Proxy and Unblocker](https://docs.apify.com/proxy),
[Playwright network proxy](https://playwright.dev/python/docs/network) and
[browser-context isolation](https://playwright.dev/docs/browser-contexts),
[Crawl4AI anti-bot detection and fallback](https://docs.crawl4ai.com/advanced/anti-bot-and-fallback/).

| Capability customers compare | Current Apex position | Build path | Proof required before marketing it as ready |
|---|---|---|---|
| Durable crawling and job control | Implemented locally; hosted source now includes paid, opt-in scheduled monitors with persisted schedule/event state, signed HTTPS notifications, bounded retries, pause/resume/delete and plan quotas | Harden cross-process claiming, idempotent delivery under restart, delivery observability, queue fairness, retention tools and capacity controls | Fault-injection and load run with stated hardware, job mix and recovery measurements; monitor tests cover billing/refunds, restart, duplicate delivery, SSRF/DNS rebinding, tenant isolation and exhausted-credit pause |
| Extraction breadth | HTML, Markdown, JSON-LD, tables, feeds, sitemaps, JSON, text and text-based PDFs | Add reviewed product/article/job schemas, pagination patterns, iframe policy, image OCR and language-aware parsing | Human-labeled gold set; per-field precision, recall, coverage and source attribution |
| Recipe authoring | CSS recipes and required fields | Visual selector picker, page preview, multi-page recipe tests, suggested fields with source highlights | Recipe success and maintenance study across unseen layouts; user can inspect and edit every rule |
| Layout change detection | Compare saved results | Detect selector drift, unexpected empty fields, types and page-template changes; generate repair proposals for review | Versioned-change benchmark; show detection and false-alarm rates, keep human approval before replacing a recipe |
| JavaScript sites | Source now supports isolated Brave rendering plus bounded one-request click, wait and scroll actions (8 actions, 10 total viewport heights, 10 seconds explicit waits); no retained browser or login session | Persistent browser sessions and reusable profiles with domain limits, expiration, and audit evidence; configurable resource policy | Fixture proves a click reveals dynamic content and is returned through REST/MCP; verify metering, failure refunds, SSRF guards, resource budgets, and session isolation before marketing persistent sessions |
| Signed-in customer accounts | Not supported | A local, isolated browser profile that the account owner signs into directly; domain-limited reusable sessions; explicit expiration and revocation | Security review, profile isolation tests, no password capture, no main-browser cookie import, no credentials in jobs/logs/repository |
| CAPTCHA and access challenges | Challenge pages are detected with a dedicated access-challenge error and stopped as failures; no bypass or human-resume flow | Add a visible challenge pause for an isolated, owner-authorized profile. Let the account owner complete a permitted step themselves, then resume only if the site allows it; preserve event evidence and revoke state on demand | Demonstrate owner-directed completion, challenge detection and safe resume without automation impersonating a human or selling CAPTCHA bypass |
| Proxy and geographic access | Optional operator-configured customer-owned HTTP proxy; destination DNS is separately checked against the public-address policy; no managed pool or geography claim | Build an Apex-operated outbound proxy fleet on infrastructure owned or contracted directly by Apex, then add per-tenant egress policy, explicit region selection, pinned sessions, health checks, quotas, spend caps, source/ownership records, and abuse response. Residential egress is out of scope unless every endpoint has documented, informed opt-in and revocation | Network ownership and sourcing evidence, per-region availability, spend cap and independent route tests; no proxy-rotation or ban-evasion guarantee |
| Multiple customers and teams | Single trusted installation | Add separate accounts, encrypted tenant storage, project roles, quotas, audit log, deletion and backup/restore | Tenant-isolation tests, external security assessment, recovery exercise and published operational ownership |
| Hosted managed service | Not implemented | Only after the multi-tenant security model: account isolation, durable regional workers, public API, queues, billing, observability and support operations | Production load test, independent security review, incident runbook, tested backups and an honestly scoped service commitment |
| API and assistant access | 11 self-hosted MCP tools; hosted source now exposes 19 tools plus optional whole-web search; REST OpenAPI, Python/Node clients, 15 installer targets and direct API examples for OpenAI, Anthropic, Google ADK, and xAI/Grok's native and Responses APIs in this branch | Validate each client schema and supported version; publish packages only through the reviewed release gate; use integration success and retention—not installer count—as adoption measures | Per-client integration tests, stable OpenAPI contract, clean registry installs, signed notification checks and measured API uptime/error rates |
| Data quality commitment | No numeric guarantee | Offer a scoped acceptance commitment for specified sources, fields, sample size, region, frequency and freshness window | Signed gold set, independent scoring, minimum denominator, error remedy and explicit exclusions for source outages/access blocks |
| Cost advantage | No required scraping vendor key | Measure CPU, storage, bandwidth, operator time and customer infrastructure for each run; show predicted and actual use before scaling | Reproducible cost-per-correct-field results alongside equivalent managed-service comparisons |

## Build order

### Gate 1 — Trust and measurement

Before widening access, define the data contract and quality dashboard. Each
record gets source URL, retrieved time, extraction method, evidence, validation
result and a coverage status. Build a customer-owned benchmark format with
expected records and field values, review state and permitted-domain scope.

Report distinct denominators:

- **Page reach** = eligible pages successfully retrieved / eligible pages in
  the reviewed scope.
- **Field coverage** = expected fields returned / expected fields in the gold
  set.
- **Field accuracy** = returned values that meet the field-specific rule / all
  returned values reviewed.
- **Job completion** = jobs reaching the requested terminal state / accepted
  jobs, reporting blocked and canceled jobs separately.
- **Cost per correct field** = measured infrastructure and operator cost /
  correctly extracted fields.

Publish sample size, confidence intervals, date, exact engine version, target
sites, rendering mode and omissions. Do not combine field accuracy with page
reach into a headline percentage. A 99% score over one hand-picked page is not
a general 99% guarantee.

### Gate 2 — Account-owned access and human challenge recovery

Never read credentials from the user's everyday browser or ask an assistant to
copy cookies into chat. Open a separate, isolated, visible browser profile on
the user's machine. The account owner signs in and completes MFA or an access
challenge themselves. Save a domain-bound session only in the operating
system's protected credential store, show when it expires, and provide a
one-click revoke/delete action. A collection job receives only the named
profile, exact domain allowlist and read-only HTTP methods by default. Every job
records the profile identifier, not secret material.

If a site blocks access or requires a challenge, stop with a useful reason. Do
not solve third-party CAPTCHA puzzles, spoof a person, defeat paywalls, or
rotate identities to evade a site's controls. For a site the customer owns,
integrate that site's supported test token or authorization flow. Cloudflare
requires server-side validation of Turnstile tokens before treating a challenge
as passed; a visible checkbox alone proves nothing. [Cloudflare Turnstile
guidance](https://developers.cloudflare.com/cloudflare-challenges/challenge-types/turnstile/)

Playwright specifically warns that browser state files contain reusable
credentials and should not be checked into source control. Account state must
therefore be isolated and encrypted before this feature ships.
[Playwright authentication guidance](https://playwright.dev/python/docs/auth)

### Gate 3 — Quality-first extraction

Add reviewed, typed schemas for product, article, job posting, event, property
and business directory data. Add visual extraction that highlights the exact
source span, a set-based recipe test runner, pagination planning, and drift
alerts. Any AI-assisted schema or recipe suggestion must be a reviewable draft
and must retain field-level evidence. Core use remains available without a paid
AI or scraping account.

### Gate 4 — Customer network controls

Add an optional customer-supplied proxy only after secret storage and policy
checks are ready. Validate destination safety after DNS resolution, prevent
proxy credentials from appearing in URLs or logs, cap spend, and show the
customer which egress region their own provider actually supplied. A
first-party residential proxy fleet is not a quick software feature: it needs
network sourcing, consent, regional operations, abuse prevention, privacy
reviews, vendor contracts and substantial capital. Do not represent rented or
customer-supplied endpoints as an Apex-owned network.

### Gate 5 — Team and managed service parity

Build a hosted service only after isolation, public API authentication, billing
and incident operations have passed security review. Then add team roles,
quotas, audit exports, data retention controls, alerting and responsive support.
An SLA must name the covered service, measurement window, exclusions, remedies
and support hours. It never guarantees that a target website will allow a
request or keep its own layout stable.

## How to compare fairly

Our existing 20-provider list is a curated introduction, not a verified feature
audit or a universal top-20 ranking. Compare the exact product tier, current
documentation, date, cost model and workload. The categories differ:
open-source crawlers, visual desktop tools, cloud automation marketplaces,
proxy networks, managed extraction APIs and data-delivery services are not
interchangeable products.

For every contender, use the same permissioned, versioned gold set and report:
page reach; per-field exactness; source evidence; JavaScript coverage; access
challenge rates; p50/p95 time; retries; human repair minutes; cost per correct
field; cancellation/recovery; region; export format; and support response
commitments. Run separate direct-network and customer-proxy tests. Preserve
the raw scorecard so a marketing claim can be traced to results.

Examples of structural advantages competitors publicly document include
managed proxies, persistent proxy sessions, browser automation, and automatic
structured extraction. Apify documents proxy groups and persistent sessions;
Zyte documents browser/HTTP extraction paths, automatic schemas and pricing by
request tier. Apex should meet the customer's underlying job where it is
valuable, while retaining source-level evidence, explicit budgets, portable
storage and customer control. These examples do not imply that every competitor
offers every feature. [Apify proxy sessions](https://docs.apify.com/proxy)
· [Zyte extraction and pricing](https://docs.zyte.com/zyte-api/usage/extract/spiders.html)

## Public claims policy

Do not say “works on every site,” “beats everyone,” “bypasses CAPTCHA,” “99%
accurate,” “unlimited,” or “guaranteed uptime” until the matching product
capability, benchmark and customer remedy exist. The comparison page must say
when a feature is planned and name the evidence behind any result. Rankings,
search citations and customer outcomes cannot be guaranteed.

For search and answer visibility, use clear technical structure and original
helpful material, then measure real discovery. Google states that indexing and
serving are not guaranteed and recommends useful, expert-led content over
search-first volume. [Google guidance for generative search](https://developers.google.com/search/docs/fundamentals/ai-optimization-guide)

## Immediate next three builds

1. **Evaluator implemented in branch:** `tests/benchmark/goldset_evaluator.py`
   verifies signed permission-scoped manifests and reports reach, field
   coverage, field accuracy, Wilson confidence intervals, failures, latency,
   and reported cost. Its format and limitations are in
   `tests/benchmark/GOLDSET-GUIDE.md`. Still required: owner-authorized,
   multi-layout gold data and an identical-condition cross-vendor run.
2. **Migration adapter started in branch:** Firecrawl-shaped `/v2/scrape`,
   `/v2/map`, `/v2/search`, and crawl create/status/cancel routes now translate
   to Apex's existing metered features and reject unsupported options. The
   async batch route returns an explicit 501. Five endpoint tests cover the
   current subset. Still required: same-client compatibility tests, more
   response-schema coverage, broader endpoints, and a reviewed production
   release before customers can use it live.
3. **Build safe persistent local sessions:** owner login, MFA, explicit human
   challenge handling, encrypted site-bound state, expiry, scope and
   revocation. Keep sessions off the hosted service and out of chat.

The current public feature-surface comparison is tracked in
`FIRECRAWL-PARITY-MATRIX-2026-11-22.md`. Its top gaps remain general web search,
durable browser sessions, autonomous research, large-scale data providers,
async batch throughput, keyless team access and measured service capacity.
Do not treat the matrix as proof of feature equivalence or production release.

Only after these gates should we scope customer-owned proxies, first-party
network infrastructure, OCR, teams and managed hosting. This sequence delivers
quality and trust before buying scale.
