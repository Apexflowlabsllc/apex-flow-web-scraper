"""Write site/backlinks.html, the Backlinks and SEO product page.

Every sample reply below is real output of the endpoints, captured 23 November 2026 against the live web and the
cc-main-2026-aug-sep-oct link index. Competitor prices were read on 23 November 2026 from openrush.com/pricing and
ahrefs.com/pricing with our own scraper. Run: python scripts/build-backlinks-page.py
"""
import html
import json
from pathlib import Path

BASE = "https://webscraper.apexflowlabs.com"
e = lambda t: html.escape(str(t), quote=True)
CREDIT = 15 / 110000          # Starter: $15 for 110,000 credits

GAP = {"summary": {"union": 1698, "after_filters": 320, "gap": 320, "earned": 0},
       "gap": [{"domain": "n8n.io", "authority": 582, "competitor_count": 4, "links_to": ["firecrawl.dev", "scrapingbee.com", "zenrows.com", "scrapfly.io"], "earned": False},
               {"domain": "apify.com", "authority": 545, "competitor_count": 4, "links_to": ["firecrawl.dev", "scrapingbee.com", "zenrows.com", "scrapfly.io"], "earned": False},
               {"domain": "salesflare.com", "authority": 459, "competitor_count": 4, "links_to": ["firecrawl.dev", "scrapingbee.com", "zenrows.com", "scrapfly.io"], "earned": False}]}
AUTH = {"domain": "firecrawl.dev", "found": True, "authority": 312, "global_rank": 391009, "referring_domains": 2653,
        "strong_referring_domains": 797, "domains_ranked": 134979706, "graph_release": "cc-main-2026-aug-sep-oct"}
COMP = {"domain": "petco.com", "competitors": [{"domain": "chewy.com", "authority": 549, "shared_referring_domains": 35},
                                              {"domain": "petcolove.org", "authority": 307, "shared_referring_domains": 14},
                                              {"domain": "petsmart.com", "authority": 484, "shared_referring_domains": 25}]}
DEAD = {"domain": "zenrows.com", "checked": 120, "dead_pages": [{
    "url": "https://www.zenrows.com/blog/best-python-web-scraping-libraries", "status": 404, "last_seen_working": "2023-05-25",
    "archive_url": "https://web.archive.org/web/20230525100404/https://www.zenrows.com/blog/best-python-web-scraping-libraries",
    "used_to_say": {"title": "5 Best Python Web Scraping Libraries in 2023", "words": 2392,
                    "headings": ["5 Best Python Web Scraping Libraries in 2023", "What Are the Best Python Web Scraping Libraries?", "How to Scrape a Web Page with ZenRows?"]}}],
    "who_links_to_the_site": [{"domain": "mozilla.org", "authority": 810}, {"domain": "archlinux.org", "authority": 659}, {"domain": "vercel.com", "authority": 598}]}
FIND = {"target": "scrapingbee.com", "site": "pagecrawl.io", "pages_read": 15,
        "links": [{"page": "https://pagecrawl.io/blog/best-web-scraping-apis-2026", "links_to": "https://www.scrapingbee.com/", "anchor": "ScrapingBee", "dofollow": True}]}
SEO = {"url": "https://webscraper.apexflowlabs.com/", "score": 94, "title_length": 62, "description_length": 201, "h1": ["Turn the public web into clean, structured data."],
       "canonical": "https://webscraper.apexflowlabs.com/", "lang": "en", "structured_data_types": ["FAQPage", "SoftwareApplication"],
       "issues": [{"code": "title_long", "severity": "warning", "message": "Title is 62 characters; search results cut it near 60."},
                  {"code": "description_long", "severity": "notice", "message": "Meta description is 201 characters; results cut it near 160."}]}

