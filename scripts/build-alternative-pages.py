"""Write the dated "<competitor> alternative" pages into site/.

Run from the repository root:  python scripts/build-alternative-pages.py
Every competitor figure below was read from that vendor's public pricing page on the date in READ.
Re-read the pages and update the figures before changing READ.
"""
import html
import json
from pathlib import Path

READ = "13 November 2026"
BASE = "https://webscraper.apexflowlabs.com"
APEX = [("Apex Project Pass", "$5 once", "35,000 (30 days)", "$0.14"), ("Apex Starter", "$15 / mo", "110,000", "$0.14"),
        ("Apex Growth", "$79 / mo", "850,000", "$0.09"), ("Apex Scale", "$299 / mo", "3,750,000", "$0.08")]
SHARED_DIFFERENCES = [
    ("A one-time pass.", "$5 for 35,000 credits, valid 30 days, no subscription and no renewal."),
    ("No overage charges.", "When credits run out, requests stop. Failed scrapes return their credits."),
    ("Rendered pages cost 3 credits.", "{rendered}"),
    ("Change tracking and token-budget reads are included.", "Every read says whether the page is new, unchanged or changed, and <code>max_tokens</code> sizes a page to your model."),
    ("A labelled archive copy when a site refuses.", "You get the latest Internet Archive copy with its capture time instead of an error, unless you switch that off."),
    ("Open source engine (MIT).", "Run the same converter yourself, including a reader for pages you are signed in to, on your own machine."),
]
PAGES = [
    {"slug": "scrapingbee-alternative", "name": "ScrapingBee", "url": "https://www.scrapingbee.com/pricing/",
     "plans": [("ScrapingBee $19 plan", "$19 / mo", "75,000", "$0.25"), ("ScrapingBee $49 plan", "$49 / mo", "250,000", "$0.20"),
               ("ScrapingBee $99 plan", "$99 / mo", "1,000,000", "$0.10"), ("ScrapingBee $249 plan", "$249 / mo", "3,000,000", "$0.08")],
     "rendered": "ScrapingBee's pricing page lists 5 credits for a request with JavaScript rendering and 1 without.",
     "ahead": ["Managed proxy rotation, including premium and stealth proxies. We do not offer proxies.",
               "Higher concurrency on its plans than our 20.", "A longer track record and a larger customer base."],
     "cheaper": "On the plans we compared, yes for plain pages: $0.14 per 1,000 on our $15 Starter against $0.25 on ScrapingBee's $19 plan, and $0.09 on our $79 Growth against $0.10 on its $99 plan. At about $250 to $300 a month the two are within a fraction of a cent of each other."},
    {"slug": "scraperapi-alternative", "name": "ScraperAPI", "url": "https://www.scraperapi.com/pricing/",
     "plans": [("ScraperAPI $49 plan", "$49 / mo", "100,000", "$0.49"), ("ScraperAPI $149 plan", "$149 / mo", "1,000,000", "$0.15"),
               ("ScraperAPI $299 plan", "$299 / mo", "3,000,000", "$0.10"), ("ScraperAPI $475 plan", "$475 / mo", "5,000,000", "$0.10")],
     "rendered": "ScraperAPI charges additional credits for rendered and protected pages; see its documentation for the multipliers.",
     "ahead": ["Managed proxy rotation and anti-bot handling. We do not offer proxies or solve challenges.",
               "20 to 500 concurrent threads on its plans against our 6 to 20.", "A 7-day trial with 5,000 credits (ours is 1,000 credits, once)."],
     "cheaper": "On the plans we compared, yes for plain pages: $0.14 per 1,000 on our $15 Starter against $0.49 on ScraperAPI's $49 plan, and $0.08 on our $299 Scale against $0.10 on its $299 plan."},
    {"slug": "scrapfly-alternative", "name": "Scrapfly", "url": "https://scrapfly.io/pricing",
     "plans": [("Scrapfly $30 plan", "$30 / mo", "200,000", "$0.15"), ("Scrapfly $100 plan", "$100 / mo", "1,000,000", "$0.10"),
               ("Scrapfly $250 plan", "$250 / mo", "2,500,000", "$0.10"), ("Scrapfly $500 plan", "$500 / mo", "5,500,000", "$0.09")],
     "rendered": "Scrapfly's pricing page lists 5 credits for JavaScript rendering, 25 with residential proxies and 60 for a full-page screenshot.",
     "ahead": ["An unblocker with browser and proxy automation. We do not offer proxies or evade bot walls.",
               "Up to 100 concurrent requests on its listed plans against our 20.", "Compliance paperwork (SOC 2, ISO 27001) that we do not have."],
     "cheaper": "On the plans we compared, slightly for plain pages: $0.14 per 1,000 on our $15 Starter against $0.15 on Scrapfly's $30 plan, $0.09 on our $79 Growth against $0.10 on its $100 plan, and $0.08 on our $299 Scale against $0.09 on its $500 plan. Our entry price is half of theirs."},
    {"slug": "zenrows-alternative", "name": "ZenRows", "url": "https://www.zenrows.com/pricing",
     "plans": [("ZenRows Build", "$16 / mo", "45,000", "$0.36"), ("ZenRows Launch", "$58 / mo", "250,000", "$0.23"),
               ("ZenRows Growth", "$166 / mo", "1,200,000", "$0.14"), ("ZenRows Scale", "$458 / mo", "5,000,000", "$0.09")],
     "rendered": "ZenRows' pricing page lists 5 credits with JavaScript rendering, 10 with premium proxies and 25 with both.",
     "ahead": ["5,000 free credits every month (ours are a one-time 1,000).", "Premium and residential proxies. We do not offer proxies.",
               "20 to 400 concurrent requests on its paid plans against our 6 to 20."],
     "cheaper": "On the plans we compared, yes for plain pages on paid plans: $0.14 per 1,000 on our $15 Starter against $0.36 on ZenRows Build at $16, and $0.09 on our $79 Growth against $0.14 on ZenRows Growth at $166. ZenRows' free plan is larger than ours."},
    {"slug": "apify-alternative", "name": "Apify", "url": "https://apify.com/pricing",
     "plans": [("Apify Free", "$0", "$5 of platform usage a month", "varies by Actor"), ("Apify Starter", "$19 / mo", "$19 of platform usage", "varies by Actor"),
               ("Apify Scale", "$199 / mo", "$199 of platform usage", "varies by Actor"), ("Apify Business", "$999 / mo", "$999 of platform usage", "varies by Actor")],
     "rendered": "Apify bills compute units and per-result prices set by each Actor, so a rendered page has no single price.",
     "ahead": ["A marketplace of thousands of ready-made scrapers for specific sites. We have a general reader and a small set of data packages.",
               "$5 of free platform usage every month.", "A full platform for scheduling, storage and custom code that we do not try to match."],
     "cheaper": "It depends on the Actor. Apify sells platform usage in dollars and each Actor sets its own price per result or per compute unit, so there is no single per-page figure to compare. Our price is fixed: 1 credit for a plain page, from $0.14 down to $0.08 per 1,000."},
]


