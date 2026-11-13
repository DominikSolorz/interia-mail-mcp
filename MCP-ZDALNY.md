# Zdalna integracja Interia Poczta

`asystent.serwer` pozostaje transportem lokalnym dla Codexa i Claude. Nowy
`asystent.serwer_http` udost�pnia ten sam serwer przez Streamable HTTP dla
zdalnego konektora ChatGPT.

Przed publikacj� trzeba doda� reverse proxy HTTPS, uwierzytelnianie OAuth oraz
sekrety Interii jako zmienne �rodowiskowe lub sekret hostingu. Nie wystawiaj
portu bezpo�rednio do Internetu i nie zapisuj has�a w repozytorium.

Uruchomienie lokalne do testu:

```text
mcp-http.cmd
```

Po wdro�eniu do hostingu adresem integracji b�dzie publiczny endpoint MCP,
zwykle `https://twoja-domena.example/mcp`.
