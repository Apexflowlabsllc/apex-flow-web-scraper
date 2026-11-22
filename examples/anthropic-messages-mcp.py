"""Connect the Anthropic Messages API to Apex Flow's remote MCP server.

Requires ANTHROPIC_API_KEY, APEX_SCRAPER_API_KEY, and ANTHROPIC_MODEL in the
environment. The MCP beta is approval-controlled by the application using it.
"""

import os

from anthropic import Anthropic


client = Anthropic()
response = client.beta.messages.create(
    model=os.environ["ANTHROPIC_MODEL"],
    max_tokens=1200,
    messages=[
        {
            "role": "user",
            "content": "Scrape https://example.com and summarize the main page in three sentences.",
        }
    ],
    mcp_servers=[
        {
            "type": "url",
            "name": "apex_flow_scraper",
            "url": "https://webscraper.apexflowlabs.com/v1/mcp",
            "authorization_token": os.environ["APEX_SCRAPER_API_KEY"],
        }
    ],
    tools=[
        {
            "type": "mcp_toolset",
            "mcp_server_name": "apex_flow_scraper",
            "default_config": {"enabled": False},
            "configs": {"scrape_page": {"enabled": True}},
        }
    ],
    betas=["mcp-client-2025-11-20"],
)

for block in response.content:
    if block.type == "text":
        print(block.text)
