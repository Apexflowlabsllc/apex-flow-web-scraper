"""Build the integration pages: one page per AI client, framework or tool, plus the hub at /integrations/.

Every snippet below was checked against the vendor's own documentation (or the tool's own help output) on the
date in CHECKED. Re-check and update that date before changing a snippet. Run: python scripts/build-integration-pages.py
"""
import html
import json
from pathlib import Path

BASE = "https://webscraper.apexflowlabs.com"
MCP = BASE + "/v1/mcp"
CHECKED = "21 November 2026"
KEY = "APEX_SCRAPER_API_KEY"

TOOLS = [
    ("scrape_page", "Read one page as clean Markdown, with a source receipt and what changed since your last read."),
    ("scrape_batch", "Read up to ten pages in parallel."),
    ("check_changes", "Re-read pages and get back only the lines that changed."),
    ("map_site", "List a site's pages from its sitemaps."),
    ("crawl_site", "Start a background crawl of one site's own pages."),
    ("crawl_status", "Read a crawl's progress and its pages as Markdown."),
    ("web_search", "Search the web and return links, titles and snippets."),
    ("search_saved", "Search the pages your account has already read. No credits."),
    ("house_prices", "House price index and trend for a US metro, from federal housing data."),
    ("npr_news", "Latest NPR headlines for a topic, with links."),
    ("track_store", "Snapshot a Shopify store's public catalog and report what changed."),
    ("rising_products", "Rank a tracked store's products by recent change signals."),
    ("tracked_stores", "List the stores your account tracks. No credits."),
    ("credit_balance", "Remaining credits, plan, limits and where to add more."),
]

J = lambda obj: json.dumps(obj, indent=2)
BEARER_ENV = "Bearer ${" + KEY + "}"

