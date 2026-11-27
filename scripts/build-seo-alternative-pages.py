"""Write the backlink-tool comparison pages (Ahrefs, Semrush, Moz, Majestic, OpenRush).

Every rival figure was read from the rival's own page with our scraper on 27 November 2026 (OpenRush on 23 November):
ahrefs.com/pricing + /big-data, semrush.com/prices, moz.com/products/pro/pricing + /link-explorer, majestic.com/about +
/plans-pricing, openrush.com/pricing. Run: python scripts/build-seo-alternative-pages.py
"""
import html
import json
from pathlib import Path

BASE = "https://webscraper.apexflowlabs.com"
e = lambda t: html.escape(str(t), quote=True)
OURS = {"price": "$0 to start (1,000 credits), then $5 once or $15 a month", "gap": "10 credits (about $0.0014 on Starter)",
        "api": "Yes, on every plan including the free trial", "mcp": "Yes, 12 link and SEO tools on every plan",
        "dead": "Yes: dead pages, what they used to say (archive), and who links to the site", "contacts": "Yes: public emails, contact and write-for-us pages",
        "history": "Referring domains and authority per quarter since 2017, 2 credits", "verify": "Yes: the link finder reads the linking page live",
        "index": "Own index: 135M domains, 1.8B domain-to-domain links (Common Crawl web graph); page-level links read live"}
RIVALS = [
    dict(slug="ahrefs-alternative", name="Ahrefs", price="From $129 a month (Lite), $249 and $449 plans", gap="Included in the plan (Site Explorer link intersect)",
         api="API and MCP access with 1,000 credits per user on Lite", index="35T records of external backlinks history, 494B pages in index, updated every 15-30 minutes",
         ahead=["A far bigger page-level backlink index, refreshed every 15 to 30 minutes.", "Keyword, rank tracking and content tools in the same plan.", "A polished web interface for people who do not use an API or an AI agent."],
         angle="Pay once, not monthly, and let your AI agent run the link hunts."),
    dict(slug="semrush-backlink-alternative", name="Semrush", price="$139 a month (SEO plan), $117.33 a month billed annually", gap="Included in the plan (Backlink Gap)",
         api="See its pricing page", index="Large proprietary backlink database",
         ahead=["A full marketing suite: keywords, ads, content and local SEO.", "Page-level backlink data with history in its web interface.", "Team and reporting features for agencies."],
         angle="Backlink research for a fraction of a cent, without a $139 monthly plan."),
    dict(slug="moz-link-explorer-alternative", name="Moz", price="$99 a month (Standard), $79 a month paid yearly; 10 free queries a month", gap="Included (Link Intersect, up to five competitors)",
         api="See its pricing page", index="45.5 trillion links (Link Explorer)",
         ahead=["Domain Authority is a widely quoted metric your clients may already know.", "A larger page-level link index.", "A free tier of 10 queries a month in the web interface."],
         angle="A link gap against 20 competitors, not five, for 10 credits."),
    dict(slug="majestic-alternative", name="Majestic", price="From US$49.99 a month", gap="Included (Clique Hunter)",
         api="On higher plans", index="Fresh Index updated hourly; Historic Index",
         ahead=["Trust Flow and Citation Flow, metrics many link builders work with.", "A page-level Fresh Index rebuilt every hour.", "A Historic Index of links going back many years."],
         angle="Link gaps, dead-page hunts and outreach contacts in one API, from $5."),
    dict(slug="openrush-alternative", name="OpenRush", price="$10 per 1,000 credits ($0.01 a credit)", gap="60 credits ($0.60)",
         api="Yes, built for agents", index="Third-party backlink index",
         ahead=["Keyword research, live search results and Search Console data in the same tool.", "A page-level backlink index from a commercial data vendor."],
         angle="The same link gap for 10 credits instead of 60, at a fraction of the credit price."),
]
ROWS = [("Price to start", "price"), ("Cost of a link gap report", "gap"), ("API access", "api"), ("MCP tools for AI agents", "mcp"),
        ("Link index", "index"), ("Dead-page hunt with archive copies", "dead"), ("Outreach contacts", "contacts"),
        ("Link history", "history"), ("Live check of the linking page", "verify")]


