"""Weiterleitungsangaben (``X-Forwarded-For``/``-Proto``) nur vom Eingangs-Proxy übernehmen.

Bis 1.439 lief uvicorn mit ``--forwarded-allow-ips "*"``: Es übernahm ``X-Forwarded-For``
von JEDEM Absender. Agenten rufen den Orchestrator direkt im Docker-Netz und konnten so
eine beliebige Absenderadresse vorgeben — bei der Community-Variante (Server ohne Caddy
veröffentlicht) sogar jeder aus dem Internet. Betroffen: Anmelde-Bremse, Rate-Limit,
Protokoll (``core/client_ip``).

Jetzt läuft uvicorn mit ``--no-proxy-headers``, und diese Middleware (die äußerste) wertet
die Angaben selbst aus — nur, wenn die echte Gegenstelle ein vertrauter Proxy ist:

* ``TRUSTED_PROXY_HOSTS``: Container-Namen (Dockers DNS), Adressen oder Netze (CIDR), mit
  Komma getrennt. Voreinstellung ``ai-employee-caddy``.
* Die Adresse ist der RECHTESTE Eintrag in ``X-Forwarded-For``, der kein vertrauter Proxy
  ist — der linke Teil stammt vom Client und ist fälschbar, sobald ein Proxy anhängt statt
  zu überschreiben (Caddy mit ``trusted_proxies``).
* Namen werden höchstens alle 60 s aufgelöst, im Hintergrund und mit Zeitgrenze; bis dahin
  gilt der letzte Stand. Eine Anfrage wartet nie auf DNS (außer beim allerersten Mal, kurz).
  Ein Fehlschlag gilt nur 5 s — nach einem Neustart von Caddy fängt es sich schnell.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import socket
import time

logger = logging.getLogger(__name__)

VORGABE = "ai-employee-caddy"
GUELTIG_S = 60.0
FEHLSCHLAG_S = 5.0
DNS_ZEIT_S = 2.0


def _eintraege() -> list[str]:
    roh = os.environ.get("TRUSTED_PROXY_HOSTS", VORGABE)
    return [n.strip() for n in roh.split(",") if n.strip()]


def _als_netz(eintrag: str):
    try:
        return ipaddress.ip_network(eintrag, strict=False)
    except ValueError:
        return None


def _adresse(text: str):
    text = text.strip()
    if text.startswith("[") and "]" in text:          # [IPv6]:port
        text = text[1:text.index("]")]
    elif text.count(":") == 1:                        # IPv4:port
        text = text.split(":", 1)[0]
    try:
        return ipaddress.ip_address(text)
    except ValueError:
        return None


class VertrauterProxy:
    """Reine ASGI-Middleware: Proxy-Kopfzeilen nur von vertrauten Gegenstellen."""

    def __init__(self, app) -> None:
        self.app = app
        self._netze: tuple = ()
        self._stand = -GUELTIG_S
        self._gueltig = 0.0
        self._aufgabe: asyncio.Task | None = None

    # ── Wem vertraut wird ────────────────────────────────────────────────
    async def _aufloesen(self) -> tuple:
        netze, namen = [], []
        for e in _eintraege():
            n = _als_netz(e)
            (netze.append(n) if n else namen.append(e))
        loop = asyncio.get_running_loop()
        fehlschlag = False
        for name in namen:
            try:
                for eintrag in await asyncio.wait_for(
                        loop.getaddrinfo(name, None, type=socket.SOCK_STREAM), DNS_ZEIT_S):
                    netze.append(ipaddress.ip_network(eintrag[4][0]))
            except (OSError, asyncio.TimeoutError):
                fehlschlag = True
                logger.debug("Vertrauter Proxy %r nicht auflösbar", name)
        self._netze = tuple(netze)
        self._stand = time.monotonic()
        self._gueltig = FEHLSCHLAG_S if fehlschlag else GUELTIG_S
        return self._netze

    async def netze(self) -> tuple:
        alt = time.monotonic() - self._stand >= self._gueltig
        if not alt:
            return self._netze
        if self._stand < 0:                      # allererstes Mal: kurz warten
            return await self._aufloesen()
        if self._aufgabe is None or self._aufgabe.done():
            self._aufgabe = asyncio.get_running_loop().create_task(self._aufloesen())
        return self._netze                       # bis dahin der letzte Stand

    @staticmethod
    def _vertraut(adresse, netze) -> bool:
        return adresse is not None and any(adresse.version == n.version and adresse in n for n in netze)

    # ── Auswerten ────────────────────────────────────────────────────────
    @staticmethod
    def _kopf(scope, name: bytes) -> str | None:
        werte = [v.decode("latin1") for k, v in scope.get("headers", []) if k.lower() == name]
        return ",".join(werte) if werte else None

    def _umschreiben(self, scope, netze) -> dict:
        scope = dict(scope)
        xff = self._kopf(scope, b"x-forwarded-for")
        if xff:
            kette = [_adresse(t) for t in xff.split(",") if t.strip()]
            client = next((a for a in reversed(kette) if a is not None and not self._vertraut(a, netze)), None)
            if client is None and kette and kette[0] is not None:
                client = kette[0]                # alles Proxys: der äußerste
            if client is not None:
                scope["client"] = (str(client), 0)
        proto = self._kopf(scope, b"x-forwarded-proto")
        if proto:
            schema = proto.split(",")[-1].strip().lower()
            if schema in ("http", "https"):
                scope["scheme"] = schema.replace("http", "ws") if scope["type"] == "websocket" else schema
        return scope

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            client = scope.get("client")
            if client:
                netze = await self.netze()
                if self._vertraut(_adresse(client[0]), netze):
                    scope = self._umschreiben(scope, netze)
        return await self.app(scope, receive, send)