PAGES = [
    # ---- coding assistants -------------------------------------------------------------------------------------
    dict(slug="claude-code", name="Claude Code", mark="CC", group="Coding assistants", doc="https://code.claude.com/docs/en/mcp",
         blurb="One command in your terminal, or one entry in .mcp.json for the whole team.",
         steps=[("Add the server from your terminal", "bash",
                 f'claude mcp add --transport http apex-scraper {MCP} \\\n  --header "Authorization: Bearer ${KEY}"'),
                ("Or share it with your team: add this to .mcp.json in the project", "json",
                 J({"mcpServers": {"apex-scraper": {"type": "http", "url": MCP, "headers": {"Authorization": BEARER_ENV}}}}))],
         note="Type /mcp inside Claude Code to confirm the server is connected."),
    dict(slug="claude-desktop", name="Claude Desktop", mark="CD", group="Coding assistants", doc="https://github.com/geelen/mcp-remote",
         blurb="Add the scraper to the Claude desktop app through the open-source mcp-remote bridge.",
         steps=[("Open Settings, Developer, Edit Config, and add this to claude_desktop_config.json", "json",
                 J({"mcpServers": {"apex-scraper": {"command": "npx", "args": ["-y", "mcp-remote", MCP, "--header", "Authorization:${AUTH_HEADER}"],
                                                    "env": {"AUTH_HEADER": "Bearer YOUR_API_KEY"}}}}))],
         note="This needs Node.js on your computer. The bridge sends your key as a request header; restart Claude Desktop after saving the file."),
    dict(slug="cursor", name="Cursor", mark="CU", group="Coding assistants", doc="https://cursor.com/docs/context/mcp",
         blurb="Add one entry to mcp.json and Cursor's agent can read, crawl and watch the web.",
         steps=[("Add this to .cursor/mcp.json in your project (or ~/.cursor/mcp.json for every project)", "json",
                 J({"mcpServers": {"apex-scraper": {"url": MCP, "headers": {"Authorization": "Bearer ${env:" + KEY + "}"}}}}))],
         note=f"Cursor fills in ${{env:{KEY}}} from your environment, so the key never sits in the file."),
    dict(slug="vs-code", name="VS Code and GitHub Copilot", mark="VS", group="Coding assistants",
         doc="https://code.visualstudio.com/docs/agents/reference/mcp-configuration",
         blurb="VS Code asks for your key once and keeps it out of the workspace file.",
         steps=[("Add this to .vscode/mcp.json", "json",
                 J({"inputs": [{"type": "promptString", "id": "apex-scraper-key", "description": "Apex Flow Web Scraper API key", "password": True}],
                    "servers": {"apex-scraper": {"type": "http", "url": MCP, "headers": {"Authorization": "Bearer ${input:apex-scraper-key}"}}}}))],
         note="VS Code prompts for the key the first time the server starts."),
    dict(slug="windsurf", name="Windsurf (Devin Local)", mark="WS", group="Coding assistants", doc="https://docs.devin.ai/cli/extensibility/mcp/configuration",
         blurb="One entry in the MCP config file for Windsurf's local agent.",
         steps=[("Add this to .devin/mcp_config.json in your project (or ~/.config/devin/mcp_config.json for every project)", "json",
                 J({"mcpServers": {"apex-scraper": {"url": MCP, "headers": {"Authorization": "Bearer YOUR_API_KEY"}}}}))],
         note="Keep a file that holds a real key out of version control: use the local override file .devin/mcp_config.local.json."),
    dict(slug="cline", name="Cline", mark="CL", group="Coding assistants", doc="https://docs.cline.bot/mcp/connecting-to-a-remote-server",
         blurb="One command, or one entry in Cline's MCP settings.",
         steps=[("Add the server from your terminal", "bash",
                 f'cline mcp add apex-scraper {MCP} --transport http \\\n  --header "Authorization: Bearer YOUR_API_KEY" --yes'),
                ("Or add this under mcpServers in Cline's MCP settings", "json",
                 J({"mcpServers": {"apex-scraper": {"type": "streamableHttp", "url": MCP, "headers": {"Authorization": "Bearer YOUR_API_KEY"}}}}))],
         note="In the extension, the same fields are under MCP Servers, Remote Servers, with transport Streamable HTTP."),
    dict(slug="codex", name="Codex CLI", mark="CX", group="Coding assistants", doc="https://developers.openai.com/codex/config-reference",
         blurb="One command registers the scraper for every Codex session.",
         steps=[("Add the server from your terminal", "bash", f"codex mcp add apex-scraper --url {MCP} \\\n  --bearer-token-env-var {KEY}"),
                ("Or add this to ~/.codex/config.toml", "toml",
                 f'[mcp_servers.apex-scraper]\nurl = "{MCP}"\nbearer_token_env_var = "{KEY}"')],
         note=f"Codex reads the key from the {KEY} environment variable each time it connects."),
    dict(slug="gemini-cli", name="Gemini CLI", mark="GM", group="Coding assistants",
         doc="https://github.com/google-gemini/gemini-cli/blob/main/docs/tools/mcp-server.md",
         blurb="Add one server to settings.json.",
         steps=[("Add this to ~/.gemini/settings.json", "json",
                 J({"mcpServers": {"apex-scraper": {"httpUrl": MCP, "headers": {"Authorization": "Bearer YOUR_API_KEY"}}}}))],
         note="Gemini CLI uses httpUrl for servers that speak Streamable HTTP, which ours does."),
    dict(slug="zed", name="Zed", mark="ZD", group="Coding assistants", doc="https://zed.dev/docs/ai/mcp",
         blurb="Add a custom server to Zed's settings.",
         steps=[("Add this to your Zed settings.json", "json",
                 J({"context_servers": {"apex-scraper": {"url": MCP, "headers": {"Authorization": "Bearer YOUR_API_KEY"}}}}))],
         note="The same fields are in Settings, AI, MCP Servers."),
    dict(slug="lm-studio", name="LM Studio", mark="LM", group="Coding assistants", doc="https://lmstudio.ai/docs/app/mcp",
         blurb="Give a local model the live web: one entry in mcp.json.",
         steps=[("Open the Program tab, Install, Edit mcp.json, and add this", "json",
                 J({"mcpServers": {"apex-scraper": {"url": MCP, "headers": {"Authorization": "Bearer YOUR_API_KEY"}}}}))],
         note="Your model runs on your machine; only the page requests go to the scraper."),
    # ---- model APIs and agent frameworks ------------------------------------------------------------------------
    dict(slug="claude-api", name="Claude API", mark="AN", group="APIs and agent frameworks", doc="https://platform.claude.com/docs/en/agents-and-tools/mcp-connector",
         blurb="Pass the scraper to a Messages API call; Claude calls the tools itself.",
         steps=[("Python", "python",
                 'import os, anthropic\n\nclient = anthropic.Anthropic()\nresponse = client.beta.messages.create(\n    model="claude-opus-5-5",\n    max_tokens=2000,\n'
                 '    messages=[{"role": "user", "content": "Read https://example.com and summarise it in three points."}],\n'
                 f'    mcp_servers=[{{\n        "type": "url",\n        "url": "{MCP}",\n        "name": "apex-scraper",\n'
                 f'        "authorization_token": os.environ["{KEY}"],\n    }}],\n'
                 '    tools=[{"type": "mcp_toolset", "mcp_server_name": "apex-scraper"}],\n    betas=["mcp-client-2025-11-20"],\n)\nprint(response.content)')],
         note="The connector sends your scraper key as the bearer token on each call."),
    dict(slug="openai-responses", name="OpenAI Responses API", mark="OA", group="APIs and agent frameworks",
         doc="https://platform.openai.com/docs/guides/tools-connectors-mcp",
         blurb="Add the scraper as a remote MCP tool in a Responses API call.",
         steps=[("Python", "python",
                 'import os\nfrom openai import OpenAI\n\nclient = OpenAI()\nresp = client.responses.create(\n    model="gpt-6.2-astra",\n    tools=[{\n        "type": "mcp",\n'
                 f'        "server_label": "apex_scraper",\n        "server_description": "Read, crawl and watch public web pages as Markdown.",\n        "server_url": "{MCP}",\n'
                 f'        "authorization": os.environ["{KEY}"],\n        "require_approval": "never",\n    }}],\n'
                 '    input="Read https://example.com and summarise it in three points.",\n)\nprint(resp.output_text)')],
         note="OpenAI does not store the authorization value, so send it with every request."),
    dict(slug="openai-agents", name="OpenAI Agents SDK", mark="AG", group="APIs and agent frameworks", doc="https://openai.github.io/openai-agents-python/mcp/",
         blurb="Attach the scraper to an agent as a Streamable HTTP server.",
         steps=[("Python", "python",
                 'import asyncio, os\nfrom agents import Agent, Runner\nfrom agents.mcp import MCPServerStreamableHttp\n\nasync def main():\n    async with MCPServerStreamableHttp(\n'
                 f'        name="Apex Flow Web Scraper",\n        params={{"url": "{MCP}",\n                "headers": {{"Authorization": f"Bearer {{os.environ[\'{KEY}\']}}"}}}},\n'
                 '        cache_tools_list=True,\n    ) as server:\n        agent = Agent(name="Researcher", instructions="Use the web tools to answer.", mcp_servers=[server])\n'
                 '        result = await Runner.run(agent, "Read https://example.com and summarise it in three points.")\n        print(result.final_output)\n\nasyncio.run(main())')],
         note="cache_tools_list saves a round trip on every run."),
    dict(slug="langchain", name="LangChain and LangGraph", mark="LC", group="APIs and agent frameworks", doc="https://github.com/langchain-ai/langchain-mcp-adapters",
         blurb="Load the scraper's tools into any LangChain or LangGraph agent.",
         steps=[("Install the adapter", "bash", "pip install langchain-mcp-adapters"),
                ("Python", "python",
                 'import os\nfrom langchain_mcp_adapters.client import MultiServerMCPClient\nfrom langchain.agents import create_agent\n\nclient = MultiServerMCPClient({\n    "apex-scraper": {\n'
                 f'        "transport": "http",\n        "url": "{MCP}",\n        "headers": {{"Authorization": f"Bearer {{os.environ[\'{KEY}\']}}"}},\n    }}\n}})\n'
                 'tools = await client.get_tools()\nagent = create_agent("openai:gpt-4.1", tools)\n'
                 'reply = await agent.ainvoke({"messages": "Read https://example.com and summarise it in three points."})')],
         note="Run this inside an async function or a notebook."),
    dict(slug="vercel-ai-sdk", name="Vercel AI SDK", mark="AI", group="APIs and agent frameworks", doc="https://ai-sdk.dev/docs/ai-sdk-core/mcp-tools",
         blurb="Create an MCP client and hand its tools to generateText or streamText.",
         steps=[("TypeScript", "ts",
                 "import { createMCPClient } from '@ai-sdk/mcp';\n\nconst scraper = await createMCPClient({\n  transport: {\n    type: 'http',\n"
                 f"    url: '{MCP}',\n    headers: {{ Authorization: `Bearer ${{process.env.{KEY}}}` }},\n  }},\n}});\n\n"
                 "const tools = await scraper.tools();\n// pass tools to generateText or streamText, then:\nawait scraper.close();")],
         note="Close the client when your request is finished."),
    # ---- everything else ------------------------------------------------------------------------------------------
    dict(slug="rest-api", name="REST API: curl, Python, Node", mark="{}", group="Any language or tool", doc=BASE + "/docs.html",
         blurb="No SDK needed. One POST returns a page as Markdown.",
         steps=[("curl", "bash", f'curl {BASE}/v1/scrape \\\n  -H "Authorization: Bearer ${KEY}" \\\n  -H "Content-Type: application/json" \\\n  -d \'{{"url":"https://example.com","render":"never"}}\''),
                ("Python", "python", f'import os, requests\n\nr = requests.post("{BASE}/v1/scrape",\n    headers={{"Authorization": f"Bearer {{os.environ[\'{KEY}\']}}"}},\n    json={{"url": "https://example.com", "render": "never"}}, timeout=60)\nprint(r.json()["result"]["markdown"])'),
                ("Node 18+", "js", f"const r = await fetch('{BASE}/v1/scrape', {{\n  method: 'POST',\n  headers: {{ Authorization: `Bearer ${{process.env.{KEY}}}`, 'Content-Type': 'application/json' }},\n  body: JSON.stringify({{ url: 'https://example.com', render: 'never' }}),\n}});\nconsole.log((await r.json()).result.markdown);")],
         note="Use \"render\":\"auto\" only for pages that need JavaScript: a rendered page costs 3 credits instead of 1."),
    dict(slug="openapi", name="OpenAPI: custom GPTs, Postman and API tools", mark="O3", group="Any language or tool", doc=BASE + "/openapi.json",
         blurb="Import one file and every endpoint is described for you.",
         steps=[("Import this address wherever a tool accepts an OpenAPI 3.1 file", "text", BASE + "/openapi.json"),
                ("Set the authentication to an API key sent as a bearer token", "text", "Authorization: Bearer YOUR_API_KEY")],
         note="This is how a custom GPT action, Postman or an API gateway learns the API without hand-written setup."),
    dict(slug="automation", name="n8n, Make and Zapier", mark="NC", group="Any language or tool", doc=BASE + "/docs.html",
         blurb="Use the HTTP step every automation tool already has.",
         steps=[("Add an HTTP request step (n8n: HTTP Request, Make: HTTP, Zapier: Webhooks) with these fields", "text",
                 f"Method   POST\nURL      {BASE}/v1/scrape\nHeader   Authorization: Bearer YOUR_API_KEY\nHeader   Content-Type: application/json\nBody     {{\"url\": \"https://example.com\", \"render\": \"never\"}}"),
                ("To watch a page, call it on a schedule and branch on this field of the reply", "text", "change.status   new | unchanged | changed")],
         note="Store the key in the tool's own credential or secret store, not in the step."),
    dict(slug="agent-skill", name="Agent skill file", mark="SK", group="Any language or tool", doc=BASE + "/agent-onboarding/SKILL.md",
         blurb="A ready-made skill file that teaches an agent the API, the costs and the rules.",
         steps=[("Give your agent this file", "text", BASE + "/agent-onboarding/SKILL.md"),
                ("Machine-readable summary and prices", "text", f"{BASE}/llms.txt\n{BASE}/pricing.md")],
         note="The skill tells the agent to ask a person for the key instead of pasting it into chat."),
]

