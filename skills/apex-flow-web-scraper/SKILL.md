---
name: apex-flow-web-scraper
description: Read, map, crawl, and compare public webpages with Apex Flow Web Scraper. Use when an AI agent needs current website content, structured page data, crawl discovery, or source-backed web research.
license: MIT
compatibility: Requires Python 3.10+ and network access. APEX_SCRAPER_API_KEY must be set in the agent's environment; the key is never passed as a command-line argument.
metadata:
  author: Apexflowlabsllc
  version: "1.0.0"
---

# Apex Flow Web Scraper

Use the hosted Apex Flow API to read public pages as clean Markdown, discover site URLs, batch requests, and crawl bounded sections of a site. The API returns source URLs, retrieval metadata, clear errors, and archive-fallback labels.

## Install

Install this skill in a compatible agent from the public repository:

```bash
npx skills add Apexflowlabsllc/apex-flow-web-scraper --skill apex-flow-web-scraper
```

The repository must be public and this skill merged into its default branch for that command to work. Alternatively, copy this skill directory into the agent's supported skills folder. Check the agent's environment configuration for how to provide secrets, set `APEX_SCRAPER_API_KEY` there, and restart the agent. Never ask the user to paste the key into a prompt or command line.

## Read a page

Run the bundled standard-library CLI from this skill directory:

```bash
python scripts/apex_scraper.py scrape https://example.com --render auto
```

Use `--render never` for static pages or `--render always` when the page depends on JavaScript. For large pages, `--max-tokens 4000` returns a part that fits the requested context budget. The default output is JSON containing the result, credits, and retrieval receipt.

## Choose the smallest useful operation

- Exact page known: `scrape URL`.
- Several known URLs: `batch --url URL --url URL` (up to ten).
- Compare pages with their previous saved versions: `changes --url URL --url URL` (up to ten; returns changes-only diffs).
- Need URLs from one known site: `map URL --limit 100 --search "topic"`.
- Need a bounded set of pages from one site: `crawl URL --limit 25` and poll the returned job with `crawl-status JOB_ID --offset 0`; continue using `next_offset` until it is `null`.
- Need to find a URL on the open web: `web-search QUERY`. This endpoint depends on the hosted search provider being enabled; if it returns `not_configured`, use an available search tool and then pass its source URLs to `scrape`.
- Search pages already saved to this account: `saved-search QUERY`; this is not open-web search.
- Check remaining credits and account limits: `usage`.
- Schedule recurring change alerts: `monitor create URL --webhook-url HTTPS_URL --interval-seconds 86400 --confirm-recurring`; use `monitor list`, `monitor pause ID`, `monitor resume ID`, `monitor events ID`, and `monitor delete ID` to manage them. Creating a monitor spends credits on each successful check; only create one after the user explicitly authorizes recurring checks. Store the one-time signing secret securely and verify each signed webhook.

Commands accept `--help`. The base URL can be overridden with `APEX_SCRAPER_API_BASE` for an approved compatible deployment.

## Report results carefully

1. Cite the exact source URL returned by the API for every factual summary.
2. Distinguish a live page from an archived fallback using the returned `archive` metadata and warnings.
3. Preserve uncertainty, blocked pages, and extraction warnings. A successful HTTP response does not prove that every page element was captured.
4. Use CSS `recipe` extraction when stable, known selectors are available; use model-based `extract` only when the requested fields cannot be selected directly.
5. Crawl only the site and page count needed for the user's task. Respect site access controls; do not attempt CAPTCHA bypass, credential theft, or access to private pages.
6. Check `credits_left`, `retry_after`, `code`, and `buy_url` on errors before retrying or suggesting a paid plan.
7. Scheduled monitors run only at the requested cadence, obey robots.txt, and stop when their paid entitlement or credits end. Never create recurring work without explicit user consent.

Read the [signed scheduled monitoring guide](https://webscraper.apexflowlabs.com/monitoring.html) before configuring webhook verification.

The hosted service performs the fetch and rendering. The skill CLI makes authenticated HTTPS requests and has no third-party Python dependencies.
