"""Write the two data-package answer pages: a Zillow-data alternative and a Glassdoor-salary alternative.
Sample replies are real output of the packages (rent FY2026, pay May 2024). Run: python scripts/build-data-pages.py"""
import html
import json
from pathlib import Path

BASE = "https://webscraper.apexflowlabs.com"
e = lambda t: html.escape(str(t), quote=True)

RENT = {"region": "Phoenix-Mesa-Chandler, AZ MSA", "year": "FY2026", "monthly_rent": {"studio": 1457, "1_bedroom": 1583, "2_bedroom": 1839, "3_bedroom": 2452, "4_bedroom": 2720}}
PAY = {"region": "Seattle-Tacoma-Bellevue, WA", "jobs": [{"title": "Software Developers", "employed": 72730, "annual": {"median": 169340, "mean": 181040, "p10": 105060, "p25": 136010, "p75": 211590, "p90": "239200+"}}]}

PAGES = [
    dict(slug="zillow-data-alternative", title="Zillow data alternative: house prices, rents and mortgage rates by API",
         h1="Housing data for any US metro, <em>in one call.</em>",
         lede="Zillow's terms forbid automated reading and reuse of its pages, so scraping it is a dead end for a product. Our home package answers the same questions from data you may use and resell: the house price trend, monthly rent for a studio to a 4-bedroom, and this week's mortgage rates.",
         call=('curl https://webscraper.apexflowlabs.com/v1/packages/home \\\n  -H "Authorization: Bearer $APEX_SCRAPER_API_KEY" -H "Content-Type: application/json" \\\n  -d \'{"region":"Phoenix, AZ"}\''),
         sample=RENT, sample_label="Part of the reply (rents, real data)",
         rows=[("House price trend", "Latest quarter, 1-year and 5-year change, quarterly series", "Federal Housing Finance Agency (public domain)"),
               ("Rent by bedroom count", "Studio to 4 bedrooms, every metro and county area", "HUD Fair Market Rents FY2026 (public domain)"),
               ("Mortgage rates", "30- and 15-year fixed, this week, a year ago, last 8 weeks", "Freddie Mac PMMS (used with attribution)")],
         endpoints="Separate calls: <code>/v1/packages/housing</code>, <code>/v1/packages/rent</code>, <code>/v1/packages/mortgage-rates</code>. MCP tools: <code>home_report</code>, <code>rent_prices</code>, <code>mortgage_rates</code>, <code>house_prices</code>.",
         faq=[("Can I scrape Zillow with it?", "A Zillow page you name is fetched like any page, but Zillow's terms forbid automated use and reuse, and it often refuses automated readers. The home package gives you the numbers people go to Zillow for, from sources whose terms allow it."),
              ("Is this a Zestimate?", "No. The house price figure is an index of how prices in the metro moved (compare periods), not a value for one house. Rents are dollar figures by bedroom count."),
              ("What does it cost?", "One credit an answer: about $0.00014 on the $15 Starter plan. A list of possible place names, when your question matches more than one, is free.")]),
    dict(slug="glassdoor-salary-api", title="Glassdoor salary data alternative: pay by job and city by API",
         h1="Pay for any job, <em>in any US metro.</em>",
         lede="Glassdoor sits behind a bot wall and its terms forbid scraping. Our pay package returns pay for about 800 jobs in every US metro from the government's own employer survey, which you may use and resell: median, mean, the 10th to 90th percentile and how many people do the job.",
         call=('curl https://webscraper.apexflowlabs.com/v1/packages/pay \\\n  -H "Authorization: Bearer $APEX_SCRAPER_API_KEY" -H "Content-Type: application/json" \\\n  -d \'{"job":"software developer","region":"Seattle, WA"}\''),
         sample=PAY, sample_label="Part of the reply (real data)",
         rows=[("Annual pay", "Median, mean, 10th, 25th, 75th and 90th percentile", "BLS OEWS, May 2024 (public domain)"),
               ("Hourly median", "For every job with a published figure", "BLS OEWS, May 2024"),
               ("Jobs in the metro", "How many people hold the job there", "BLS OEWS, May 2024")],
         endpoints="Endpoint: <code>/v1/packages/pay</code> with <code>job</code>, <code>region</code> and optional <code>limit</code> (up to 20 closest job titles). MCP tool: <code>job_pay</code>.",
         faq=[("Is this the same as Glassdoor salaries?", "No. Glassdoor collects reports from employees; this is a survey of about 1.1 million employers run by the U.S. Bureau of Labor Statistics. It covers every metro and is not limited by how many people posted."),
              ("How current is it?", "May 2024, the latest release. The government publishes a new year every spring and we update the table when it does."),
              ("What does it cost?", "One credit an answer. A list of possible metro names, when your question matches more than one, is free.")]),
]


