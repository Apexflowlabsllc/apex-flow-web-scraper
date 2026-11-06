# Benchmark, 5 November 2026

Harness: `tests/benchmark/` (30 public URLs in `urls.json`, 94 hand-chosen reference checks in
`references.json`, scorer `score.py`). Each URL runs in a fresh process, sequentially, cache off, on one
Windows 11 machine over a home connection. Re-run: `python tests/benchmark/run_benchmark.py LABEL`.
Raw per-URL numbers: `tests/benchmark/results-0.2.0.json` (ours) and `results-0.1.0.json` (before).

## Headline (rainbo-scrape only)

| | 0.1.0 + header fix (before) | 0.2.0 (after) |
|---|---|---|
| URLs fetched OK | 25 / 30 | 26 / 30 |
| Reference checks passed (headings as headings, tables, code, phrases, no chrome) | 89 / 94 (94.7%) | 92 / 94 (97.9%) |
| Median time per URL | 2,062 ms | 1,454 ms |
| Mean time per URL | 4,602 ms | 2,170 ms |
| Peak memory of the scrape process (median / max) | 56.1 / 71.1 MB | 56.4 / 82.1 MB |

Memory is the Python process only; the parser subprocess and Brave are not included.

## Fixed in this release, each measured

- news.ycombinator.com 31,141 ms to 1,156 ms: Crawl-delay: 30 stalled the first page.
- apify.com pages failed (8 KB header limit); now fetch. cnn.com failed (TLS roots, then 12 MB ceiling); now 11.5k chars in about 1.6 s.
- books.toscrape.com listing 53 chars to 991; headings wrapped in links now stay headings; tables and code blocks keep structure.
- arXiv PDF 17.3 s (first run, network variance) to 2.4 s.

## Not won, with reasons

- 4 URLs not fetched: text.npr.org and reuters.com (robots.txt disallows us; we obey it), zillow.com and glassdoor.com (HTTP 403 bot walls; we do not evade or solve challenges).
- spa-4 (scrapingcourse JS page) keeps the text "Enable JavaScript to see products"; page-2 keeps a "Login" link. 2 of 94 checks fail.
- Rendered pages cost 5 to 6.7 s (Brave start plus network-quiet wait).

## Firecrawl comparison: 29 of 30 URLs, free keyless tier, scored against the same checks

Firecrawl's keyless hosted scrape (markdown, onlyMainContent, maxAge 0) was called once per URL on 2026-11-05: no key,
no retries, no proxy options. Its output arrives inline, so most of it was scored by reading it against the same
reference checks (score.py's rules); docs-1, docs-3, table-1, table-2 and consent-2 were scored with score.py itself.
Hand-scoring is a weaker method than a script, so treat single-check differences with care. bot-1 is left out (our
reference exists, theirs was not scored). Firecrawl time and memory were not measured.

| Result over the 24 URLs with reference checks (bot-1 excluded) | rainbo-scrape 0.2.0 | Firecrawl |
|---|---|---|
| Checks passed | 91 / 93 | 85 / 93 |
| URLs where we score higher | 6 (news-2, docs-1, docs-2, shop-3, consent-1, table-3) | 0 |
| URLs tied | 18 | 18 |
| URLs where Firecrawl scores higher | 0 | |

What Firecrawl missed: bold link text instead of a heading line (news-2, shop-3, consent-1), "Navigation" kept
(docs-1), "Skip to main content" kept (docs-2), a raw-HTML dump instead of a table row (table-3).

## Where Firecrawl beats us, measured

- **Coverage: Firecrawl returned content for all 30 URLs; we fetched 26.** The 4 we did not fetch:
  text.npr.org and reuters.com (robots.txt disallows us and we obey it; Firecrawl returned real content for both),
  zillow.com and glassdoor.com (HTTP 403 to us). Firecrawl's Zillow output was almost entirely navigation and footer
  links with no listings; its Glassdoor output was the Recruiter marketing page. So it reached the front doors, not the data.
- Firecrawl's output is larger on several pages (docs-3: 513,362 characters against our 38,774, same 5/5 checks).

## Still not measured

Firecrawl and Tavily latency, memory, Tavily search quality (we have no web search), live schema extraction,
whether either service returns the same output from other networks, and anything at crawl scale.