def e(text):
    return html.escape(text, quote=True)


def rows(plans):
    return "".join("<tr>" + "".join(f"<td>{e(cell)}</td>" for cell in plan) + "</tr>\n" for plan in plans)


def build(page):
    name, slug = page["name"], page["slug"]
    faq = [
        (f"Is there a {name} alternative with a one-time price?",
         f"Yes. Apex Flow 3D Scraper sells a $5 Project Pass with 35,000 credits valid for 30 days, with no subscription and no renewal. {name}'s public pricing page (read {READ}) lists monthly plans."),
        (f"Is it cheaper than {name}?", page["cheaper"] + " Each vendor counts credits differently, so test your own workload."),
        (f"What does {name} do better?", " ".join(page["ahead"])),
        ("Are failed pages charged?", "No. Failed scrapes return the reserved credits, and there are no automatic overage charges."),
        ("Can I self-host it?", "Yes. The engine is open source under the MIT licence at github.com/Apexflowlabsllc/apex-flow-web-scraper."),
    ]
    ld = {"@context": "https://schema.org", "@type": "FAQPage",
          "mainEntity": [{"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in faq]}
    differences = "".join(f"<li><b>{e(title)}</b> {body.format(rendered=e(page['rendered']))}</li>\n" for title, body in SHARED_DIFFERENCES)
    ahead = "".join(f"<li>{e(item)}</li>" for item in page["ahead"])
    title = f"{name} alternative with a one-time price · Apex Flow 3D Scraper"
    description = (f"An alternative to {name} for turning web pages into Markdown: a $5 one-time pass, $15 a month for 110,000 credits, "
                   f"failed pages refunded, no overage. Dated price comparison with honest tradeoffs.")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(title)}</title><meta name="description" content="{e(description)}"><link rel="canonical" href="{BASE}/{slug}.html"><meta property="og:title" content="{e(title)}"><meta property="og:description" content="{e(description)}"><meta property="og:url" content="{BASE}/{slug}.html"><meta property="og:type" content="article"><link rel="stylesheet" href="/app.css">
<script type="application/ld+json">{json.dumps(ld, ensure_ascii=False)}</script></head><body><nav><a href="/">APEX FLOW 3D SCRAPER</a><a href="/dashboard.html">Dashboard →</a></nav><main>
<p class="eyebrow">Compared {READ}</p><h1>A {e(name)} alternative with a one-time price.</h1>
<p class="lede">Apex Flow 3D Scraper turns public web pages, PDFs and Word files into clean Markdown. Start with 1,000 free credits, or buy a $5 pass for 35,000 credits with no subscription. Here is how it differs from {e(name)}, including where {e(name)} is ahead.</p>
<div class="card"><h2 style="margin-top:0">Price per 1,000 plain pages (monthly billing)</h2>
<table><thead><tr><th>Plan</th><th>Price</th><th>Credits</th><th>Per 1,000</th></tr></thead><tbody>
{rows(APEX)}{rows(page["plans"])}</tbody></table>
<p>{e(name)} figures from <a href="{e(page["url"])}" rel="nofollow noopener">its public pricing page</a> on {READ}. Each vendor counts credits differently, so measure your own workload.</p></div>
<h2>Is it cheaper than {e(name)}?</h2><p>{e(page["cheaper"])}</p>
<h2>Where we differ</h2>
<ul>{differences}</ul>
<h2>Where {e(name)} is ahead</h2>
<ul>{ahead}</ul>
<h2>What we do not do</h2>
<p>We do not rotate proxies, disguise requests or solve CAPTCHAs. A site behind a bot wall stays refused on the live read, and you receive a dated archive copy instead. If your work depends on getting through bot walls at volume, {e(name)} is the better fit.</p>
<p><a class="button" href="/trial.html">Try 1,000 free credits →</a> <a class="button secondary" href="/compare.html">See every comparison</a></p></main></body></html>
"""


def main():
    site = Path(__file__).resolve().parent.parent / "site"
    for page in PAGES:
        (site / (page["slug"] + ".html")).write_text(build(page), encoding="utf-8", newline="\n")
        print("wrote", page["slug"] + ".html")


main()
