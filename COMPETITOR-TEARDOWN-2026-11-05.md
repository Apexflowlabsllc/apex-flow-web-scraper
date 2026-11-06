# Competitor teardown, 5 November 2026

Method: every page below was fetched with `rainbo-scrape` itself (no Firecrawl,
no hosted scraping API). Pages are the vendors' own public docs and pricing,
plus the GitHub REST search API and the Hacker News Algolia API for developer
sentiment. Where a vendor page blocked us or returned too little, that is
stated. Figures are vendor claims unless marked "measured". Reddit and X were
not read: both disallow automated fetches in robots.txt and we obey robots.txt.

## Firecrawl (the incumbent)

Sources: docs.firecrawl.dev (introduction, scrape, crawl, map, search, extract,
agent, change-tracking, fast-scraping, enhanced-mode, batch-scrape, proxies),
firecrawl.dev/pricing, firecrawl.dev/alexandria, github.com/firecrawl/firecrawl,
api.github.com repo + issue search. Read 2026-11-05.

| Area | What they ship |
|---|---|
| Endpoints | scrape, crawl, map, search, batch scrape, extract, agent, interact (browser session), parse (local files), monitor |
| Output formats | markdown, html, rawHtml, screenshot, links, images, summary, changeTracking, branding, json (schema), query (question answering), audio |
| Caching | `maxAge` in ms, default 172,800,000 (2 days); `maxAge: 0` forces fresh; `storeInCache: false`. They claim "up to 500% faster" on cache hits |
| Anti-bot | `proxy`: basic / enhanced / auto (basic first, retry on enhanced); `proxy` field is now deprecated in favour of auto |
| Change tracking | `changeTracking` format with git-diff and JSON modes |
| Data catalogue | "Alexandria": 465 catalogued providers (public records 213, company 93, retail 66, finance 40 ...) callable through scrape/search (needs key) |
| Pricing | Free 1,000 credits/mo, 2 concurrent. Hobby $16/mo (5k), Standard $83 (100k, 25 concurrent), Growth $333 (500k), Scale $599 (1M, 100 concurrent). 1 credit = 1 basic page; search 2 credits per 10 results; JSON/Question/Highlight formats +4 credits per page; interact 2 credits per browser minute |
| Integration | Python, Node, Go, CLI, MCP server (github.com/mendableai/firecrawl-mcp-server: 7,765 stars, MIT) |
| License | Core repo AGPL-3.0, 198,929 stars, 10,487 forks, 753 open issues (GitHub API, 2026-11-05) |
| Funding | $75M Series B announced on their banner and on HN (2026-09-22) |

What developers complain about (open issues, GitHub API, 2026-11-05):
- A 7-reaction issue still asks for an identifiable user agent so sites can block the crawler (2025-02, 18 comments).
- /map misses sitemaps whose robots.txt URL does not end in .xml and stops after 25 sitemap files (2026-11-02).
- Change tracking reports "same" when content reorders characters (2026-10-05).
- Intermittent 409 on `interact` after a scrape (2026-10-26).
- /scrape returns four different line counts for the same PDF bytes over twelve days (2026-09-21): non-deterministic parsing.
- JSON format silently returns empty on some OpenAI-compatible backends (2026-08-06).
- HN: "Firecrawl getting blocked due to headlesness" (2025-12), "I replaced Firecrawl with 2,700 lines of Elixir" (2026-03), and four "Firecrawl alternative" Show HNs in 2026 (Draco single-binary Rust, FastCRW 6 MB binary, Extractor.sh, Firecrawl-Simple). The self-host fork demand is real: people want a small, fast, cheap, self-hosted scraper.

## Crawl4AI

Sources: docs.crawl4ai.com, crawl4ai.com, github.com/unclecode/crawl4ai, GitHub API. Read 2026-11-05.
- Apache-2.0, 86,953 stars (API) / 71,073 shown on site, 1.9M downloads/month claimed, 311 open issues.
- Python library, Docker server, and (new) hosted Cloud: `/scrape`, `/extract`, `/search`, `/answer`, `/serp` (Google SERP as data). Free tier 1,000 credits/month, 10 requests/min, 2 at a time. Page 0.5 credit, archived page half price.
- Strengths: fit-markdown (BM25/pruning content filters), adaptive crawling ("knows when to stop"), session reuse, hooks, stealth, proxies, deep crawl, MCP tools.
- Complaints (open issues): memory leak on repeated /md requests in Docker (10 reactions, top issue), markdown export loses heading hierarchy and table structure, PruningContentFilter drops whitespace in `<pre>/<code>`, MCP scrape lacks `wait_until`/SPA support, Python 3.14 incompatibility, scroll_full_page keeps only the last elements.