def page(p):
    rows = "".join(f"<tr><td>{a}</td><td>{b}</td><td>{c}</td></tr>" for a, b, c in p["rows"])
    faq = "".join(f"<details><summary>{e(q)}</summary><p>{e(a)}</p></details>" for q, a in p["faq"])
    ld = {"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [{"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in p["faq"]]}
    desc = p["lede"][:155]
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(p["title"])} · Apex Flow Web Scraper</title>
<meta name="description" content="{e(desc)}"><link rel="canonical" href="{BASE}/{p["slug"]}.html"><meta property="og:title" content="{e(p["title"])}"><meta property="og:description" content="{e(desc)}"><meta property="og:image" content="{BASE}/assets/og-crawl.jpg"><meta name="twitter:card" content="summary_large_image">
<link rel="stylesheet" href="/app.css"><link rel="stylesheet" href="/integrations/int.css"><script type="application/ld+json">{json.dumps(ld, ensure_ascii=False)}</script></head><body>
<nav aria-label="Primary"><a href="/">APEX FLOW WEB SCRAPER</a><span class="nav-links"><a href="/integrations/">Integrations</a><a href="/docs.html#packages">Docs</a><a href="/#pricing">Pricing</a><a class="button" href="/trial.html">Get a free key</a></span></nav><main>
<p class="eyebrow">Data packages</p><h1>{p["h1"]}</h1><p class="lede">{e(p["lede"])}</p>
<div class="code"><button type="button" class="copy" data-copy>Copy</button><pre><code>{e(p["call"])}</code></pre></div>
<h2>{e(p["sample_label"])}</h2><div class="code"><pre><code>{e(json.dumps(p["sample"], indent=2))}</code></pre></div>
<h2>What comes back</h2><div class="tablewrap"><table><thead><tr><th>Field</th><th>Detail</th><th>Source</th></tr></thead><tbody>{rows}</tbody></table></div>
<p>{p["endpoints"]} Every answer names its source and attribution.</p>
<h2>Questions</h2>{faq}
<p class="cta"><a class="button" href="/trial.html">Get 1,000 free credits →</a> <a class="button secondary" href="/docs.html#packages">Read the package docs</a></p>
</main><footer><span>Apex Flow Web Scraper, by Apex Flow Labs.</span><a href="/integrations/">Integrations</a><a href="/docs.html">Docs</a><a href="/compare.html">Compare</a><a href="/terms.html">Terms</a><a href="/privacy.html">Privacy</a></footer>
<script src="/integrations/int.js" defer></script><script src="/assets/cursor.js" defer></script></body></html>
'''


site = Path(__file__).resolve().parent.parent / "site"
for p in PAGES:
    (site / f"{p['slug']}.html").write_text(page(p), encoding="utf-8", newline="\n")
sm = site / "sitemap.xml"
t = sm.read_text(encoding="utf-8")
add = "".join(f"<url><loc>{BASE}/{p['slug']}.html</loc></url>" for p in PAGES if f"/{p['slug']}.html<" not in t)
sm.write_text(t.replace("</urlset>", add + "</urlset>"), encoding="utf-8", newline="\n")
print("wrote", len(PAGES))