def page(r):
    title = f"{r['name']} alternative for backlinks: {r['angle']}"
    desc = f"Compare Apex Flow's backlink API with {r['name']}: price, link gap cost, API and MCP access, dead-page hunting, outreach contacts and link history."[:155]
    rival = {"price": r["price"], "gap": r["gap"], "api": r["api"], "mcp": "Yes" if r["name"] in ("Ahrefs", "OpenRush") else "See its pricing page",
             "index": r["index"], "dead": "Not as one workflow" if r["name"] != "Ahrefs" else "Broken backlinks report; no archived copy or outreach step",
             "contacts": "Not included", "history": "Yes, in its paid plans" if r["name"] in ("Ahrefs", "Majestic") else "See its pricing page",
             "verify": "Index data, refreshed on its schedule"}
    rows = "".join(f"<tr><td>{e(label)}</td><td>{e(OURS[k])}</td><td>{e(rival[k])}</td></tr>" for label, k in ROWS)
    ahead = "".join(f"<li>{e(x)}</li>" for x in r["ahead"])
    faq = [(f"Is Apex Flow as big as {r['name']}?", f"No. {r['name']} keeps a much larger page-level index. Ours is domain-level, built from Common Crawl's web graph, and the exact linking page is read live when you ask. For finding who links to your competitors and winning those links, that is enough, at a small fraction of the price."),
           ("What does a link gap cost?", "10 credits for up to 20 competitors, about $0.0014 on the $15 Starter plan, from the same credits as scraping."),
           ("Can an AI agent run it?", "Yes. Connect any MCP client to " + BASE + "/v1/mcp with your key; the link and SEO tools are on every plan, including the free trial.")]
    ld = {"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [{"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in faq]}
    faqh = "".join(f"<details><summary>{e(q)}</summary><p>{e(a)}</p></details>" for q, a in faq)
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(title)} · Apex Flow Web Scraper</title>
<meta name="description" content="{e(desc)}"><link rel="canonical" href="{BASE}/{r["slug"]}.html"><meta property="og:title" content="{e(title)}"><meta property="og:description" content="{e(desc)}"><meta property="og:image" content="{BASE}/assets/og-scraper-2026-11-22.jpg"><meta name="twitter:card" content="summary_large_image">
<link rel="stylesheet" href="/app.css"><link rel="stylesheet" href="/integrations/int.css"><script type="application/ld+json">{json.dumps(ld, ensure_ascii=False)}</script></head><body>
<nav aria-label="Primary"><a href="/">APEX FLOW WEB SCRAPER</a><span class="nav-links"><a href="/backlinks.html">Backlinks</a><a href="/docs.html#links">Docs</a><a href="/#pricing">Pricing</a><a class="button" href="/trial.html">Get a free key</a></span></nav><main>
<p class="eyebrow">Compare · {e(r["name"])}</p><h1>{e(r["name"])} alternative for backlinks. <em>{e(r["angle"])}</em></h1>
<p class="lede">Apex Flow's backlink API finds the sites that link to your competitors and not to you, hunts their dead pages, and finds the exact link and the contact to ask. It runs on the same credits as our web scraper, with no monthly plan required.</p>
<div class="tablewrap"><table><thead><tr><th></th><th>Apex Flow</th><th>{e(r["name"])}</th></tr></thead><tbody>{rows}</tbody></table></div>
<p class="note">{e(r["name"])} figures were read from its own website on 27 November 2026. Check theirs for current prices.</p>
<h2>Where {e(r["name"])} is ahead</h2><ul>{ahead}</ul>
<h2>Where Apex Flow wins</h2><ul><li>No monthly plan needed: 1,000 free credits, then $5 once for 35,000.</li><li>The whole hunt in one API: link gap, dead pages and what they said, the exact linking page, the contact to ask, lost links to win back.</li><li>Built for AI agents: every tool is an MCP tool on every plan.</li><li>The same credits also buy web scraping, crawling and change tracking.</li></ul>
<h2>Questions</h2>{faqh}
<p class="cta"><a class="button" href="/backlinks.html">Check a domain free →</a> <a class="button secondary" href="/trial.html">Get 1,000 free credits</a></p>
</main><footer><span>Apex Flow Web Scraper, by Apex Flow Labs.</span><a href="/backlinks.html">Backlinks</a><a href="/docs.html">Docs</a><a href="/compare.html">Compare</a><a href="/terms.html">Terms</a><a href="/privacy.html">Privacy</a></footer>
<script src="/integrations/int.js" defer></script><script src="/assets/cursor.js" defer></script></body></html>
'''


site = Path(__file__).resolve().parent.parent / "site"
for r in RIVALS:
    (site / f"{r['slug']}.html").write_text(page(r), encoding="utf-8", newline="\n")
sm = site / "sitemap.xml"
t = sm.read_text(encoding="utf-8")
add = "".join(f"<url><loc>{BASE}/{r['slug']}.html</loc></url>" for r in RIVALS if f"/{r['slug']}.html<" not in t)
sm.write_text(t.replace("</urlset>", add + "</urlset>"), encoding="utf-8", newline="\n")
print("wrote", len(RIVALS))
