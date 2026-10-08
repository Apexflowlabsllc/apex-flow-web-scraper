# Access platform: proxy infrastructure and challenge handling

**Reviewed 8 October 2026.** This plan distinguishes documented competitor features from our implementation and proposed build. No source reviewed establishes universal access or a universal CAPTCHA success rate.

## What the four products document

- **Firecrawl:** its Enhanced Mode describes `basic`, `enhanced`, and `auto` proxy modes. `auto` tries the basic route and may retry on 401, 403, or 429 with an enhanced route; a persistent block is returned to the caller. This is proxy escalation, not proof that every CAPTCHA is solved.
- **Apify:** provides managed proxy groups and sessions. Its documentation describes stable session identity tied to a proxy IP and cookie/session state. The network is a product and ongoing infrastructure expense, not a library feature.
- **Playwright:** provides browser-level or per-context proxy configuration and isolated browser contexts. It is a browser automation framework; a proxy service and challenge policy must be supplied separately.
- **Crawl4AI:** documents detection of block/challenge signals, bounded retries, and retries through configured proxies. Its marketplace separately lists CAPTCHA-solving integrations; treat those as external integrations, not proof that the core crawler natively grants access.

Official references: [Firecrawl Enhanced Mode](https://docs.firecrawl.dev/features/enhanced-mode), [Apify Proxy](https://docs.apify.com/proxy), [Playwright network](https://playwright.dev/python/docs/network), [Playwright context isolation](https://playwright.dev/docs/browser-contexts), [Crawl4AI anti-bot detection and fallback](https://docs.crawl4ai.com/advanced/anti-bot-and-fallback/), [Crawl4AI marketplace](https://docs.crawl4ai.com/marketplace/).

## Product position

Build **Apex Access Fabric** as an owned, transparent outbound networking and source-access control plane. Do not market CAPTCHA defeat. A challenge is an access decision from the target. The product must stop, report it, and offer the source owner's documented API or access process, or an owner-controlled human handoff when the destination permits it.

The value proposition is operator control and evidence: know which route served a result, what it cost, which tenant used it, what policy applied, and why a challenge stopped a job. No success claim without a dated benchmark on a representative, permitted source set.

## Infrastructure stages

1. **Current:** one operator-supplied HTTP proxy endpoint through `RAINBO_SCRAPE_PROXY_URL`. This is a routing hook. There is no Apex proxy fleet, region menu, tenant-level proxy UI, traffic billing or managed SLA.
2. **Private egress pilot:** launch a small number of Apex-controlled datacenter egress nodes in selected regions. Use a gateway/control plane, outbound-only workers, tenant-isolated credentials, per-job route selection, pinned sessions, per-origin quotas, cost ceilings, health probes, and auditable usage. Permit only approved public destinations and customer-authorized sources. Implement emergency deny/revoke controls and abuse response before adding customers.
3. **Multi-region service:** add regional capacity, failover, traffic metering, customer isolation, lawful-use onboarding, retention controls, incident response, and an external security review. Publish real capacity and pricing after measured use.
4. **Residential routes:** defer. Only consider endpoints with explicit, informed opt-in, clear compensation/consent terms, revocation, security controls, and jurisdictional review. No covert devices, repurposed consumer bandwidth, or shared credential collections.

Apex may own and control its gateway and rented/owned servers while paying upstream bandwidth or hosting costs. Call this “Apex-operated infrastructure” and disclose third-party hosting providers; do not claim every physical network link is owned by Apex.

## Challenge workflow

- Detect challenge pages using status, structural markers and page content. Keep a separate result state and never mark a challenge page as a successful extraction.
- Stop automatic retries when challenge signals appear. Proxy rotation is not a challenge bypass and must not be used to evade a block.
- Show the blocked URL, timestamp, challenge category, last route identifier (never credentials), and remediation options.
- Offer source-owner API/documentation, a permission contact path, or a paused job. A future local isolated browser profile may let the account owner sign in and complete permitted steps. Resume only after owner action and only if the source terms permit continued collection.
- Encrypt and scope any future session data by tenant and origin. Provide expiration, audit access, export restrictions and immediate revocation. Never copy cookies from the owner's everyday browser or ask for passwords in chat.
- For Apex-owned sites, integrate official challenge verification at the server, use test keys in development, and verify tokens server-side. Never treat a UI checkbox as proof of validation.

## Competitive edge we can prove

1. **Route provenance:** each result can state its route class, region, policy and cost without exposing IP secrets.
2. **Human-controlled challenge state:** explicit pause, owner action and resume audit; no fabricated pass/fail result.
3. **Predictable spend:** quotas and per-job maximum cost, with an operator-visible usage ledger.
4. **Tenant isolation:** route credentials and session state never appear in jobs, exports, model context or logs.
5. **Source policy:** per-domain allowlists, robots behavior, cadence limits and customer authorization records.
6. **Honest quality reporting:** page reach, field coverage, field accuracy and access-challenge rate remain separate metrics.
7. **No hidden dependency:** document hosting, bandwidth and upstream network providers; disclose costs and outages.

These are design goals until implemented and measured. Website copy must label them “planned” until their acceptance gates pass. Never promise universal access, CAPTCHA bypass, a guaranteed extraction rate, or “no competitor can match us.”

## Release gates

- Threat model and abuse policy approved before external service access.
- Proxy credentials stored outside job payloads and logs; authentication tests cover secret redaction.
- SSRF checks apply before both direct and proxied requests; redirects are checked again.
- Route isolation, cost caps, revoke controls, metering, alerts and emergency shutdown demonstrated.
- Challenge classification precision/recall reported against a labeled page set; no automatic solve action exists.
- Human handoff tested on sources where the operator has documented authorization; session data encrypted and revocable.
- Capacity, error rate, latency and cost published from a load test before announcing a managed fleet or SLA.
