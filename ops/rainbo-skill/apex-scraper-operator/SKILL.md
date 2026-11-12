---
name: apex-scraper-operator
description: "Run, protect and grow Apex Flow 3D Scraper (webscraper.apexflowlabs.com), the paid web-scraping API store, without Brian: daily numbers, health, safe releases, rollback, money rules and the growth loop."
trigger: Use for anything about the web scraper store, webscraper.apexflowlabs.com, the scraper API, its sales, trials, credits, customers, outages, releases, pricing, listings, Firecrawl comparison, or when a rainbo-alert has where=apex-webscraper.
category: apex-sop
---

# Apex Flow 3D Scraper operator. You run this store. Brian does not.

One job: money in, service up, nothing lost. Measure before you speak ([[vendor-truth]]). Server access rules are in [[rainbo-box-ops]].

## 1. What it is
- A paid API that turns public web pages into clean Markdown for developers and AI agents. Site: https://webscraper.apexflowlabs.com. Code: github.com/Apexflowlabsllc/apex-flow-web-scraper (public, MIT).
- Prices: 1,000 free trial credits once; $5 one-time pass (5,000 credits, 30 days); $15 / $79 / $299 a month for 25,000 / 150,000 / 750,000 credits. Plain page 1 credit, rendered page 3, AI extraction 25 (paid plans only). Failed pages are free. No overages.
- It runs on this server as systemd service `apex-webscraper` (port 8850, behind Caddy at webscraper-api.159.223.109.53.nip.io). The website is the Vercel project `apex-flow-web-scraper`. Payments are Stripe, shared with other Apex businesses: never touch Stripe objects that are not tagged product=apex-flow-web-scraper.

## 2. Every day (already automated; you read and act)
- A root timer runs `/opt/apex-webscraper/ops/apex_scraper_ops.py daily` at 03:40 UTC. It backs up the customer database (14 days kept in /opt/apex-webscraper/backups) and writes `~/rainbo-reports/apex-scraper/latest.md` and `latest.json`.
- Read `latest.json` each morning. Report to Brian in three lines: paid orders and monthly recurring revenue, trials in the last 7 days, anything broken. A started checkout is not a sale. Only `paid_orders_total` and `paid_accounts_active` are money.
- For live numbers at any time: `sudo python3 /opt/apex-webscraper/ops/apex_scraper_ops.py report` (read-only).
- A `new_paid_order` alert means a customer paid. Tell Brian the same hour.

## 3. Health (already automated)
- A root timer runs the `health` command every 5 minutes. If the API is down it restarts it and files an alert in `~/rainbo-alerts` with where=apex-webscraper.
- `auto_recovered`: nothing to do; mention it in the daily report.
- `service_down`: the restart failed. Go to section 5, Rollback. Then tell Brian what happened and what you did.
- `site_unreachable`: the server is fine but the public site fails. Check the Vercel deployment and `systemctl is-active caddy`. Do not restart the API for this.
- `disk_nearly_full`: delete old release folders in /opt/apex-webscraper/releases except the one in use and the one before it. Never delete /var/lib/apex-webscraper.
- Check by hand: `curl -s https://webscraper.apexflowlabs.com/api/status` must return `"ok": true`.

## 4. Releasing a change (the only way)
1. Change code in the GitHub repo on a branch. Run the tests: every Python and Node test must pass. Open a pull request and merge it.
2. API: upload `git archive HEAD src pyproject.toml` to `/opt/apex-webscraper/releases/<name>.tar`, unpack to `/opt/apex-webscraper/releases/<name>/`.
3. Test on a COPY of the database first (see the stage check used in earlier releases). Never test against the live database.
4. Back up: SQLite backup of `/var/lib/apex-webscraper/billing.db` into `/opt/apex-webscraper/backups`.
5. Switch: write a new drop-in in `/etc/systemd/system/apex-webscraper.service.d/` containing `Environment=PYTHONPATH=<release>/src`. The file that sorts LAST alphabetically wins, so name it after the newest existing one. Then `systemctl daemon-reload && systemctl restart apex-webscraper`.
6. Verify within 20 seconds: local `/api/status` shows the new version. If not, roll back at once (section 5).
7. Verify the changed behaviour through the public address with a labelled internal QA account that you revoke afterwards (pattern: `scripts/verify-hosted.py` in the repo). Internal QA accounts use the email internal-qa@apexflowlabs.com so reports exclude them.
8. Website: from `site/`, `vercel deploy --prod --yes --scope apex-flow-labs`. It does NOT deploy on merge.
9. MCP Registry: bump `server.json`, then `gh workflow run publish-mcp.yml --ref main`.

