# Apex Flow Web Scraper benchmark, 22 November 2026

## Method

Ran the repository's 30-URL benchmark against the current 0.11.0 branch code.
Each URL ran sequentially in a fresh Python process with caching disabled.
`render=never` was set, robots rules were obeyed, and no hosted API key or
paid search provider was used. Raw rows and extracted Markdown are saved under
`tests/benchmark/out/2026-11-22-final/` locally (the output directory is
gitignored).

Re-run from the repository root with:

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) "src")
.\.venv\Scripts\python.exe tests/benchmark/run_benchmark.py 2026-11-22-final --render never
.\.venv\Scripts\python.exe tests/benchmark/rescore.py 2026-11-22-final
```

## Results

- Retrieval succeeded for 27 of 30 URLs. Two were denied by `robots.txt`; one
  returned an HTTP error.
- Median fetch time among successful pages was 1.156 seconds.
- The scorer passed 83 of 94 checks. All 83 checks not dependent on browser
  rendering or dated headlines passed.
- Seven failed checks are expected in this no-render run: three JS-only pages
  returned application shells instead of rendered content.
- Four checks use dated news headlines that had changed on the source pages:
  two on Al Jazeera, one on BBC, and one on The Guardian. Refresh these
  references from an independent review before using that score as a quality
  claim.
- The prior `Login` header leak on Quotes to Scrape is fixed; page-2 now passes
  all three of its reference checks. The static/isolated regression test also
  confirms that real article text mentioning login is retained.

This is an Apex-only no-render run, not a Firecrawl head-to-head. It does not
measure JS rendering, search quality, large-crawl throughput, or equivalent
cost per correct field. The most recent cross-vendor comparison remains the
dated 5 November run in [BENCHMARK-2026-11-05.md](BENCHMARK-2026-11-05.md).
