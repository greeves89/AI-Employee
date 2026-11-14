"""Größengrenze für Uploads — VOR dem Einlesen des Körpers.

FastAPI liest ein Formular (``request.form()``), bevor Abhängigkeiten wie die
Anmeldung geprüft werden; Starlette legt die Datei dabei in eine temporäre Datei.
Eine Grenze im Endpunkt kommt also zu spät: auch ohne Anmeldung landeten
beliebig große Uploads erst einmal auf der Platte (Sicherheits-Review 28.10.2026).

Diese reine ASGI-Middleware weist zu große Uploads anhand von ``Content-Length``
ab, bevor ein Byte gelesen wird. Ohne Längenangabe (chunked) gibt es 411 —
Browser senden sie bei ``FormData`` immer mit. Caddy setzt zusätzlich
``request_body max_size`` (Caddyfile).
"""

import json
import re

#: (Methode, Pfad-Muster, Höchstgröße in Bytes)
REGELN: list[tuple[str, re.Pattern, int]] = []


#: Pfad-Muster → Query-Parameter, bei dem eine Anfrage OHNE Längenangabe durchgeht.
#: Für Anfragen, deren Körper der Endpunkt dann gar nicht liest — etwa die Adressprüfung
#: von Microsoft Graph (``?validationToken=…``, leerer Körper, Längenangabe nicht zugesichert).
OHNE_LAENGE: dict[str, str] = {}


def regel(methode: str, muster: str, max_bytes: int, ohne_laenge_bei: str | None = None) -> None:
    REGELN.append((methode.upper(), re.compile(muster), max_bytes))
    if ohne_laenge_bei:
        OHNE_LAENGE[muster] = ohne_laenge_bei


def _gezaehlt(receive, max_bytes: int):
    """``receive`` mit Zähler: über ``max_bytes`` sieht die Anwendung nur noch einen Abbruch."""
    gelesen = 0

    async def lesen():
        nonlocal gelesen
        nachricht = await receive()
        if nachricht.get("type") == "http.request":
            gelesen += len(nachricht.get("body") or b"")
            if gelesen > max_bytes:
                return {"type": "http.disconnect"}
        return nachricht

    return lesen


def _ohne_laenge_erlaubt(muster: re.Pattern, scope) -> bool:
    from urllib.parse import parse_qs

    param = OHNE_LAENGE.get(muster.pattern)
    if not param:
        return False
    werte = parse_qs((scope.get("query_string") or b"").decode("latin-1")).get(param) or []
    return any(w.strip() for w in werte)


async def _antwort(send, status: int, text: str) -> None:
    body = json.dumps({"detail": text}).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


class KoerperGrenze:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            methode, pfad = scope.get("method", ""), scope.get("path", "")
            for r_methode, muster, max_bytes in REGELN:
                if methode != r_methode or not muster.match(pfad):
                    continue
                laenge = dict(scope.get("headers") or []).get(b"content-length")
                if laenge is None and _ohne_laenge_erlaubt(muster, scope):
                    # Die Ausnahme hängt an der Query — die liest der Endpunkt womöglich anders
                    # (doppelte Parameter, leere Werte). Deshalb hier NICHT vertrauen, sondern
                    # mitzählen: Mehr als ``max_bytes`` Körper gibt es auch so nicht.
                    receive = _gezaehlt(receive, max_bytes)
                    break
                if laenge is None or not laenge.isdigit():
                    await _antwort(send, 411, "Für Uploads bitte die Länge (Content-Length) mitsenden.")
                    return
                if int(laenge) > max_bytes:
                    await _antwort(send, 413, f"Die Datei ist größer als {max_bytes // 1024 // 1024} MB.")
                    return
                break
        await self.app(scope, receive, send)
