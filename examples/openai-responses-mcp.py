"""Use Apex Flow Web Scraper as a remote MCP tool in the OpenAI Responses API."""

import os

from openai import OpenAI


client = OpenAI()
response = client.responses.create(
    model="gpt-6.2-astra",
    tools=[
        {
            "type": "mcp",
            "server_label": "apex_flow_scraper",
            "server_description": "Read public web pages as clean Markdown for research and extraction.",
            "server_url": "https://webscraper.apexflowlabs.com/v1/mcp",
            "authorization": f"Bearer {os.environ['APEX_SCRAPER_API_KEY']}",
            "require_approval": "always",
            "allowed_tools": ["scrape_page"],
        }
    ],
    input="Read https://example.com and summarize the page in three sentences.",
)

print(response.output_text)
