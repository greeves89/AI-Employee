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
import json
import logging
import os
from pathlib import Path

from mitmproxy import ctx, http, tls
from mitmproxy.proxy import server_hooks

import importlib

import egress_logik as logik
import anonym

# mitmproxy laedt dieses Skript bei Aenderung neu, das importierte Modul aber nicht —
# ohne das liefe nach einem Update neue Addon-Logik mit alter Pruefung.
logik = importlib.reload(logik)
anonym = importlib.reload(anonym)

log = logging.getLogger("aie_egress")

#: Ab dieser Größe wird in einem eigenen Prozess erkannt: Pythons ``re`` gibt den GIL nicht
#: frei — ein Thread hielte bei 1 MB Ziffernfolgen (Pi: ~1 s) den Proxy für ALLE Agenten an.
_PROZESS_AB = 64 * 1024
_pool = None


def _prozesse():
    global _pool
    if _pool is None:
        import concurrent.futures
        import multiprocessing

        _pool = concurrent.futures.ProcessPoolExecutor(
            max_workers=2, mp_context=multiprocessing.get_context("spawn"))
    return _pool


async def _erkennen(klar: bytes, schluessel: str, begriffe: list[str]):
    if len(klar) < _PROZESS_AB:
        return anonym.anfrage_anonymisieren(klar, schluessel, begriffe)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_prozesse(), anonym.anfrage_anonymisieren, klar, schluessel, begriffe)


def _zstd_begrenzt(roh: bytes, hoechstens: int) -> bytes:
    """zstd entpacken, höchstens ``hoechstens`` Bytes.

    mitmproxy bis 12.2 (Python 3.13) brachte das Paket ``zstandard`` mit; ab 12.3 läuft es auf
    Python 3.14, das zstd selbst kann (``compression.zstd``) — ``zstandard`` fehlt dort. Beide
    Wege begrenzen die Ausgabe (Kompressionsbombe)."""
    try:
        from compression import zstd   # Python ≥ 3.14
    except ImportError:
        import io

        import zstandard
        with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(roh)) as r:
            return r.read(hoechstens)
    return zstd.ZstdDecompressor().decompress(roh, max_length=hoechstens)


def _entpacken(roh: bytes, kodierung: str) -> bytes:
    """Anfragekörper entpacken, nie über ``MAX_ANTWORT`` hinaus (Kompressionsbombe)."""
    if kodierung == "zstd":
        daten = _zstd_begrenzt(roh, logik.MAX_ANTWORT + 1)
        if len(daten) > logik.MAX_ANTWORT:
            raise logik.Abgelehnt(413, "zu groß")
        return daten
    return logik.entpacken_begrenzt(roh, kodierung)


def _antwort(status: int, text: str, **kopf) -> http.Response:
    return http.Response.make(status, text.encode(), {"Content-Type": "text/plain; charset=utf-8", **kopf})


