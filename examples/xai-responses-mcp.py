"""Use Grok's Responses API to call Apex Flow Web Scraper through remote MCP.

Install the OpenAI-compatible Python client with ``python -m pip install openai``.
Requires XAI_API_KEY and APEX_SCRAPER_API_KEY in the process environment.
Set XAI_MODEL to override the default model. A run can use xAI tokens and Apex
scraper credits; this example restricts Grok to the scrape_page tool.
"""

import os

from openai import OpenAI


client = OpenAI(
    api_key=os.environ["XAI_API_KEY"],
    base_url="https://api.x.ai/v1",
)

response = client.responses.create(
    model=os.environ.get("XAI_MODEL", "grok-4.7"),
    tools=[
        {
            "type": "mcp",
            "server_label": "apex_flow_scraper",
            "server_description": "Read public web pages as clean Markdown with source receipts.",
            "server_url": "https://webscraper.apexflowlabs.com/v1/mcp",
            "headers": {
                "Authorization": f"Bearer {os.environ['APEX_SCRAPER_API_KEY']}",
            },
            "allowed_tools": ["scrape_page"],
        }
    ],
    input="Read https://example.com and summarize the page in three sentences.",
)

print(response.output_text)
