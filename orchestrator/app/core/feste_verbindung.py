"""HTTP-Verbindung zu EINER geprüften Adresse (gegen DNS-Rebinding).

Prüfen und Verbinden lösen den Namen sonst getrennt auf: Ein Angreifer mit eigenem
DNS liefert beim Prüfen eine öffentliche, beim Verbinden eine interne Adresse.
``FesteAdresse`` löst je Anfrage einmal auf (``url_guard.feste_adresse``), prüft jede
Adresse und verbindet per IP. Zertifikat und SNI gelten weiter für den Namen, der
Host-Kopf bleibt der Name.

Eine Stelle für alle, die von außen vorgegebene Server abfragen: Git-Server der
Projekte (``projekt_git``, mit Freigabe für Server im Firmennetz) und den
Identitätsanbieter der MCP-Anmeldung (``mcp_idp_auth``, nur öffentlich).
"""

from __future__ import annotations

import asyncio
import logging

import httpx

log = logging.getLogger(__name__)


class FesteAdresse(httpx.AsyncBaseTransport):
    def __init__(self, verify=True, innen: httpx.AsyncBaseTransport | None = None, aufloesen=None,
                 firmennetz_frei=None, name: str = "Verbindung"):
        self._innen = innen or httpx.AsyncHTTPTransport(verify=verify)
        self._aufloesen = aufloesen
        #: ``(host, port) -> bool``: darf dieses Ziel im Firmennetz liegen? Standard: nein.
        self._firmennetz_frei = firmennetz_frei or (lambda host, port: False)
        self._name = name

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        from app.core import url_guard

        host, port = request.url.host, request.url.port or 443
        kw = {"aufloesen": self._aufloesen} if self._aufloesen else {}
        try:
            ip = await asyncio.to_thread(url_guard.feste_adresse, host, port,
                                         self._firmennetz_frei(host, port), **kw)
        except ValueError as e:
            log.info("[%s] Verbindung zu %s:%s abgelehnt: %s", self._name, host[:100], port, e)
            raise httpx.ConnectError("Server nicht erreichbar", request=request) from None
        request.url = request.url.copy_with(host=ip)
        request.extensions = {**request.extensions, "sni_hostname": host}
        return await self._innen.handle_async_request(request)

    async def aclose(self) -> None:
        await self._innen.aclose()