## Jina Reader

Source: jina.ai/reader. Read 2026-11-05. `r.jina.ai/<url>` returns LLM-ready text, `s.jina.ai` search, `mcp.jina.ai`. Options: browser engine, return format, JSON, timeout, token budget, OCR via jina-ocr-v1 ("costs 40x more tokens"). Simplicity (prefix a URL) is its moat. Their own host disallows crawling through robots.txt (we obeyed it and did not fetch through r.jina.ai).

## Spider.cloud

Source: spider.cloud. Read 2026-11-05. Claims "100K+ pages per second", own browser engine, 215M+ residential exits in 199 countries, 100% browser fidelity on its own 80-task bench (2026-09-18). Pages stream as markdown. Vendor-measured only.

## Apify

Sources: apify.com/pricing, apify.com/apify/website-content-crawler. Read 2026-11-05 (both pages failed on the first attempt because of a 8 KB response-header limit in our HTTP client; fixed in this release, see BENCHMARK). Marketplace of "Actors"; $0.20 per compute unit on Free ($5 credit), $0.16 on $199, $0.13 on $999. Website Content Crawler is the standard RAG crawler. Strength: thousands of prebuilt site-specific scrapers.

## Cloudflare Browser Run `/crawl`

Source: developers.cloudflare.com (page dated 2026-11-03). New entrant. POST a URL, get a job id, results as HTML/Markdown/JSON, jobs up to 7 days, results kept 14 days. Distribution advantage: it sits where the sites already are.

## Zyte API, ScrapingBee, Scrapfly, Bright Data, Oxylabs

- Zyte: from $0.06 per 1,000 successful HTTP responses up to $1.27 (tiered by site difficulty); browser-rendered priced separately; pay for success only; Proxyway benchmark cited by vendor.
- ScrapingBee: credits; JS rendering 5 credits, premium proxy 10 or 25.
- Scrapfly: $0.15 per 1,000 credits prepaid, 1,000 free credits.
- Bright Data: "pay only for successful results", $1.5 per 1,000 records on prebuilt scrapers, 60% launch discount banner.
- Oxylabs: SOC 2 Type II on Scrape endpoint and Web Unblocker. Product page returned 175 chars to us (client-side rendered); no price extracted.

## Search-first APIs: Exa, Tavily, Diffbot, Olostep, ScrapeGraphAI, Hyperbrowser, Browserless

- Tavily: 1,000 free credits/month, $0.008 per credit pay as you go; search/extract/crawl/research; PII blocking in queries.
- Exa: pricing page returned 578 chars (client-rendered); not extracted.
- Diffbot: 1 credit per extracted page, 25 per Knowledge Graph entity; automatic article/product extraction is its edge.
- Olostep: batches and crawls with markdown retrieval.
- ScrapeGraphAI: LLM-schema scraping; the home page served an obfuscated text blob that no HTML-text parser can read (our parser returned it as 108,898 chars of noise, a gap we log).
- Hyperbrowser: browser sessions for agents. Browserless: managed browsers, $0 / $25 plans, residential proxy 6 units/MB.

## Where the market is weak (our openings)

1. Everyone gates quality behind keys and credits; the open-source leaders (Crawl4AI, Firecrawl core) are heavy (browsers, Docker, Redis) and leak memory.
2. Markdown fidelity is the loudest, most measurable complaint (tables, headings, code blocks).
3. Non-deterministic or silent-failure behaviour (PDF line counts, JSON empty, change tracking "same").
4. No competitor publishes a reproducible benchmark that anyone can re-run. We will ship one.
5. Self-hosters want a small, single-binary-style, honest scraper that obeys robots.txt and identifies itself.

## Feature gaps found in `rainbo-scrape` before this release (from reading our own code)

| Gap | Competitor reference |
|---|---|
| Header-size limit (8 KB) broke Apify pages | n/a (our bug, measured) |
| No `maxAge` cache control per request | Firecrawl `maxAge` |
| No map (URL discovery via sitemap + links) | Firecrawl /map |
| Main-content detection removed `<header>` (lost H1s) and could not pick the densest block | Crawl4AI fit-markdown, Jina |
| No JSON-schema LLM extraction | Firecrawl json, Crawl4AI /extract |
| No change-tracking against previous scrape of the same URL | Firecrawl changeTracking |
| No screenshot | Firecrawl screenshot |
| No DOCX parsing | Firecrawl parse |
| No web search | Firecrawl, Tavily, Exa, Crawl4AI |
| Parser subprocess per page adds startup latency | measured in BENCHMARK |
| CLI printed multi-line JSON, job id not script-parseable | n/a |
