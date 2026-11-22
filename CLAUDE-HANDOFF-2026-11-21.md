# Claude Code handoff: Apex Flow Web Scraper

**Prepared:** 2026-11-21 (Pacific)  
**Project:** `E:\apex\work\codex-brief-20261121\scraper`  
**Branch:** `codex/handoff-2026-11-21-scraper`  
**Head:** `f39ba8babb47451402582c215b239b955b27afc1`  
**Pull request:** [#34 — Upgrade Web Scraper hero and AI distribution](https://github.com/Apexflowlabsllc/apex-flow-web-scraper/pull/34)

## Assignment

Review the current branch as an experienced product and systems engineer. Help turn Apex Flow Web Scraper into a dependable, profitable developer product that competes credibly with Firecrawl. Check the actual code, current PR and existing parity documents before recommending changes. Be specific about what is working, what is incomplete, and what the next best implementation is. Do not claim “number one” or full Firecrawl parity without comparative evidence.

The user wants a visually distinctive, highly animated crawler product, broader access from AI tools, customer growth and revenue. Keep the website concise and useful; the user has specifically objected to empty space and vague “crawling the business” imagery. The hero should communicate the page-to-data extraction process.

## Current verified state

- PR #34 is **open, draft, and mergeable** at the head above. GitHub reported **no checks** for the branch on the last check.
- No production deployment or package publication has occurred. These branch changes are not live in production.
- Full Python suite: **191 passed, 22 warnings** at this head. Focused billing and compatibility suites: **23 passed**. `node --check site/dashboard.js`, OpenAPI JSON parsing, and `git diff --check` passed.
- Current committed changes include dashboard API-key controls; create/list/revoke REST endpoints; one-time key display; a 25-active-key limit; and a guard requiring a replacement key before revoking the last active one.
- Credit usage now supports account and per-key historical views. New usage is attributed using an opaque stable key ID; old ledger entries are preserved and returned as unattributed.
- Earlier work on this branch includes Firecrawl-style document parsing, crawl active listing, v2 credit usage, schema-based extraction, bounded browser sessions, crawl filters and errors, SDK/CLI/Agent Skill work, and the animated product hero. See the existing parity matrix and PR body for the full list and limits.
- The dashboard key panel is collapsed by default and keeps the key in the current tab's memory. A new secret is shown once and can be copied by an explicit user action.
- Production Vercel environment variable **names only** were inspected without decrypting values. The current production list showed `SCRAPER_GATEWAY_SECRET` and `STRIPE_SECRET_KEY`; no search-provider key was present. Filtering the PR branch environment returned no variables. Do not request or reveal secret values.

## Highest-value gaps

1. **Search:** `/v1/websearch` and v2 search require an operator-configured provider. No search-provider environment variable is currently configured. Propose a reliable, lawful provider path, cost controls and fallback/error behavior; do not silently rely on a public service that may prohibit or rate-limit commercial use.
2. **AI reach:** 15 MCP installer targets and Python/Node clients exist, but setup files do not prove that each integration works. Prioritize clean-install and first-call tests for the highest-value clients, then add distribution listings only with verified packaging.
3. **Browser capability:** interactive sessions are short-lived and process-local, limited to two browser slots per process, and do not expose CDP/live streaming, persistent profiles or authenticated login. Durable multi-worker session affinity is still missing.
4. **Research and extraction breadth:** autonomous agent runs/traces, research/developer/government indexes, broad provider catalogs, image OCR, audio/video extraction, and PII redaction are not implemented.
5. **Product readiness:** there is no proven head-to-head quality result, production load/uptime evidence, public package release, or verified revenue from this branch. The current deployment has not been updated from this PR.

## Review these changes closely

- `src/rainbo_scrape/billing.py`: additive SQLite migration for key IDs and per-key usage; context-local request attribution.
- `src/rainbo_scrape/api.py`: auth middleware, key create/list/revoke routes, account ownership and last-key protection.
- `src/rainbo_scrape/hosted_firecrawl_compat.py`: current and historical usage response shapes.
- `site/dashboard.html`, `site/dashboard.js`, `site/app.css`: collapsed key panel, one-time secret display and revoke flow.
- `site/openapi.json`, `site/docs.html`, `site/firecrawl-compat.html`: public contract and guidance.
- Tests: `tests/test_billing.py` and `tests/test_firecrawl_compat.py`.

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