PRICES = [("Domain authority", "/v1/links/authority", 1, "$0.15 (backlink profile, 15 credits)"),
          ("Referring domains", "/v1/links/referring-domains", 2, "$0.15 (backlink profile, 15 credits)"),
          ("Link gap against up to 20 competitors", "/v1/links/gap", 10, "$0.60 (backlink gap, 60 credits)"),
          ("Competitors from shared links", "/v1/links/competitors", 5, "$0.09 (competitors, 9 credits)"),
          ("Dead-page hunt, per page checked or read", "/v1/links/dead", 1, "not offered"),
          ("Find the exact linking page, per page read", "/v1/links/find", 1, "not offered"),
          ("SEO check of one page", "/v1/seo/page", 1, "part of a site audit"),
          ("SEO site audit, per page", "/v1/seo/audit", 1, "$0.09 per audit (9 credits)"),
          ("Link history since 2018", "/v1/links/history", 2, "not offered"),
          ("New and lost referring domains", "/v1/links/changes", 3, "not offered"),
          ("Outreach contacts, per page read", "/v1/links/contacts", 1, "not offered")]

FAQ = [("Where does the link data come from?", "From our own link index, built from Common Crawl's public web graph: 135 million domains and 1.8 billion links between them, from the August to October 2026 crawls. We rebuild it when Common Crawl publishes a new graph. Page-level links, anchors and dead pages come from live reads of the web by our own crawler."),
       ("Is authority the same as Ahrefs Domain Rating or Moz Domain Authority?", "No. It is our own 0 to 1,000 score from a domain's harmonic centrality, a measure of how close it sits to every other site in the link graph. 1,000 is the best-connected site on the web. Compare domains with each other on our scale, not with another vendor's."),
       ("Why is my small site not found?", "The index lists links from the web's five million best-connected domains and includes a domain once at least two of them link to it. A new or small site often has fewer, which is exactly what the link gap fixes. The SEO checks and the link finder still work on any site."),
       ("How does the dead-page hunt work?", "It asks the Internet Archive for every page it saw working on a competitor's site, checks each one live, and keeps those that now return 404 or 410, redirect to the home page, or whose domain is gone. It reads the archived copy of the first few so you see what they said, drops template pages that were never real, and lists the strong sites linking to that competitor so you know who to tell about your better version."),
       ("What does it cost?", "Credits from the same balance as scraping. A link gap report is 10 credits, about $0.0014 on the $15 Starter plan; OpenRush lists $0.60 for the same report. Jobs cost 1 credit per page read, and failed pages are free."),
       ("Can my AI agent use it?", "Yes. The remote MCP server at https://webscraper.apexflowlabs.com/v1/mcp has twelve tools for it: domain_authority, referring_domains, backlink_gap, find_competitors, dead_page_hunt, find_backlinks, outreach_contacts, link_history, link_changes, seo_page_check, seo_site_audit and link_job.")]


def code(obj):
    return f'<div class="code"><pre><code>{e(json.dumps(obj, indent=2))}</code></pre></div>'


def curl(path, body):
    slash = chr(92)
    lines = ["curl " + BASE + "/v1" + path + " " + slash,
             '  -H "Authorization: Bearer $APEX_SCRAPER_API_KEY" -H "Content-Type: application/json" ' + slash,
             "  -d '" + json.dumps(body) + "'"]
    return '<div class="code"><button type="button" class="copy" data-copy>Copy</button><pre><code>' + e(chr(10).join(lines)) + "</code></pre></div>"