PROMPTS = ["Read https://example.com/pricing and list the plans as a table.",
           "Map docs.example.com, then read the five pages about authentication.",
           "Check these three pages again and show me only what changed.",
           "What has the house price trend been in Sacramento, CA over five years?"]
FAQ = [("What does it cost?", "A plain page is 1 credit and a rendered page is 3. The free trial is 1,000 credits once, with no card. After that it is $5 once for 35,000 credits or $15 a month for 110,000. Failed pages return their credits and there are no automatic overage charges."),
       ("Where do I get the key?", "At webscraper.apexflowlabs.com/trial.html. The key is shown once in your browser, so save it in a password manager and put it in an environment variable or your tool's secret store."),
       ("What will it not do?", "It does not solve CAPTCHAs, rotate proxies or disguise requests. Crawls obey robots.txt and Crawl-delay. A page that refuses a live read comes back as a dated Internet Archive copy, labelled as such.")]


def e(text):
    return html.escape(str(text), quote=True)


def head(title, description, path, extra=""):
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(title)}</title>'
            f'<meta name="description" content="{e(description)}"><link rel="canonical" href="{BASE}{path}"><meta name="theme-color" content="#050d13">'
            f'<meta property="og:title" content="{e(title)}"><meta property="og:description" content="{e(description)}"><meta property="og:type" content="website">'
            f'<meta property="og:url" content="{BASE}{path}"><meta property="og:image" content="{BASE}/assets/og-crawl.jpg"><meta name="twitter:card" content="summary_large_image">'
            f'<link rel="stylesheet" href="/app.css"><link rel="stylesheet" href="/integrations/int.css">{extra}</head><body>'
            '<nav aria-label="Primary"><a href="/">APEX FLOW WEB SCRAPER</a><span class="nav-links"><a href="/integrations/">Integrations</a><a href="/docs.html">Docs</a><a href="/#pricing">Pricing</a><a class="button" href="/trial.html">Get a free key</a></span></nav>')


