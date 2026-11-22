"""Use the Apex Flow Web Scraper hosted MCP server from a LangChain agent.

Install with: pip install "langchain[mcp]>=1.4.0,<2" langchain-openai
Set APEX_SCRAPER_API_KEY, OPENAI_API_KEY, and optionally LANGCHAIN_MODEL.
"""

import argparse
import asyncio
import os

from fastmcp import Client
from langchain.agents import create_agent
from langchain.mcp import MCPAdapter


async def run(prompt: str) -> str:
    api_key = os.environ.get("APEX_SCRAPER_API_KEY")
    if not api_key:
        raise SystemExit("Set APEX_SCRAPER_API_KEY to an Apex API key.")

    client = Client("https://webscraper.apexflowlabs.com/v1/mcp", auth=api_key)
    async with MCPAdapter(client) as adapter:
        discovered = await adapter.list_tools()
        scraper = next((tool for tool in discovered if tool.name == "scrape_page"), None)
        if scraper is None:
            raise RuntimeError("The Apex MCP server did not expose scrape_page.")

        model = os.environ.get("LANGCHAIN_MODEL", "openai:gpt-4.1")
        agent = create_agent(model=model, tools=[scraper])
        result = await agent.ainvoke({"messages": [{"role": "user", "content": prompt}]})
        messages = result.get("messages", [])
        if not messages:
            raise RuntimeError("LangChain returned no agent messages.")
        return str(messages[-1].content)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", help="A task that uses the public-page scraper")
    args = parser.parse_args()
    print(asyncio.run(run(args.prompt)))


if __name__ == "__main__":
    main()
