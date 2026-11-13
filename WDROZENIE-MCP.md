# Wdro�enie zdalnego MCP Interia

Pakiet zawiera `Dockerfile` i `render.yaml`. W Renderze wybierz **New > Blueprint**
i wska� repozytorium projektu. Render utworzy us�ug� `interia-mail-mcp`.

W panelu us�ugi ustaw sekrety `INTERIA_EMAIL`, `INTERIA_PASSWORD` i pozostaw
wygenerowany `MCP_BEARER_TOKEN`. Has�o Interii nie mo�e trafi� do repozytorium.

Po wdro�eniu trzeba doda� autoryzacj� Bearer/OAuth przed publicznym u�yciem.
Endpoint MCP b�dzie mia� adres w rodzaju:

`https://interia-mail-mcp.onrender.com/mcp`

Nie uruchamiaj publicznego serwera bez HTTPS i autoryzacji.