## 5. Rollback (one minute)
Delete the newest drop-in in `/etc/systemd/system/apex-webscraper.service.d/`, then `systemctl daemon-reload && systemctl restart apex-webscraper`, then check `/api/status`. The previous release starts. The database is forward-compatible; do not restore a backup unless data is actually damaged, and ask Brian first.

## 6. Money rules (never break these)
- Never turn on a feature that pays a vendor per request unless its credit price is above its worst-case cost on the cheapest plan. AI extraction is 25 credits, paid plans only, 20,000 characters per call, 3,000 calls a day service-wide. Web search stays off until a search key exists and it is priced above that key's per-query cost.
- Never raise the free trial (1,000 credits, 20 new trials a day, one per network).
- Never lower prices, issue refunds, change Stripe settings, or spend money without Brian saying yes to that exact action.
- Never add CAPTCHA solving, disguised requests or proxy rotation. The store obeys robots.txt and site blocks; the archive fallback is the answer for blocked pages.
- Never claim "number one", user counts, or a win over a competitor that you have not measured. Published comparisons must give the date and method.

## 7. Customers
- Access problems: the customer opens the dashboard in the browser where they bought; the key is re-shown there. A lost trial key cannot be re-issued; they start a paid plan.
- Out of credits: point to https://webscraper.apexflowlabs.com/#pricing. Billing changes and cancellation: the "Manage billing" button in the dashboard (Stripe portal).
- A page that will not scrape: ask for the URL and the error `code`. `robots_denied`, `access_challenge` and `http_error` are the site refusing; suggest `"fallback":"archive"`. Anything else, reproduce it, then fix it through section 4.
- Refund or dispute requests go to Brian the same day with the order id.

## 8. Growth loop (the store has almost no visitors; this is the real work)
Baseline on 12 November 2026: 33 page views in 7 days, 0 real trials, 0 paid orders. The product is ahead of its traffic. Each week, in this order, and measure trials after each step:
1. **Be listed where developers pick tools.** The official MCP Registry entry exists. A pull request to punkpeye/awesome-mcp-servers is open (number 19351): check it, answer reviewers, fix what they ask. Then submit to awesome-remote-mcp-servers, Glama, Smithery, PulseMCP and mcp.so, one a day, using the README text. Each needs Brian's yes the first time because it posts under his name.
2. **Answer pages** ([[apex-get-found]]): one page per real question, answer in the first 50 words. Start with: "Firecrawl alternative with a one-time price", "how to track changes on a web page with an API", "how to fit a web page into an LLM context window", "scrape a whole site to Markdown for RAG", "MCP server for web scraping". Add each to `site/` and `sitemap.xml`, deploy, ping IndexNow.
3. **Proof.** Re-run the benchmark in `tests/benchmark/` monthly and update `site/compare.html` with the date and method. Real numbers only.
4. **Show it working.** One short demo per week (a real crawl, a real change alert, a real agent session) for the Apex channels, linking to the free trial.
5. **Turn trials into buyers.** Read the funnel: page views to trials to checkouts to paid. Fix the worst step first. A trial that used more than 500 credits and did not buy is the first person to learn from.
6. **Never buy ads or traffic** without Brian approving the amount.

## 9. What still needs Brian
Anything that spends money, changes prices, refunds a customer, posts under his name on a new site for the first time, or signs a contract. Everything else in this file is yours.
