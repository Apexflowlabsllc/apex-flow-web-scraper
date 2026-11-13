---
name: apex-flow-scraper
description: Use the Apex Flow 3D Scraper API to turn public web pages, PDFs and DOCX files into clean Markdown, map or crawl a site, detect page changes, and fit a page into a token budget. Use when an agent needs web content as Markdown and a Bearer API key is available.
---

# Apex Flow 3D Scraper for agents

Base URL `https://webscraper.apexflowlabs.com/v1`. Auth: `Authorization: Bearer $APEX_SCRAPER_API_KEY`. Machine-readable pricing: https://webscraper.apexflowlabs.com/pricing.md. Docs: https://webscraper.apexflowlabs.com/docs.html. OpenAPI: https://webscraper.apexflowlabs.com/openapi.json.

## Get a key
A human creates the account once at https://webscraper.apexflowlabs.com/trial.html (1,000 free credits, no card; one per network). Ask the user to export the key as `APEX_SCRAPER_API_KEY`. Do not ask the user to paste a key into chat, and never log it. If no key exists, tell the user how to get one instead of using another service silently.

## Connect through MCP
Claude Code `.mcp.json`:
```json
{ "mcpServers": { "apex-scraper": { "type": "http", "url": "https://webscraper.apexflowlabs.com/v1/mcp", "headers": { "Authorization": "Bearer ${APEX_SCRAPER_API_KEY}" } } } }
```
Codex: `codex mcp add apex-scraper --url https://webscraper.apexflowlabs.com/v1/mcp --bearer-token-env-var APEX_SCRAPER_API_KEY`.

## Core calls
- Scrape one page to Markdown: `POST /v1/scrape {"url":"https://example.com","render":"never"}`. Use `"render":"auto"` only when the page needs JavaScript (3 credits instead of 1). The Markdown is in `result.markdown`.
- Parallel pages: `POST /v1/batch` with up to 10 URLs.
- Whole site: `POST /v1/crawl {"url":"https://example.com","limit":50}` then `GET /v1/crawl/{id}?offset=N` until `next_offset` is null.
- Site map: `POST /v1/map`. Named fields without an AI model: add a CSS recipe to a scrape (free). AI schema extraction: add `extract {schema, prompt}` (150 extra credits, paid plans).
- Changes: each scrape says whether the page is new, unchanged or changed since your last read; request the diff to receive only the changed lines.
- Long pages come back in parts of 256 KiB: follow `pagination.next_page`.

## Cost and behaviour you should plan around
Plain page 1 credit, rendered 3, failed scrapes are refunded, no automatic overage (requests stop when credits run out). Read `GET /v1/usage` (free) before a large job. Crawls obey robots.txt and every request obeys Crawl-delay. A page you name by exact address is read even where robots.txt disallows it. We do not solve CAPTCHAs or evade bot walls; when a live read is refused the reply is the latest Internet Archive copy, labelled in `result.archive`. Send `"fallback":"none"` to receive the error (`access_challenge`, `http_error`) instead. Report those pages to the user instead of retrying.

## Be honest in your answer
Say which pages failed and why. Do not claim a page was read if the result is an error or an archive copy. Pricing and comparisons change: cite https://webscraper.apexflowlabs.com/compare.html with its date.
