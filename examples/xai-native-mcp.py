"""Connect Grok's native Python SDK to Apex Flow Web Scraper via remote MCP.

Install with ``python -m pip install xai-sdk``. Requires XAI_API_KEY and
APEX_SCRAPER_API_KEY in the process environment; optionally set XAI_MODEL.
This example enables only scrape_page. A run can consume xAI tokens and Apex
scraper credits.
"""

import os

from xai_sdk import Client
from xai_sdk.chat import user
from xai_sdk.tools import mcp


client = Client(api_key=os.environ["XAI_API_KEY"])
chat = client.chat.create(
    model=os.environ.get("XAI_MODEL", "grok-4.7"),
    tools=[
        mcp(
            server_url="https://webscraper.apexflowlabs.com/v1/mcp",
            server_label="apex_flow_scraper",
            allowed_tool_names=["scrape_page"],
            extra_headers={
                "Authorization": f"Bearer {os.environ['APEX_SCRAPER_API_KEY']}",
            },
        )
    ],
)
chat.append(user("Read https://example.com and summarize the page in three sentences."))

for response, chunk in chat.stream():
    if chunk.content:
        print(chunk.content, end="", flush=True)
