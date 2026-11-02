"""Docker-Wächter: der einzige Weg des Orchestrators zu Docker (P1-13).

Ein HTTP/1.1-Durchreicher vor dem Socket-Proxy. Jede Anfrage wird einzeln
gelesen: ``POST /containers/create``, ``/containers/{id}/exec`` und
``/volumes/create`` prüft ``regeln``; alles andere geht unverändert durch —
auch Datenströme (Logs, Events) und Verbindungen, die Docker für ``exec``/
``attach`` auf rohe Bytes umschaltet (101 bzw. Antwort ohne Länge).

Dazu ``POST /aie/compose`` (mit Dienst-Schlüssel): Agenten-Apps. Den Runner mit
dem Docker-Socket legt NUR der Wächter an, nach eigener Prüfung des Modells
(``compose_pruefung.pruefen``). Der Orchestrator selbst bekommt keinen
Container mit Socket mehr durch.

Entschieden wird auf dem dekodierten, bereinigten Pfad — so routet auch Docker
(``//containers/create`` oder ``%2Fcreate`` helfen nicht).
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import posixpath
import re
from urllib.parse import unquote

from app.docker_waechter import regeln

logger = logging.getLogger("docker_waechter")

_KOPF_MAX = 64 * 1024
_KOERPER_MAX = 4 * 1024 * 1024
_VERSION = r"(?:/v[0-9.]+)?"
_GEPRUEFT = (
    (re.compile(rf"^{_VERSION}/containers/create$"), "container"),
    (re.compile(rf"^{_VERSION}/containers/[^/]+/exec$"), "exec"),
    (re.compile(rf"^{_VERSION}/volumes/create$"), "volume"),
    (re.compile(rf"^{_VERSION}/containers/[^/]+/start$"), "start"),
)
_FELDNAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
#: Anfragen IN einen bestehenden Container: das Ziel wird bei Docker nachgeschlagen.
_ZIEL = re.compile(rf"^(?P<v>{_VERSION})/containers/(?P<id>[^/]+)/"
                   r"(?P<rest>exec|archive|attach|attach/ws|update|export|copy|rename)$")
#: Endpunkte, bei denen Docker die Verbindung auf rohe Bytes umschaltet.
_ROHSTROM = re.compile(rf"^{_VERSION}/(containers/[^/]+/attach(/ws)?|exec/[^/]+/start)$")
COMPOSE_PFAD = "/aie/compose"


class Abgelehnt(Exception):
    def __init__(self, status: int, grund: str):
        super().__init__(grund)
        self.status, self.grund = status, grund


def pfad_bereinigt(ziel: str) -> str:
    """Pfad, wie Docker ihn sieht: dekodiert, doppelte Schrägstriche und ``..`` aufgelöst.

    Nicht ``urlsplit``: das liest ``//containers/create`` als Rechnername
    ``containers`` und Pfad ``/create`` — und die Prüfung liefe ins Leere.
    """
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", ziel):  # absolute Form: Schema und Rechner weg
        ziel = "/" + ziel.split("://", 1)[1].partition("/")[2]
    pfad = unquote(ziel.split("?", 1)[0].split("#", 1)[0] or "/")
    return posixpath.normpath("/" + pfad.lstrip("/"))


def art_der_anfrage(methode: str, ziel: str) -> str | None:
    """Welche Prüfung braucht die Anfrage? ``None`` = durchreichen."""
    if methode.upper() != "POST":
        return None
    pfad = pfad_bereinigt(ziel)
    for muster, art in _GEPRUEFT:
        if muster.match(pfad):
            return art
    return None


async def _kopf_lesen(reader: asyncio.StreamReader) -> bytes | None:
    try:
        kopf = await reader.readuntil(b"\r\n\r\n")
    except asyncio.IncompleteReadError as e:
        if e.partial.strip():
            raise Abgelehnt(400, "unvollständige Anfrage") from None
        return None
    except asyncio.LimitOverrunError:
        raise Abgelehnt(431, "Kopf zu groß") from None
    if len(kopf) > _KOPF_MAX:
        raise Abgelehnt(431, "Kopf zu groß")
    return kopf


def _kopf_zerlegen(kopf: bytes) -> tuple[str, list[tuple[str, str]]]:
    zeilen = kopf.decode("latin-1").split("\r\n")
    felder = []
    for zeile in zeilen[1:]:
        if not zeile:
            continue
        name, doppelpunkt, wert = zeile.partition(":")
        if not doppelpunkt or not _FELDNAME.match(name) or "\n" in zeile or "\r" in zeile:
            # Gefaltete Zeilen, nacktes LF, Leerzeichen im Namen: Wächter und Docker
            # läsen dieselbe Anfrage verschieden.
            raise Abgelehnt(400, "Kopfzeile nicht eindeutig lesbar")
        felder.append((name, wert.strip()))
    if "\n" in zeilen[0]:
        raise Abgelehnt(400, "Anfragezeile nicht eindeutig lesbar")
    return zeilen[0], felder


def _eindeutig(felder: list[tuple[str, str]]) -> None:
    """Genau eine Längenangabe — sonst bestimmen Wächter und Docker das Ende verschieden."""
    laengen = [w for n, w in felder if n.lower() == "content-length"]
    te = [w.lower() for n, w in felder if n.lower() == "transfer-encoding"]
    if len(laengen) > 1 or (laengen and te) or len(te) > 1 or (te and te[0] != "chunked"):
        raise Abgelehnt(400, "Längenangabe nicht eindeutig")
    if laengen and not laengen[0].isdigit():
        raise Abgelehnt(400, "Längenangabe ungültig")


def _feld(felder: list[tuple[str, str]], name: str) -> str | None:
    name = name.lower()
    for n, w in felder:
        if n.lower() == name:
            return w
    return None


def _kopf_bauen(erste_zeile: str, felder: list[tuple[str, str]]) -> bytes:
    return (erste_zeile + "\r\n" + "".join(f"{n}: {w}\r\n" for n, w in felder) + "\r\n").encode("latin-1")


_CHUNK_ZEILE = re.compile(rb"^[0-9A-Fa-f]{1,8}(;[^\r\n]*)?\r\n$")
#: Ungeprüfte Anfragen mit chunked-Körper werden ebenfalls eingelesen und mit fester
#: Länge weitergegeben (Datei-Uploads): Docker sieht nie die Rahmung des Aufrufers.
_DURCHREICH_MAX = 512 * 1024 * 1024


async def _chunked_lesen(reader: asyncio.StreamReader, grenze: int | None) -> bytes:
    daten = bytearray()
    while True:
        zeile = await reader.readuntil(b"\r\n")
        if not _CHUNK_ZEILE.match(zeile) or b"\n" in zeile[:-1]:
            raise Abgelehnt(400, "Chunk-Rahmung nicht eindeutig")
        groesse = int(zeile.split(b";")[0], 16)
        if groesse == 0:
            while (await reader.readuntil(b"\r\n")) != b"\r\n":
                pass
            return bytes(daten)
        daten += await reader.readexactly(groesse)
        if await reader.readexactly(2) != b"\r\n":
            raise Abgelehnt(400, "Chunk-Rahmung nicht eindeutig")
        if grenze is not None and len(daten) > grenze:
            raise Abgelehnt(413, "Anfrage zu groß")


async def _chunked_kopieren(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    while True:
        zeile = await reader.readuntil(b"\r\n")
        writer.write(zeile)
        groesse = int(zeile.split(b";")[0].strip() or b"0", 16)
        if groesse == 0:
            while True:
                rest = await reader.readuntil(b"\r\n")
                writer.write(rest)
                if rest == b"\r\n":
                    await writer.drain()
                    return
        writer.write(await reader.readexactly(groesse + 2))
        await writer.drain()


async def _laenge_kopieren(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, laenge: int) -> None:
    while laenge > 0:
        stueck = await reader.read(min(laenge, 65536))
        if not stueck:
            raise ConnectionError("Verbindung vor Ende des Inhalts geschlossen")
        writer.write(stueck)
        laenge -= len(stueck)
        await writer.drain()


async def _pumpen(von: asyncio.StreamReader, nach: asyncio.StreamWriter) -> None:
    try:
        while True:
            stueck = await von.read(65536)
            if not stueck:
                break
            nach.write(stueck)
            await nach.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        try:
            if nach.can_write_eof():
                nach.write_eof()
        except (OSError, RuntimeError):
            pass


async def _durchreichen(c_reader, c_writer, u_reader, u_writer) -> None:
    """Rohe Bytes in beide Richtungen, bis eine Seite schließt (exec/attach)."""
    await asyncio.gather(_pumpen(c_reader, u_writer), _pumpen(u_reader, c_writer))


def _antwort(status: int, inhalt: dict) -> bytes:
    text = json.dumps(inhalt).encode()
    gruende = {200: "OK", 400: "Bad Request", 401: "Unauthorized", 403: "Forbidden", 411: "Length Required",
               413: "Payload Too Large", 422: "Unprocessable Entity", 431: "Request Header Fields Too Large",
               502: "Bad Gateway"}
    return (f"HTTP/1.1 {status} {gruende.get(status, 'Error')}\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(text)}\r\nConnection: close\r\n\r\n").encode() + text


class Waechter:
    """Ein Wächter vor ``upstream`` (host, port) mit den erlaubten Host-Pfaden."""

    def __init__(self, upstream: tuple[str, int], erlaubte_host_pfade: list[str], *,
                 dienst_schluessel: str = "", compose=None):
        self.upstream = upstream
        self.erlaubte_host_pfade = erlaubte_host_pfade
        self.dienst_schluessel = dienst_schluessel
        #: ``compose(koerper: dict) -> (status, antwort: dict)`` — Agenten-Apps, siehe compose.py.
        self.compose = compose

    def _pruefen(self, art: str, koerper: bytes) -> tuple[str | None, bytes]:
        """(Grund, Körper zum Weiterreichen). Weitergereicht wird das geprüfte JSON in
        fester Form — nicht die Original-Bytes, die ein anderer Leser anders läse."""
        if art == "start" and not (koerper or b"").strip():
            return None, b""
        try:
            daten = json.loads(koerper or b"{}", object_pairs_hook=regeln.ohne_dubletten)
        except ValueError as e:
            return f"Anfrage ist kein eindeutiges JSON ({e})", b""
        if art == "container":
            grund = regeln.container_anlegen(daten, self.erlaubte_host_pfade)
        elif art == "exec":
            grund = regeln.exec_anlegen(daten)
        elif art == "start":
            grund = regeln.container_starten(daten)
        else:
            grund = regeln.volume_anlegen(daten)
        return grund, json.dumps(daten).encode()

    async def _ziel_pruefen(self, container: str) -> tuple[str | None, str]:
        """Den Zielcontainer bei Docker nachschlagen und gegen ``regeln.zielcontainer`` halten.
        (Grund, volle ID) — weitergereicht wird an die ID, nicht an den Namen: sonst
        liesse sich zwischen Prüfung und Ausführung ein anderer Container so umbenennen."""
        from urllib.parse import quote

        try:
            reader, writer = await asyncio.open_connection(*self.upstream)
        except OSError as e:
            return f"Docker nicht erreichbar: {e}", ""
        try:
            writer.write(f"GET /containers/{quote(container, safe='')}/json HTTP/1.1\r\nHost: docker\r\n"
                         "Connection: close\r\n\r\n".encode())
            await writer.drain()
            kopf = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
            erste, felder = _kopf_zerlegen(kopf)
            if (_feld(felder, "Transfer-Encoding") or "").lower() == "chunked":
                inhalt = await asyncio.wait_for(_chunked_lesen(reader, _KOERPER_MAX), 10)
            else:
                inhalt = await asyncio.wait_for(reader.read(_KOERPER_MAX), 10)
            if " 200 " not in erste + " ":
                return "Zielcontainer unbekannt", ""
            daten = json.loads(inhalt)
            kennung = str(daten.get("Id") or "")
            if not re.fullmatch(r"[0-9a-f]{64}", kennung):
                return "Zielcontainer ohne gültige ID", ""
            return regeln.zielcontainer(daten), kennung
        except (asyncio.TimeoutError, ValueError, ConnectionError, Abgelehnt, asyncio.IncompleteReadError) as e:
            return f"Zielcontainer nicht prüfbar ({e})", ""
        finally:
            writer.close()

    async def _koerper_lesen(self, reader, felder) -> bytes:
        if (_feld(felder, "Transfer-Encoding") or "").lower() == "chunked":
            return await _chunked_lesen(reader, _KOERPER_MAX)
        laenge = int(_feld(felder, "Content-Length") or 0)
        if laenge > _KOERPER_MAX:
            raise Abgelehnt(413, "Anfrage zu groß")
        return await reader.readexactly(laenge) if laenge else b""

    async def _compose(self, felder, koerper: bytes) -> bytes:
        schluessel = _feld(felder, "X-Waechter-Token") or ""
        if not self.dienst_schluessel or not hmac.compare_digest(schluessel.encode(), self.dienst_schluessel.encode()):
            return _antwort(401, {"message": "Dienst-Schlüssel fehlt oder falsch"})
        if self.compose is None:
            return _antwort(400, {"message": "Compose ist hier nicht eingerichtet"})
        try:
            daten = json.loads(koerper or b"{}")
        except ValueError:
            return _antwort(400, {"message": "Anfrage ist kein JSON"})
        status, inhalt = await asyncio.to_thread(self.compose, daten)
        return _antwort(status, inhalt)

    async def verbindung(self, c_reader: asyncio.StreamReader, c_writer: asyncio.StreamWriter) -> None:
        u_reader = u_writer = None
        try:
            while True:
                try:
                    kopf = await _kopf_lesen(c_reader)
                except Abgelehnt as e:
                    c_writer.write(_antwort(e.status, {"message": e.grund}))
                    await c_writer.drain()
                    return
                if kopf is None:
                    return
                try:
                    erste, felder = _kopf_zerlegen(kopf)
                    _eindeutig(felder)
                except Abgelehnt as e:
                    c_writer.write(_antwort(e.status, {"message": e.grund}))
                    await c_writer.drain()
                    return
                teile = erste.split(" ")
                if len(teile) != 3 or teile[2] != "HTTP/1.1":
                    c_writer.write(_antwort(400, {"message": "nur HTTP/1.1"}))
                    return
                methode, ziel, _ = teile

                if pfad_bereinigt(ziel) == COMPOSE_PFAD and methode.upper() == "POST":
                    koerper = await self._koerper_lesen(c_reader, felder)
                    c_writer.write(await self._compose(felder, koerper))
                    await c_writer.drain()
                    return

                art = art_der_anfrage(methode, ziel)
                pfad = pfad_bereinigt(ziel)
                treffer = _ZIEL.match(pfad)
                if treffer:
                    grund, kennung = await self._ziel_pruefen(treffer.group("id"))
                    if grund:
                        logger.warning("Abgelehnt: %s %s — %s", methode, pfad[:120], grund)
                        c_writer.write(_antwort(403, {"message": f"Docker-Wächter: {grund}"}))
                        await c_writer.drain()
                        return
                    # An genau den geprüften Container: feste ID statt Name.
                    abfrage = ziel.split("?", 1)[1] if "?" in ziel else ""
                    ziel = f"{treffer.group('v')}/containers/{kennung}/{treffer.group('rest')}" + (
                        f"?{abfrage}" if abfrage else "")
                    erste = f"{methode} {ziel} HTTP/1.1"
                    pfad = pfad_bereinigt(ziel)
                koerper = None
                if art:
                    try:
                        koerper = await self._koerper_lesen(c_reader, felder)
                    except Abgelehnt as e:
                        c_writer.write(_antwort(e.status, {"message": e.grund}))
                        return
                    grund, koerper = self._pruefen(art, koerper)
                    if grund:
                        logger.warning("Abgelehnt: %s %s — %s", methode, pfad_bereinigt(ziel)[:120], grund)
                        c_writer.write(_antwort(403, {"message": f"Docker-Wächter: {grund}"}))
                        await c_writer.drain()
                        return
                    # Mit fester Länge weiter, damit genau das ankommt, was geprüft wurde.
                    felder = [(n, w) for n, w in felder if n.lower() not in ("content-length", "transfer-encoding")]
                    felder.append(("Content-Length", str(len(koerper))))

                if u_writer is None:
                    try:
                        u_reader, u_writer = await asyncio.open_connection(*self.upstream)
                    except OSError as e:
                        c_writer.write(_antwort(502, {"message": f"Docker nicht erreichbar: {e}"}))
                        return
                if koerper is None and (_feld(felder, "Transfer-Encoding") or "").lower() == "chunked":
                    try:
                        koerper = await _chunked_lesen(c_reader, _DURCHREICH_MAX)
                    except Abgelehnt as e:
                        c_writer.write(_antwort(e.status, {"message": e.grund}))
                        return
                    felder = [(n, w) for n, w in felder if n.lower() != "transfer-encoding"]
                    felder.append(("Content-Length", str(len(koerper))))
                u_writer.write(_kopf_bauen(erste, felder))
                if koerper is not None:
                    u_writer.write(koerper)
                else:
                    await _laenge_kopieren(c_reader, u_writer, int(_feld(felder, "Content-Length") or 0))
                await u_writer.drain()

                # Antwort — Zwischenantworten (1xx ausser 101) überspringen bis zur endgültigen.
                rohstrom = bool(_ROHSTROM.match(pfad))
                while True:
                    antwort_kopf = await _kopf_lesen(u_reader)
                    if antwort_kopf is None:
                        return
                    status_zeile, a_felder = _kopf_zerlegen(antwort_kopf)
                    c_writer.write(antwort_kopf)
                    await c_writer.drain()
                    status = int(status_zeile.split(" ")[1])
                    if not (100 <= status < 200) or status == 101:
                        break
                if status == 101:
                    if not rohstrom:
                        return  # Umschaltung nur für attach/exec — sonst Verbindung schliessen
                    await _durchreichen(c_reader, c_writer, u_reader, u_writer)
                    return
                if methode.upper() == "HEAD" or status in (204, 304):
                    continue
                if (_feld(a_felder, "Transfer-Encoding") or "").lower() == "chunked":
                    await _chunked_kopieren(u_reader, c_writer)
                elif _feld(a_felder, "Content-Length") is not None:
                    await _laenge_kopieren(u_reader, c_writer, int(_feld(a_felder, "Content-Length")))
                elif rohstrom:
                    # attach/exec ohne Upgrade: Docker schaltet trotzdem auf rohen Strom.
                    await _durchreichen(c_reader, c_writer, u_reader, u_writer)
                    return
                else:
                    # Ohne Länge und ohne Umschaltung: bis zum Ende lesen, dann schliessen.
                    await _pumpen(u_reader, c_writer)
                    return
                if (_feld(a_felder, "Connection") or "").lower() == "close":
                    return
        except (ConnectionError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ValueError) as e:
            logger.debug("Verbindung beendet: %s", e)
        finally:
            for w in (c_writer, u_writer):
                if w is not None:
                    try:
                        w.close()
                    except Exception:  # noqa: BLE001
                        pass

    async def starten(self, host: str, port: int) -> asyncio.base_events.Server:
        return await asyncio.start_server(self.verbindung, host, port, limit=_KOPF_MAX)
