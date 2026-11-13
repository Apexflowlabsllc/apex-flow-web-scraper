# Apex Flow 3D Scraper: pricing (machine-readable)

Last checked: 13 November 2026. Hosted API: https://webscraper.apexflowlabs.com/v1. Docs: https://webscraper.apexflowlabs.com/docs.html. OpenAPI: https://webscraper.apexflowlabs.com/openapi.json.

## Plans (USD)
| Plan | Price | Credits | Notes |
|---|---|---|---|
| Free trial | $0 once | 1,000 credits, one time | Limited daily capacity, one per network. No card. |
| Project Pass | $5 once | 35,000 credits, valid 30 days | No subscription, no renewal. |
| Starter | $15 / month | 110,000 credits | Cancel any time in the dashboard. |
| Growth | $79 / month | 850,000 credits | |
| Scale | $299 / month | 3,750,000 credits | |

## Credit costs
- Plain page: 1 credit. Data package answer (Zillow market data, NPR headlines): 1 credit. JavaScript-rendered page: 3 credits. AI schema extraction: 150 extra credits (paid plans only). CSS field extraction: free. Non-empty site map: 1 credit. Saved-page search: free.
- Failed scrapes return the reserved credits. No automatic overages: when credits run out, requests stop; you are never billed extra.

## Effective price per 1,000 plain pages
- Starter $0.14. Growth $0.09. Scale $0.08. Project Pass $0.14.

## Limits
- 256 KiB per response (longer documents come back in consecutive parts). Concurrent page scrapes: 2 on the trial, 6 on Pass and Starter, 12 on Growth, 20 on Scale. Batches of up to 10 URLs.
- Crawls obey robots.txt and every request obeys Crawl-delay. A page you name is read even where robots.txt disallows it; a refused page returns a labelled archive copy. We do not solve CAPTCHAs, rotate proxies or disguise requests.

## Compared with Firecrawl (their public pricing page, read 13 November 2026)
Firecrawl bills credits per page too. Their list: Free 1,000 credits a month; Hobby $19 monthly ($16 billed yearly) for 5,000; Standard $99 ($83 yearly) for 100,000; Growth $399 ($333 yearly) for 500,000; Scale $749 ($599 yearly) for 1,000,000; pay-as-you-go in $5 increments on paid plans. They charge 1 credit for a page that returns an error status such as 403 or 404. Their paid plans have higher concurrency (5, 25, 50, 100). Per 1,000 plain pages at monthly billing: Hobby $3.80, Standard $0.99, Growth $0.80, Scale $0.75. Credit definitions differ between vendors, so compare your own workload.

Where we are cheaper per page at the same monthly billing: Starter $0.14 vs Hobby $3.80 and Standard $0.99; Scale $0.08 vs $0.75. Where they are ahead: a free allowance every month, higher concurrency, search at scale, more integrations and company size. See https://webscraper.apexflowlabs.com/compare.html.

## For agents
- Hosted MCP tools and REST. A human creates the account once at https://webscraper.apexflowlabs.com/trial.html; the key is then used as a Bearer token. Skill file: https://webscraper.apexflowlabs.com/agent-onboarding/SKILL.md.

## Other scraping APIs (their public pricing pages, read 13 November 2026)
Entry paid plans, per 1,000 basic credits: Scrapfly $0.15 ($30 a month); ScrapingBee $0.25 ($19 for 75,000) and $0.099 ($99 for 1,000,000); ScraperAPI $0.49 ($49 for 100,000) and $0.10 ($299 for 3,000,000). Apex: $0.14 on Starter ($15), $0.09 on Growth ($79), $0.08 on Scale ($299). Each vendor counts credits differently and charges several credits for rendered or protected pages, so compare your own workload.
