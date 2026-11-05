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

## Firecrawl spot check: 5 of 30 URLs, hand-scored (partial, not the full comparison)

Firecrawl's keyless hosted scrape (markdown, maxAge 0) was called once per URL on 2026-11-05, no key, free tier.
Its output arrives inline and cannot be saved by script, so I scored these five by reading it against the same
reference checks. Time and memory could not be measured for Firecrawl. The other 25 URLs are not measured.

| URL | rainbo-scrape 0.2.0 | Firecrawl | Result |
|---|---|---|---|
| shop-1 books.toscrape product | 6/6 | 6/6 (duplicated description, breadcrumb leaked) | tie |
| page-2 quotes.toscrape page 2 | 2/3 ("Login" link kept) | 2/3 (same) | tie |
| spa-1 quotes JS page | 3/3 | 3/3 | tie |
| spa-4 JS rendering page | 2/3 ("Enable JavaScript" text kept) | 2/3 (same) | tie |
| table-3 datatables.net | 4/4 | 3/4 (the Tiger Nixon row exists only inside a raw-HTML code dump, not as a table row) | win |
| Total | 17/19 | 16/19 | 1 win, 4 ties, 0 losses |

Firecrawl's datatables output also carried a roughly 14,000-character raw-HTML dump and about 150 sidebar links.
Five pages is too few to claim leadership; it shows we are level on these and ahead on one.

## Still not measured

The remaining 25 URLs for Firecrawl, any Firecrawl or Tavily latency, Tavily search quality (we have no web
search), and Firecrawl's behaviour on the four bot-wall URLs.
