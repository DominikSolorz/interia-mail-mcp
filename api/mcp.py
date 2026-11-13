"""Vercel ASGI entrypoint for the Interia MCP server.

The function is mounted at /api/mcp by Vercel. FastMCP exposes the
streamable HTTP MCP transport without starting a local Uvicorn process.
"""
from asystent.serwer import mcp

app = mcp.http_app(
    path="/",
    transport="streamable-http",
    stateless_http=True,
)
