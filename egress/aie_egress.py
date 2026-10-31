"""mitmproxy-Addon des Egress-Proxys (siehe orchestrator/app/core/egress.py).

* Jeder Agent meldet sich beim CONNECT mit Kennung und Agenten-Token an.
* TLS wird nur fuer Ziele aufgebrochen, an die einer seiner Keys darf — alles
  andere laeuft als Tunnel, der Proxy sieht davon nichts.
* In aufgebrochenen Verbindungen werden Platzhalter durch die echten Werte
  ersetzt — nur, wenn der Orchestrator es fuer genau dieses Ziel erlaubt.
* Ziele mit interner Adresse sind gesperrt; verbunden wird mit der geprueften IP.

Die Logik steht in egress_logik.py (ohne mitmproxy, testbar).
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from mitmproxy import ctx, http, tls
from mitmproxy.proxy import server_hooks

import egress_logik as logik

log = logging.getLogger("aie_egress")

_TEXT = ("text/", "application/json", "application/x-www-form-urlencoded", "application/xml",
         "application/graphql", "application/javascript")


def _antwort(status: int, text: str, **kopf) -> http.Response:
    return http.Response.make(status, text.encode(), {"Content-Type": "text/plain; charset=utf-8", **kopf})


def _ist_text(kopf) -> bool:
    art = (kopf.get("content-type") or "").lower()
    return art.startswith(_TEXT)


class AieEgress:
    def __init__(self):
        self.orch = logik.Orchestrator(
            os.environ.get("ORCHESTRATOR_URL", "http://ai-employee-orchestrator:8000"),
            os.environ.get("EGRESS_PROXY_TOKEN", ""),
        )
        self.agenten: dict[str, tuple[str, str]] = {}   # client_conn.id → Agent
        self.ziele: dict[str, str] = {}                 # client_conn.id → CONNECT-Ziel

    async def _hosts(self, agent):
        return await asyncio.to_thread(self.orch.hosts, agent)

    # ── Anmeldung ───────────────────────────────────────────────────────
    async def http_connect(self, flow: http.HTTPFlow):
        agent = logik.anmeldung(flow.request.headers.get("Proxy-Authorization"))
        try:
            hosts = await self._hosts(agent) if agent else None
        except logik.OrchestratorFehler:
            flow.response = _antwort(503, "Egress-Proxy: Orchestrator nicht erreichbar")
            return
        if hosts is None:
            flow.response = _antwort(407, "Anmeldung am Egress-Proxy fehlt",
                                     **{"Proxy-Authenticate": 'Basic realm="ai-employee"'})
            return
        self.agenten[flow.client_conn.id] = agent
        self.ziele[flow.client_conn.id] = flow.request.host.lower().rstrip(".")

    def client_disconnected(self, client):
        self.agenten.pop(client.id, None)
        self.ziele.pop(client.id, None)

    # ── Nur interne Ziele sperren, mit der geprueften Adresse verbinden ──
    async def server_connect(self, data: server_hooks.ServerConnectionHookData):
        if not data.server.address:
            return
        host, port = data.server.address
        try:
            ip = await asyncio.to_thread(logik.ziel_adresse, host, port)
        except logik.Abgelehnt as e:
            data.server.error = e.text
            return
        data.server.address = (ip, port)

    # ── TLS nur aufbrechen, wo ein Key hin darf ─────────────────────────
    async def tls_clienthello(self, data: tls.ClientHelloData):
        cid = data.context.client.id
        agent, ziel = self.agenten.get(cid), self.ziele.get(cid)
        sni = (data.client_hello.sni or "").lower().rstrip(".")
        aufbrechen = False
        if agent and ziel and sni == ziel:
            try:
                aufbrechen = logik.host_passt(ziel, await self._hosts(agent) or [])
            except logik.OrchestratorFehler:
                aufbrechen = False
        if not aufbrechen:
            data.ignore_connection = True

    # ── Platzhalter ersetzen ────────────────────────────────────────────
    async def request(self, flow: http.HTTPFlow):
        cid = flow.client_conn.id
        agent = self.agenten.get(cid)
        if agent is None:
            await self._ohne_tunnel(flow)
            return
        gefunden: set[str] = set()
        for name, wert in flow.request.headers.items(multi=True):
            gefunden |= logik.kopf_platzhalter(name, wert)
        text = None
        if flow.request.raw_content and len(flow.request.raw_content) <= logik.MAX_KOERPER:
            text = flow.request.get_text(strict=False)
        try:
            gefunden |= logik.platzhalter_ausserhalb_der_koepfe(
                flow.request.path, flow.request.headers.get("content-type", ""), text)
        except logik.Abgelehnt as e:
            flow.response = _antwort(e.status, e.text)
            return
        if not gefunden:
            return
        ziel = self.ziele.get(cid, "")
        if flow.request.scheme != "https" or not ziel:
            flow.response = _antwort(403, "Keys gehen nur über HTTPS hinaus")
            return
        try:
            werte = await asyncio.to_thread(self.orch.werte, agent, ziel, gefunden)
        except logik.Abgelehnt as e:
            flow.response = _antwort(e.status, e.text)
            return
        except logik.OrchestratorFehler:
            flow.response = _antwort(503, "Egress-Proxy: Orchestrator nicht erreichbar")
            return
        flow.request.headers.fields = tuple(
            (k, logik.kopf_ersetzen(k.decode("latin-1"), v.decode("latin-1"), werte).encode("latin-1"))
            for k, v in flow.request.headers.fields
        )
        flow.request.path = logik.ersetzen(flow.request.path, werte)
        if text is not None and logik.platzhalter_in(text):
            flow.request.set_text(logik.ersetzen(text, werte))
        flow.metadata["aie_werte"] = werte

    async def _ohne_tunnel(self, flow: http.HTTPFlow):
        """Klartext-HTTP direkt an den Proxy: CA-Zertifikat holen oder weiterleiten."""
        if flow.request.host == logik.CA_HOST:
            pem = Path(os.path.expanduser(ctx.options.confdir), "mitmproxy-ca-cert.pem")
            flow.response = (http.Response.make(200, pem.read_bytes(), {"Content-Type": "application/x-pem-file"})
                             if pem.exists() else _antwort(503, "CA noch nicht erzeugt"))
            return
        agent = logik.anmeldung(flow.request.headers.get("Proxy-Authorization"))
        try:
            hosts = await self._hosts(agent) if agent else None
        except logik.OrchestratorFehler:
            flow.response = _antwort(503, "Egress-Proxy: Orchestrator nicht erreichbar")
            return
        if hosts is None:
            flow.response = _antwort(407, "Anmeldung am Egress-Proxy fehlt",
                                     **{"Proxy-Authenticate": 'Basic realm="ai-employee"'})
            return
        flow.request.headers.pop("Proxy-Authorization", None)
        if logik.platzhalter_in(flow.request.url, *flow.request.headers.values()):
            flow.response = _antwort(403, "Keys gehen nur über HTTPS hinaus")

    # ── Zurueckgespiegelte Werte wieder verdecken ───────────────────────
    def response(self, flow: http.HTTPFlow):
        werte = flow.metadata.get("aie_werte")
        if not werte or flow.response is None:
            return
        flow.response.headers.fields = tuple(
            (k, logik.zurueck_maskieren(v.decode("latin-1"), werte).encode("latin-1"))
            for k, v in flow.response.headers.fields
        )
        roh = flow.response.raw_content
        if roh and len(roh) <= logik.MAX_KOERPER and _ist_text(flow.response.headers):
            text = flow.response.get_text(strict=False)
            if text:
                flow.response.set_text(logik.zurueck_maskieren(text, werte))


addons = [AieEgress()]
