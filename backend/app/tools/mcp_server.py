"""MCP Server — exposes the tool registry as a Model Context Protocol server.

This means:
  - Claude Desktop can connect to this server and use stock analysis tools
  - Any MCP client (Cursor, Windsurf, etc.) gets financial analysis tools
  - Run standalone: python -m app.tools.mcp_server

Start with: python -m app.tools.mcp_server
Then in Claude Desktop config:
  {
    "mcpServers": {
      "agent-invest": {
        "command": "python",
        "args": ["-m", "app.tools.mcp_server"],
        "cwd": "/path/to/backend"
      }
    }
  }
"""

from __future__ import annotations

import asyncio
import json

import structlog

log = structlog.get_logger(__name__)


async def run_mcp_server():
    """Start the MCP server using stdio transport."""
    try:
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
        from mcp import types

        from app.tools.registry import ToolRegistry

        registry = ToolRegistry.get()
        server = Server("agent-invest-tools")

        @server.list_tools()
        async def list_tools() -> list[types.Tool]:
            schemas = registry.get_mcp_schemas()
            return [
                types.Tool(
                    name=s["name"],
                    description=s["description"],
                    inputSchema=s["inputSchema"],
                )
                for s in schemas
            ]

        @server.call_tool()
        async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
            result = await registry.execute(name, arguments)
            content = json.dumps(result.to_dict(), default=str, indent=2)
            return [types.TextContent(type="text", text=content)]

        log.info("mcp_server_starting", tools=len(registry.get_mcp_schemas()))
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    except ImportError:
        log.error("mcp_not_installed", hint="pip install mcp")
        raise


if __name__ == "__main__":
    asyncio.run(run_mcp_server())