def build():
    title = "Backlink checker and link gap API: see who links to your competitors"
    desc = "Find the sites that link to your competitors but not to you, hunt their dead pages, and find the exact link to ask for. Own link index of 135 million domains."
    rows = "".join(f"<tr><td>{e(n)}</td><td><code>{e(p)}</code></td><td>{c}</td><td>${c * CREDIT:.4f}</td><td>{e(o)}</td></tr>" for n, p, c, o in PRICES)
    faq = "".join(f"<details><summary>{e(q)}</summary><p>{e(a)}</p></details>" for q, a in FAQ)
    ld = [{"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [{"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in FAQ]},
          {"@context": "https://schema.org", "@type": "SoftwareApplication", "name": "Apex Flow Web Scraper: Backlinks and SEO", "applicationCategory": "DeveloperApplication",
           "operatingSystem": "Web", "url": BASE + "/backlinks.html", "publisher": {"@type": "Organization", "name": "Apex Flow Labs", "url": "https://www.apexflowlabs.com"},
           "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD", "description": "1,000 free credits, then from $5"}}]
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(title)} · Apex Flow Web Scraper</title>
<meta name="description" content="{e(desc)}"><link rel="canonical" href="{BASE}/backlinks.html"><meta property="og:title" content="{e(title)}"><meta property="og:description" content="{e(desc)}"><meta property="og:image" content="{BASE}/assets/og-scraper-2026-11-22.jpg"><meta name="twitter:card" content="summary_large_image">
<link rel="stylesheet" href="/app.css"><link rel="stylesheet" href="/integrations/int.css"><style>
.hunts{{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:16px;margin:32px 0 8px}}
.hunts a{{display:block;padding:20px 20px 18px;border:1px solid rgba(25,217,238,.28);border-radius:16px;background:linear-gradient(180deg,rgba(25,217,238,.07),rgba(25,217,238,.01));color:inherit;text-decoration:none;transition:border-color .2s,transform .2s}}
.hunts a:hover,.hunts a:focus-visible{{border-color:rgba(25,217,238,.7);transform:translateY(-2px)}}
.hunts b{{display:block;font-size:18px;margin:6px 0}}.hunts span{{font:700 11px ui-monospace,Consolas,monospace;letter-spacing:.16em;color:#8fd8ea}}.hunts p{{margin:0;color:var(--mute);font-size:15px;line-height:1.5}}
.stat{{display:flex;flex-wrap:wrap;gap:28px;margin:26px 0 0}}.stat div{{min-width:150px}}.stat b{{display:block;font-size:30px;letter-spacing:-.02em}}.stat small{{color:var(--mute)}}
@media (prefers-reduced-motion:reduce){{.hunts a{{transition:none}}.hunts a:hover{{transform:none}}}}
.checker{{margin:34px 0 8px;padding:20px;border:1px solid rgba(25,217,238,.45);border-radius:16px;background:linear-gradient(180deg,rgba(25,217,238,.09),rgba(25,217,238,.02))}}
.checker label{{display:block;font:700 12px ui-monospace,Consolas,monospace;letter-spacing:.14em;text-transform:uppercase;color:#8fd8ea;margin-bottom:10px}}
.checker div{{display:flex;gap:10px;flex-wrap:wrap}}.checker input{{flex:1;min-width:200px;padding:12px 14px;border-radius:12px;border:1px solid rgba(255,255,255,.18);background:rgba(3,14,20,.8);color:#eafcff;font:600 16px ui-monospace,Consolas,monospace}}
.checker .button{{margin:0}}#chk-out table{{margin-top:14px}}#chk-out .big{{font-size:26px;font-weight:700;margin-right:22px}}
</style><script type="application/ld+json">{json.dumps(ld, ensure_ascii=False)}</script></head><body>
<nav aria-label="Primary"><a href="/">APEX FLOW WEB SCRAPER</a><span class="nav-links"><a href="/integrations/">Integrations</a><a href="/docs.html#links">Docs</a><a href="/#pricing">Pricing</a><a class="button" href="/trial.html">Get a free key</a></span></nav><main>
<p class="eyebrow">Backlinks and SEO</p><h1>See who links to your competitors. <em>Win those links.</em></h1>
<p class="lede">Our own link index maps 135 million domains and the 1.8 billion links between them. Ask who links to any site, which sites link to your rivals but not to you, and which of a competitor's pages have died, so you can offer the better version to everyone still linking to it.</p>
<div class="stat"><div><b>135M</b><small>domains ranked</small></div><div><b>1.8B</b><small>links between them</small></div><div><b>10 credits</b><small>for a link gap report</small></div><div><b>12</b><small>MCP tools for your agent</small></div></div>
<form class="checker" id="checker" aria-label="Free backlink checker"><label for="chk-domain">Check any domain free</label><div><input id="chk-domain" name="domain" placeholder="competitor.com" autocomplete="off" required maxlength="200"><button class="button" type="submit">Check backlinks</button></div><p class="note" id="chk-note">No key needed. Authority, referring domains and the ten strongest sites linking to it.</p><div id="chk-out" aria-live="polite"></div></form>
<div class="hunts">
<a href="#gap"><span>HUNT 1</span><b>The link gap</b><p>Sites that link to several of your competitors and not to you, strongest first.</p></a>
<a href="#dead"><span>HUNT 2</span><b>Dead pages and the archive</b><p>A competitor's pages that now return 404, and what they used to say.</p></a>
<a href="#find"><span>HUNT 3</span><b>The exact link</b><p>The page, the anchor text and whether it is followed, so your ask is specific.</p></a>
</div>
<h2 id="gap">Hunt 1: the link gap</h2><p>Give your site and up to 20 competitors. The reply ranks every site that links to at least two of them by how many it links to and by authority, leaves out blog hosts, site builders and shorteners, and marks the ones that already link to you as <code>earned</code>.</p>
{curl("/links/gap", {"domain": "webscraper.apexflowlabs.com", "competitors": ["firecrawl.dev", "scrapingbee.com", "zenrows.com", "scrapfly.io"], "min_authority": 200})}
<p>Part of the real reply: 320 sites link to at least two of these four scraping APIs and not to us.</p>{code(GAP)}
<h2 id="dead">Hunt 2: dead pages and the archive</h2><p>The background job asks the Internet Archive for every page it saw working on a competitor's site, checks each one live, keeps the dead ones, and reads the archived copy so you see what the page said. Write the better version, then tell the sites in <code>who_links_to_the_site</code>.</p>
{curl("/links/dead", {"domain": "zenrows.com", "limit": 120, "read_archive": 6})}
<p>Part of the real result: 12 dead pages out of 120 checked, in 51 seconds.</p>{code(DEAD)}
<h2 id="find">Hunt 3: find the exact link</h2><p>Point it at a site and a target, a domain or one exact page such as a competitor's dead article. It learns what the target is about from its home page, reads the pages most likely to hold the link first, and returns each link with its anchor text and follow status.</p>
{curl("/links/find", {"target": "scrapingbee.com", "site": "pagecrawl.io", "max_pages": 15})}
<p>The real result: found on the 15 pages it read.</p>{code(FIND)}
<h2 id="authority">Authority and referring domains</h2><p>Instant answers from the index. Subdomains roll up to the registered domain. Referring domains come back strongest first, up to 1,000 per domain.</p>
{curl("/links/authority", {"domain": "firecrawl.dev"})}{code(AUTH)}
<h2 id="competitors">Competitors from shared links</h2><p>The sites that the same niche blogs, directories and review pages link to are your real competitors. Giants that everyone links to are scored down, so you get rivals, not search engines.</p>{code(COMP)}
<h2 id="outreach">After the gap: contacts, lost links and history</h2><p><code>/v1/links/contacts</code> reads a linking site's own pages and returns the public contact emails, contact and "write for us" pages and social profiles it publishes. <code>/v1/links/changes</code> lists the strong sites that started or stopped linking to a domain between the last two quarterly releases: lost links are the easiest to win back. <code>/v1/links/history</code> shows a domain's referring domains and authority every quarter since 2018.</p>
<p>Compare: <a href="/ahrefs-alternative.html">Ahrefs</a> · <a href="/semrush-backlink-alternative.html">Semrush</a> · <a href="/moz-link-explorer-alternative.html">Moz</a> · <a href="/majestic-alternative.html">Majestic</a> · <a href="/openrush-alternative.html">OpenRush</a></p>
<h2 id="seo">SEO checks</h2><p><code>/v1/seo/page</code> checks one page: title and description length, headings, canonical, robots, language, viewport, image alt text, structured data, social preview tags and links, each issue ranked error, warning or notice. <code>/v1/seo/audit</code> crawls a site, runs the same checks on every page and adds broken internal links (with the pages linking to them) and duplicate titles and descriptions. Our own home page, checked:</p>{code(SEO)}
<h2>Prices</h2><p>Same credits as scraping. Dollar figures use the $15 Starter plan (110,000 credits). OpenRush figures are its own credit costs from openrush.com/pricing and its tool list, read on 23 November 2026 ($0.01 a credit); Ahrefs plans start at $129 a month (ahrefs.com/pricing, same day).</p>
<div class="tablewrap"><table><thead><tr><th>Task</th><th>Endpoint</th><th>Credits</th><th>On Starter</th><th>OpenRush</th></tr></thead><tbody>{rows}</tbody></table></div>
<h2>What it is, plainly</h2><p>The index is domain-level: it knows which sites link to which, not every page and anchor. It lists links from the web's five million best-connected domains and includes a domain once two of them link to it. Exact pages and anchors come from live reads by the link finder. It is rebuilt from Common Crawl's public web graph, so it reflects the web as last crawled (August to October 2026 for this release), not this morning.</p>
<h2>Questions</h2>{faq}
<p class="cta"><a class="button" href="/trial.html">Get 1,000 free credits →</a> <a class="button secondary" href="/docs.html#links">Read the docs</a></p>
</main><footer><span>Apex Flow Web Scraper, by Apex Flow Labs.</span><a href="/integrations/">Integrations</a><a href="/docs.html">Docs</a><a href="/compare.html">Compare</a><a href="/terms.html">Terms</a><a href="/privacy.html">Privacy</a></footer>
<script>document.getElementById("checker").addEventListener("submit",async ev=>{{ev.preventDefault();const out=document.getElementById("chk-out"),d=document.getElementById("chk-domain").value.trim();out.textContent="Checking…";
try{{const r=await fetch("/api/free-links",{{method:"POST",headers:{{"Content-Type":"application/json"}},body:JSON.stringify({{domain:d}})}});const j=await r.json();if(!r.ok){{out.textContent=j.error||"Please try again shortly.";return}}const x=j.result;
const esc=t=>String(t).replace(/[&<>"]/g,c=>({{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}})[c]);
if(!x.found){{out.innerHTML="<p><b>"+esc(x.domain)+"</b>: "+esc(x.note)+"</p><p><a href=\"/trial.html\">Run a link gap to see who links to its rivals →</a></p>";return}}
out.innerHTML="<p><span class=big>"+x.authority+"</span>authority (0-1,000) · <b>"+x.referring_domains.toLocaleString()+"</b> referring domains · <b>"+x.strong_referring_domains.toLocaleString()+"</b> strong · global rank "+(x.global_rank||0).toLocaleString()+"</p><div class=tablewrap><table><thead><tr><th>Strongest sites linking to "+esc(x.domain)+"</th><th>Authority</th></tr></thead><tbody>"+x.top_referring_domains.map(r=>"<tr><td>"+esc(r.domain)+"</td><td>"+r.authority+"</td></tr>").join("")+"</tbody></table></div><p><a href=\"/trial.html\">Get the full list, the link gap and outreach contacts with 1,000 free credits →</a></p>"}}catch(e){{out.textContent="Please try again shortly."}}}});</script>
<script src="/integrations/int.js" defer></script><script src="/assets/cursor.js" defer></script></body></html>
'''


if __name__ == "__main__":
    out = Path(__file__).resolve().parent.parent / "site" / "backlinks.html"
    out.write_text(build(), encoding="utf-8", newline="\n")
    print("wrote", out)
