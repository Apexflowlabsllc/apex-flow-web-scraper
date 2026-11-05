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

## Firecrawl comparison: NOT measured

A one-time Firecrawl free-tier baseline was attempted. Its MCP returns Markdown inline only and cannot be
saved to disk without retyping, so only 2 of 30 pages could be scored and those copies were unreliable. They
were discarded. No Firecrawl number appears in this file, and no win over Firecrawl is claimed. Firecrawl's
open issues (see COMPETITOR-TEARDOWN) are the only evidence we cite. Closing this needs the same 30 URLs run
by a script against Firecrawl, which the "no outside key" rule excludes, or a manual copy by Brian.
