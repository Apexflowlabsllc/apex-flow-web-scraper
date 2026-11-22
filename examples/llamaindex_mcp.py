"""Use the Apex Flow Web Scraper MCP server from a LlamaIndex agent.

Install with: pip install "llama-index-tools-mcp==0.6.0" llama-index-llms-openai
Set APEX_SCRAPER_API_KEY, OPENAI_API_KEY, and optionally LLAMAINDEX_MODEL.

The MCP client currently uses MCP SDK 2.x. Run this in the agent's own
environment; do not install the LlamaIndex MCP extra into the Apex server
environment, which currently uses MCP SDK 1.x.
"""

import argparse
import asyncio
import os

from llama_index.core.agent.workflow import FunctionAgent
from llama_index.llms.openai import OpenAI
from llama_index.tools.mcp import BasicMCPClient, McpToolSpec


async def run(prompt: str) -> str:
    api_key = os.environ.get("APEX_SCRAPER_API_KEY")
    if not api_key:
        raise SystemExit("Set APEX_SCRAPER_API_KEY to an Apex API key.")

    base_url = os.environ.get("APEX_SCRAPER_MCP_URL", "https://webscraper.apexflowlabs.com/v1/mcp")
    client = BasicMCPClient(
        base_url,
        headers={"Authorization": f"Bearer {api_key}"},
    )
    tool_spec = McpToolSpec(client=client, allowed_tools=["scrape_page"])
    tools = await tool_spec.to_tool_list_async()
    if not tools:
        raise RuntimeError("The Apex MCP server did not expose scrape_page.")

    agent = FunctionAgent(
        name="Apex web research agent",
        description="Researches public web pages with the Apex Flow Web Scraper.",
        llm=OpenAI(model=os.environ.get("LLAMAINDEX_MODEL", "gpt-4.1-mini")),
        tools=tools,
        system_prompt=(
            "Use the scrape_page tool to read public pages relevant to the request. "
            "Summarize only what the scraped page supports and include its URL."
        ),
    )
    response = await agent.run(prompt)
    return str(response)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", help="A task that uses public-page scraping")
    args = parser.parse_args()
    print(asyncio.run(run(args.prompt)))


if __name__ == "__main__":
    main()