FOOT = ('<footer><span>Apex Flow Web Scraper, by Apex Flow Labs.</span><a href="/integrations/">Integrations</a><a href="/docs.html">Docs</a><a href="/compare.html">Compare</a>'
        '<a href="/terms.html">Terms</a><a href="/privacy.html">Privacy</a></footer><script src="/integrations/int.js" defer></script></body></html>\n')


def tile(p):
    return (f'<a class="tile" href="/integrations/{p["slug"]}.html"><span class="mark" aria-hidden="true">{e(p["mark"])}</span>'
            f'<span class="tile-text"><b>{e(p["name"])}</b><span>{e(p["blurb"])}</span></span><span class="go" aria-hidden="true">→</span></a>')


def build_page(p):
    path = f'/integrations/{p["slug"]}.html'
    title = f'Web scraping for {p["name"]} · Apex Flow Web Scraper'
    description = f'Connect {p["name"]} to Apex Flow Web Scraper: read, crawl and watch public web pages as clean Markdown. {p["blurb"]} 1,000 free credits, no card.'
    ld = {"@context": "https://schema.org", "@type": "FAQPage",
          "mainEntity": [{"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in FAQ]}
    steps = "".join(f'<li><h3>{e(label)}</h3><div class="code"><button type="button" class="copy" data-copy>Copy</button><pre><code class="lang-{lang}">{e(code)}</code></pre></div></li>'
                    for label, lang, code in p["steps"])
    tools = "".join(f"<tr><td><code>{name}</code></td><td>{e(text)}</td></tr>" for name, text in TOOLS)
    others = "".join(f'<a href="/integrations/{o["slug"]}.html">{e(o["name"])}</a>' for o in PAGES if o["slug"] != p["slug"])
    faq = "".join(f"<details><summary>{e(q)}</summary><p>{e(a)}</p></details>" for q, a in FAQ)
    own = p["doc"].startswith(BASE)
    checked = (f'Setup follows <a href="{e(p["doc"])}">our API reference</a>.' if own else
               f'Checked against <a href="{e(p["doc"])}" rel="noopener">the {e(p["name"])} documentation</a> on {CHECKED}. If your version differs, follow its documentation and use the address and header shown here.')
    return (head(title, description, path, f'<script type="application/ld+json">{json.dumps(ld, ensure_ascii=False)}</script>') + f'''<main>
<p class="eyebrow"><a href="/integrations/">Integrations</a> / {e(p["group"])}</p>
<h1>Web scraping for <em>{e(p["name"])}</em>.</h1>
<p class="lede">{e(p["blurb"])} Your assistant gets {len(TOOLS)} tools for reading pages as clean Markdown, crawling sites, tracking changes and searching what it has read.</p>
<ol class="steps">
<li><h3>Get a key</h3><p>1,000 free credits, no card, no email. <a href="/trial.html">Open the free trial</a>, save the key, and put it in an environment variable named <code>{KEY}</code>.</p></li>
{steps}
<li><h3>Try it</h3><ul class="prompts">{"".join(f"<li>{e(x)}</li>" for x in PROMPTS)}</ul></li>
</ol>
<p class="note">{e(p["note"])}</p>
<p class="checked">{checked}</p>
<h2>What your assistant can do</h2>
<div class="tablewrap"><table><thead><tr><th>Tool</th><th>What it does</th></tr></thead><tbody>{tools}</tbody></table></div>
<h2>Questions</h2>{faq}
<p class="cta"><a class="button" href="/trial.html">Get 1,000 free credits →</a> <a class="button secondary" href="/docs.html">Read the API docs</a></p>
<h2>Other ways to connect</h2><p class="others">{others}</p>
</main>''' + FOOT)


def build_hub():
    groups = []
    for g in dict.fromkeys(p["group"] for p in PAGES):
        groups.append(f'<h2 id="{e(g.lower().replace(" ", "-"))}">{e(g)}</h2><div class="tiles">{"".join(tile(p) for p in PAGES if p["group"] == g)}</div>')
    tools = "".join(f"<li><code>{name}</code><span>{e(text)}</span></li>" for name, text in TOOLS)
    title = "Integrations: connect any AI assistant or app · Apex Flow Web Scraper"
    description = (f"Set up Apex Flow Web Scraper in Claude Code, Claude Desktop, Cursor, VS Code, Windsurf, Cline, Codex, Gemini CLI, Zed, LM Studio, the Claude and OpenAI APIs, "
                   f"LangChain, the Vercel AI SDK, n8n, Make, Zapier or plain HTTP. One address, {len(TOOLS)} tools.")
    ld = {"@context": "https://schema.org", "@type": "ItemList", "name": "Apex Flow Web Scraper integrations",
          "itemListElement": [{"@type": "ListItem", "position": i + 1, "name": p["name"], "url": f'{BASE}/integrations/{p["slug"]}.html'} for i, p in enumerate(PAGES)]}
    return (head(title, description, "/integrations/", f'<script type="application/ld+json">{json.dumps(ld, ensure_ascii=False)}</script>') + f'''<main class="hub">
<p class="eyebrow">Integrations</p>
<h1>Every AI gets <em>the live web.</em></h1>
<p class="lede">One address and one key connect the scraper to the assistant, framework or automation tool you already use. Pick yours for the exact setup.</p>
<div class="address"><span>MCP server</span><code>{MCP}</code><button type="button" class="copy" data-copy-text="{MCP}">Copy</button></div>
{"".join(groups)}
<h2>One address, {len(TOOLS)} tools</h2>
<ul class="toollist">{tools}</ul>
<p class="cta"><a class="button" href="/trial.html">Get 1,000 free credits →</a> <a class="button secondary" href="/docs.html">Read the API docs</a></p>
<p class="checked">Each setup page says which vendor documentation it was checked against and when. Something missing or out of date? <a href="mailto:apexflowlabs@gmail.com">Tell us</a>.</p>
</main>''' + FOOT)


def main():
    site = Path(__file__).resolve().parent.parent / "site"
    out = site / "integrations"
    out.mkdir(exist_ok=True)
    (out / "index.html").write_text(build_hub(), encoding="utf-8", newline="\n")
    for p in PAGES:
        (out / (p["slug"] + ".html")).write_text(build_page(p), encoding="utf-8", newline="\n")
    sitemap = site / "sitemap.xml"
    text = sitemap.read_text(encoding="utf-8")
    add = "".join(f"<url><loc>{BASE}{path}</loc></url>" for path in ["/integrations/"] + [f'/integrations/{p["slug"]}.html' for p in PAGES] if f"<loc>{BASE}{path}</loc>" not in text)
    if add:
        sitemap.write_text(text.replace("</urlset>", add + "</urlset>"), encoding="utf-8", newline="\n")
    print("wrote", len(PAGES) + 1, "pages;", add.count("<url>"), "sitemap entries added")


main()
