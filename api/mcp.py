"""Vercel ASGI entrypoint for the Interia MCP server."""
from asystent.serwer import mcp

# FastMCP 1.x exposes the ASGI application through this method.
app = mcp.streamable_http_app()