class AieEgress:
    def __init__(self):
        self.orch = logik.Orchestrator(
            os.environ.get("ORCHESTRATOR_URL", "http://ai-employee-orchestrator:8000"),
            os.environ.get("EGRESS_PROXY_TOKEN", ""),
        )
        self.agenten: dict[str, tuple[str, str]] = {}   # client_conn.id → Agent
        self.ziele: dict[str, tuple[str, int]] = {}     # client_conn.id → CONNECT-Ziel (Host, Port)
        self.maske = logik.Maske()

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
        # Eine Client-Verbindung = ein Tunnel. Ein zweites CONNECT (etwa im Tunnel)
        # koennte sonst das gemerkte Ziel ueberschreiben.
        if flow.client_conn.id in self.ziele:
            flow.response = _antwort(403, "Nur ein Tunnel je Verbindung")
            return
        self.agenten[flow.client_conn.id] = agent
        self.ziele[flow.client_conn.id] = (flow.request.host.lower().rstrip("."), flow.request.port)

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
        agent, (ziel, port) = self.agenten.get(cid), self.ziele.get(cid, ("", 0))
        sni = (data.client_hello.sni or "").lower().rstrip(".")
        aufbrechen = False
        if agent and ziel and sni == ziel and port == logik.KEY_PORT:
            try:
                # Zu einem Modellanbieter immer mit frischer Einstellung: Wurde gerade der
                # Anonymisierer eingeschaltet, darf keine neue Verbindung im Klartext bleiben,
                # nur weil die Anmeldung noch eine Minute zwischengespeichert ist.
                frisch = sni in logik.MODELL_ANBIETER
                hosts = (await asyncio.to_thread(self.orch.hosts, agent, frisch) if frisch
                         else await self._hosts(agent))
                aufbrechen = logik.host_passt(ziel, hosts or [])
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
        abgelehnt = await self._anonymisieren(flow, agent)
        if abgelehnt is not None:
            flow.response = abgelehnt
            return
        gefunden: set[str] = set()
        for name, wert in flow.request.headers.items(multi=True):
            gefunden |= logik.kopf_platzhalter(name, wert)
        text = None
        # Komprimierte Koerper nicht entpacken (1 MB gzip kann 1 GB werden). Darin
        # bleibt ein Platzhalter eben Platzhalter.
        kodierung = flow.request.headers.get("content-encoding", "identity").strip().lower()
        roh = flow.request.raw_content
        if roh and len(roh) <= logik.MAX_KOERPER and kodierung in ("", "identity"):
            text = flow.request.get_text(strict=False)
        gefunden |= logik.platzhalter_ausserhalb_der_koepfe(
            flow.request.path, flow.request.headers.get_all("content-type"), text)
        if not gefunden:
            return
        ziel, port = self.ziele.get(cid, ("", 0))
        # Das Ziel auch aus DIESER Anfrage ableiten: TLS-Name der Serververbindung und
        # Port muessen zum Tunnel passen (aufgebrochen wird nur bei SNI == Tunnelziel).
        sni = (flow.server_conn.sni or "").lower().rstrip(".")
        if sni != ziel or (flow.server_conn.address or ("", 0))[1] != port:
            ziel = ""
        if flow.request.scheme != "https" or not ziel or port != logik.KEY_PORT:
            flow.response = _antwort(403, "Keys gehen nur über HTTPS (Port 443) hinaus")
            return
        # Domain-Fronting: Der Key gilt fuer das Ziel der Verbindung — eine andere
        # Adresse im Host-Kopf (bzw. :authority) wuerde ihn am selben CDN woanders abliefern.
        if not logik.host_werte_passen(
                flow.request.headers.get_all("host") + [flow.request.authority or ""], ziel):
            flow.response = _antwort(421, "Host-Kopf passt nicht zum Ziel der Verbindung")
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
        flow.request.path = logik.pfad_ersetzen(flow.request.path, werte)
        # Nur ein eindeutiges Formular wird neu kodiert — JSON & Co. bleiben byte-gleich.
        if (text is not None and logik.platzhalter_in(text)
                and logik.ist_formular(flow.request.headers.get_all("content-type"), text)):
            flow.request.set_text(logik.parameter_ersetzen(text, werte))
        flow.metadata["aie_werte"] = werte
        self.maske.merken(agent[0], werte)

    # ── Anonymisierer (egress/anonym.py) ───────────────────────────────
    def _anonym_einstellung(self, flow: http.HTTPFlow) -> dict | None:
        """Einstellung, wenn diese Verbindung zu einem Modellanbieter eines Agenten mit
        eingeschaltetem Anonymisierer geht — sonst None."""
        agent = self.agenten.get(flow.client_conn.id)
        cfg = self.orch.anonym(agent) if agent else None
        if not cfg:
            return None
        ziel = self.ziele.get(flow.client_conn.id, ("", 0))[0]
        return cfg if logik.host_passt(ziel, cfg.get("ziele") or []) else None

    async def _anonymisieren(self, flow: http.HTTPFlow, agent) -> http.Response | None:
        """Personendaten in der Anfrage durch Platzhalter ersetzen. Geht das nicht sicher,
        wird die Anfrage abgewiesen — lieber keine als eine mit Klartext."""
        cfg = self._anonym_einstellung(flow)
        if cfg is None:
            return None
        roh = flow.request.raw_content
        if roh is None:
            # Gestreamter Körper (nicht gepuffert): ungeprüft nicht hinaus.
            if flow.request.headers.get("content-length", "0") != "0" or flow.request.headers.get("transfer-encoding"):
                return _antwort(413, "Anonymisierer: Anfrage nicht prüfbar")
            return None
        if not roh:
            return None
        kodierung = flow.request.headers.get("content-encoding", "identity").strip().lower()
        if len(roh) > logik.MAX_ANTWORT:
            return _antwort(413, "Anonymisierer: Anfrage zu groß zum Prüfen")
        try:
            klar = _entpacken(roh, kodierung)
        except logik.Abgelehnt:
            return _antwort(415, f"Anonymisierer: Kodierung {kodierung} nicht prüfbar")
        try:
            # Außerhalb der Ereignisschleife: die Erkennung über bis zu 32 MB darf den Proxy
            # für die anderen Agenten nicht anhalten.
            neu, zuordnung = await _erkennen(klar, cfg["schluessel"], cfg.get("begriffe") or [])
        except (ValueError, UnicodeDecodeError):
            return _antwort(400, "Anonymisierer: Anfrage ist kein JSON und wird nicht unbesehen gesendet")
        if zuordnung or klar is not roh:
            # Unkomprimiert weiter: sonst müsste neu verpackt werden, und der Anbieter nimmt beides.
            flow.request.headers.pop("content-encoding", None)
            flow.request.set_content(neu)
        if zuordnung:
            flow.metadata["aie_anonym"] = zuordnung
            log.info("Anonymisierer: %d Wert(e) ersetzt (Agent %s)", len(zuordnung), agent[0])
        return None

    def requestheaders(self, flow: http.HTTPFlow):
        # Aufgebrochene Verbindung eines Agenten: Antworten unkomprimiert anfordern —
        # verdecken laesst sich nur, was lesbar ist, und Entpacken kostet Speicher.
        if flow.client_conn.id in self.agenten:
            flow.request.headers["accept-encoding"] = "identity"
        # Anonymisierer: den Körper IMMER puffern. ``stream_large_bodies`` (Compose: 5m)
        # reichte große Anfragen sonst ungesehen durch — ein langer Verlauf hat schnell 5 MB.
        if self._anonym_einstellung(flow) is not None:
            try:
                zu_gross = int(flow.request.headers.get("content-length") or 0) > logik.MAX_ANTWORT
            except ValueError:
                zu_gross = True
            if zu_gross:
                flow.response = _antwort(413, "Anonymisierer: Anfrage zu groß zum Prüfen")
                return
            flow.request.stream = False

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
    def _maske(self, flow: http.HTTPFlow) -> dict[str, str]:
        agent = self.agenten.get(flow.client_conn.id)
        werte = dict(self.maske.werte(agent[0])) if agent else {}
        werte.update(flow.metadata.get("aie_werte") or {})
        return werte

    def responseheaders(self, flow: http.HTTPFlow):
        """Gibt es fuer diesen Agenten etwas zu verdecken, kommt keine Antwort ungeprueft durch.

        * Kopfzeilen: sofort hier — gestreamt waeren sie sonst schon unterwegs.
        * Ohne feste Laenge (Ereignisstroeme der Sprachmodelle, chunked):
          verdecken im Durchlauf (``StromMaske``), ohne zu puffern. git-Pakete nicht.
        * Mit fester Laenge oder komprimiert: ganz puffern und in ``response``
          verdecken (eine geaenderte Laenge passte nicht mehr zur Content-Length);
          zu gross → Abbruch statt Durchlass.
        """
        if flow.response is None:
            return
        art = (flow.response.headers.get("content-type") or "").lower()
        werte = self._maske(flow)
        zuordnung = flow.metadata.get("aie_anonym")
        if zuordnung:
            # Anonymisierer: Platzhalter in der Antwort wieder einsetzen. Ereignisströme
            # verstehen (zerteilte Platzhalter über Ereignisgrenzen), sonst ganz puffern.
            if werte:
                flow.response.headers.fields = tuple(
                    (k, logik.zurueck_maskieren(v.decode("latin-1"), werte).encode("latin-1"))
                    for k, v in flow.response.headers.fields
                )
            if art.startswith("text/event-stream"):
                einsetzer = anonym.SseEinsetzer(zuordnung)
                maske = logik.StromMaske(werte) if werte else None
                flow.response.stream = (lambda b: einsetzer(maske(b))) if maske else einsetzer
                return
            try:
                zu_gross = int(flow.response.headers.get("content-length") or 0) > logik.MAX_ANTWORT
            except ValueError:
                zu_gross = True
            if zu_gross:
                flow.kill()
                return
            flow.response.stream = False
            return
        if not werte:
            if art.startswith("text/event-stream"):
                flow.response.stream = True     # sofort durchreichen, nicht sammeln
            return
        flow.response.headers.fields = tuple(
            (k, logik.zurueck_maskieren(v.decode("latin-1"), werte).encode("latin-1"))
            for k, v in flow.response.headers.fields
        )
        # git-Pakete: binaer, mit Pruefsumme am Ende — ein zurueckgehaltener Wert-Anfang
        # dort verstuemmelte das Paket. Ein Wert gelangt ueber den Proxy nie in ein
        # Repository (Platzhalter in Inhalten werden nie ersetzt); gestreamt wie bisher.
        if logik.ist_git_antwort(flow.request.path, art):
            return
        kodierung = (flow.response.headers.get("content-encoding") or "identity").strip().lower()
        laenge = flow.response.headers.get("content-length")
        if kodierung not in ("", "identity") or laenge is not None:
            try:
                zu_gross = int(laenge or 0) > logik.MAX_ANTWORT
            except ValueError:
                zu_gross = True
            if zu_gross:
                flow.kill()
                return
            flow.response.stream = False
            return
        flow.response.stream = logik.StromMaske(werte)

    def response(self, flow: http.HTTPFlow):
        if flow.response is None:
            return
        zuordnung = flow.metadata.get("aie_anonym")
        if zuordnung and flow.response.stream is False and flow.response.raw_content:
            try:
                inhalt = logik.entpacken_begrenzt(flow.response.raw_content,
                                                  flow.response.headers.get("content-encoding", ""))
                text = inhalt.decode("utf-8")
                try:
                    neu = json.dumps(anonym.einsetzen_json(json.loads(text), zuordnung),
                                     ensure_ascii=False).encode("utf-8")
                except ValueError:
                    neu = anonym.einsetzen(text, zuordnung).encode("utf-8")
                flow.response.headers.pop("content-encoding", None)
                flow.response.set_content(neu)
            except Exception:  # noqa: BLE001
                log.exception("Anonymisierer: Antwort liess sich nicht zurückübersetzen")
        werte = self._maske(flow)
        if not werte:
            return
        try:
            flow.response.headers.fields = tuple(
                (k, logik.zurueck_maskieren(v.decode("latin-1"), werte).encode("latin-1"))
                for k, v in flow.response.headers.fields
            )
            roh = flow.response.raw_content
            if roh:
                if len(roh) > logik.MAX_ANTWORT:
                    raise logik.Abgelehnt(502, "Antwort zu gross zum Prüfen")
                inhalt = logik.entpacken_begrenzt(roh, flow.response.headers.get("content-encoding", ""))
                neu = logik.zurueck_maskieren_bytes(inhalt, werte)
                if neu != inhalt or inhalt is not roh:
                    flow.response.headers.pop("content-encoding", None)
                    flow.response.set_content(neu)
        except Exception:  # noqa: BLE001 — lieber keine Antwort als eine mit dem Wert
            log.exception("Antwort liess sich nicht verdecken")
            flow.response = _antwort(502, "Egress-Proxy: Antwort konnte nicht geprüft werden")

    def websocket_message(self, flow: http.HTTPFlow):
        # Anonymisierer: Websockets zu Modellanbietern werden nicht anonymisiert — schließen
        # statt Klartext durchlassen (Codex nutzt standardmäßig HTTP mit Ereignisstrom).
        if self._anonym_einstellung(flow) is not None:
            log.warning("Anonymisierer: Websocket zu einem Modellanbieter geschlossen")
            flow.kill()
            return
        werte = self._maske(flow)
        if not werte or not flow.websocket or not flow.websocket.messages:
            return
        nachricht = flow.websocket.messages[-1]
        if not nachricht.from_client:
            nachricht.content = logik.zurueck_maskieren_bytes(nachricht.content, werte)


addons = [AieEgress()]
