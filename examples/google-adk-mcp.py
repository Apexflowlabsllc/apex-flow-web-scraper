"""Connect a Google ADK agent powered by Gemini to Apex Flow via remote MCP.

Requires GOOGLE_API_KEY, APEX_SCRAPER_API_KEY, and optionally GEMINI_MODEL.
"""

import os

from google.adk.agents import LlmAgent
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams


root_agent = LlmAgent(
    model=os.environ.get("GEMINI_MODEL", "gemini-flash-latest"),
    name="apex_flow_web_researcher",
    instruction=(
        "Use Apex Flow Web Scraper when the user asks you to read a public webpage. "
        "Report the source URL and distinguish live results from archive fallback."
    ),
    tools=[
        McpToolset(
            connection_params=StreamableHTTPConnectionParams(
                url="https://webscraper.apexflowlabs.com/v1/mcp",
                headers={
                    "Authorization": f"Bearer {os.environ['APEX_SCRAPER_API_KEY']}",
                },
            ),
        )
    ],
)
