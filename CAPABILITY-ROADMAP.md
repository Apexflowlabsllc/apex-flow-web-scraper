# Apex Flow Web Scraper: capability plan

**Decision record, 8 October 2026 · Public engine 0.1.0**

The aim is broad, dependable capability under customer control. “Beat every
competitor” is a direction, not a measured result. We do not describe a feature
as ready until it runs, has a named security boundary and passes a published
acceptance gate. We do not claim every site can be accessed or every field can
be recovered.

## What users can do today

The 0.1.0 engine supports public HTTP/HTTPS pages, same-origin crawl jobs, an
optional isolated Brave render, CSS extraction recipes, required-field
validation, source evidence, durable status and cancellation, JSON/CSV exports,
local corpus search and nine MCP tools. The worker and local website are
single-installation tools; the workspace is not a public multi-user service.

It does not yet support customer login profiles, CAPTCHA solving or
user-assisted challenge continuation, customer proxy pools, visual workflow
recording, OCR, shared cloud accounts, global datasets, or a contractual
extraction SLA. The 33 existing tests and one successful server smoke scrape
are not comparative accuracy, coverage or uptime evidence.

## Capability map

| Capability customers compare | Current Apex position | Build path | Proof required before marketing it as ready |
|---|---|---|---|
| Durable crawling and job control | Implemented locally; one worker | Add scheduled runs, retry policy editor, queue fairness, capacity controls and retention tools | Fault-injection and load run with stated hardware, job mix and recovery measurements |
| Extraction breadth | HTML, Markdown, JSON-LD, tables, feeds, sitemaps, JSON, text and text-based PDFs | Add reviewed product/article/job schemas, pagination patterns, iframe policy, image OCR and language-aware parsing | Human-labeled gold set; per-field precision, recall, coverage and source attribution |
| Recipe authoring | CSS recipes and required fields | Visual selector picker, page preview, multi-page recipe tests, suggested fields with source highlights | Recipe success and maintenance study across unseen layouts; user can inspect and edit every rule |
| Layout change detection | Compare saved results | Detect selector drift, unexpected empty fields, types and page-template changes; generate repair proposals for review | Versioned-change benchmark; show detection and false-alarm rates, keep human approval before replacing a recipe |
| JavaScript sites | Isolated local Brave render | Bounded actions, explicit waits, pagination and screenshots; configurable resource policy | Repeatable same-page HTTP/browser comparison and memory/latency limits |
| Signed-in customer accounts | Not supported | A local, isolated browser profile that the account owner signs into directly; domain-limited reusable sessions; explicit expiration and revocation | Security review, profile isolation tests, no password capture, no main-browser cookie import, no credentials in jobs/logs/repository |
| CAPTCHA and access challenges | Challenge pages are detected and reported | Preserve a clear “waiting for account owner” job state; let the user continue in the isolated owned profile when the destination permits; save a short-lived resumed session | Demonstrate user-directed completion, challenge detection and safe resume without automation impersonating a human or selling CAPTCHA bypass |
| Proxy and geographic access | Direct network only | First support an optional customer-owned proxy, with an OS credential store, strict destination rules, cost caps and health checks. A first-party global IP network is a separate capital and compliance project | Network ownership and sourcing evidence, per-region availability, spend cap and independent route tests; no proxy-rotation or ban-evasion guarantee |
| Multiple customers and teams | Single trusted installation | Add separate accounts, encrypted tenant storage, project roles, quotas, audit log, deletion and backup/restore | Tenant-isolation tests, external security assessment, recovery exercise and published operational ownership |
| Hosted managed service | Not implemented | Only after the multi-tenant security model: account isolation, durable regional workers, public API, queues, billing, observability and support operations | Production load test, independent security review, incident runbook, tested backups and an honestly scoped service commitment |
| API and assistant access | Nine MCP tools in source | Finish connection in each client; then versioned REST API, OpenAPI contract, SDK examples and webhook events | Per-client integration test and public API uptime/error measurements |
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

1. Create the signed, permissioned, multi-layout gold-set evaluator and surface
   coverage, per-field accuracy, confidence intervals and failure causes in the
   workspace.
2. Replace the current one-shot render with a local visible session workflow:
   owner login, MFA, explicit human challenge handling, encrypted site-bound
   session, expiry, scope and revocation. Keep all sessions off the server and
   out of chat.
3. Add recipe drift detection and a reviewed repair proposal. Do not silently
   change a schema or mark a blocked page as an extraction success.

Only after these gates should we scope customer-owned proxies, first-party
network infrastructure, OCR, teams and managed hosting. This sequence delivers
quality and trust before buying scale.
