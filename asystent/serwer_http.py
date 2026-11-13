"""Zdalny transport MCP dla integracji z ChatGPT/Codex.

Uruchamiaj za reverse proxy HTTPS. Domy�lnie udost�pnia ten sam zestaw
bezpiecznych narz�dzi co serwer stdio; nie dodaje trwa�ego usuwania ani
automatycznego wypisywania.
"""

import os

from .serwer import mcp


def main() -> None:
    # FastMCP obs�uguje transport Streamable HTTP wymagany przez zdalne
    # konektory MCP. Reverse proxy powinien wymusza� HTTPS i autoryzacj�.
    # Host ustawia reverse proxy. Token jest przygotowany jako sekret hostingu;
    # publiczne wdro�enie musi wymusza� HTTPS przed udost�pnieniem endpointu.
    os.environ.setdefault("FASTMCP_HOST", "0.0.0.0")
    os.environ.setdefault("FASTMCP_PORT", "8000")
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
