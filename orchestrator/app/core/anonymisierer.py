"""Anonymisierer vor dem Modell — Einstellung je Agent (Entscheidung 09.11.2026: opt-in je Agent).

Die Arbeit macht der Egress-Proxy (``egress/anonym.py``): Er ersetzt Personendaten in jeder
Anfrage an den Modellanbieter durch Platzhalter und setzt sie in der Antwort wieder ein.
Dieses Modul hält die Einstellung am Agenten (``agent.config["anonymisierer"]``) und sagt dem
Proxy über ``/egress/verbindung``, was er für diesen Agenten tun soll.

Wirkt nur, wenn der Egress-Proxy an ist (sonst geht die Anfrage nicht über ihn) und der
Agent Claude Code (Anthropic direkt) oder Codex nutzt. Eigene Modelle (``custom_llm``),
Bedrock, Vertex oder eigene Adressen deckt er nicht ab — dort wird das Einschalten
abgelehnt; der Sicherheitsstatus zeigt Agenten, bei denen er eingeschaltet ist, aber nicht wirkt.
"""

from __future__ import annotations

import re

ZWECK = "anonymisierer"
SCHLUESSEL = "anonymisierer"            # in agent.config
#: Anbieter, deren Anfragen der Proxy anonymisiert (TLS wird dafür aufgebrochen).
ZIELE = ["api.anthropic.com", "chatgpt.com", "api.openai.com"]
MODI = ("claude_code", "codex_cli")
MAX_BEGRIFFE = 500
_SICHER = re.compile(r"[^\"\\\x00-\x1f\x7f]{3,100}")


class UngueltigeBegriffe(ValueError):
    pass


def bereinigen(begriffe: list[str] | None) -> list[str]:
    """Namen und Begriffe serverseitig prüfen: 3–100 Zeichen, keine Anführungszeichen,
    Backslashes oder Steuerzeichen (sonst wäre das Einsetzen in JSON nicht sicher)."""
    sauber: list[str] = []
    for b in begriffe or []:
        b = " ".join(str(b).split())
        if not b:
            continue
        if not _SICHER.fullmatch(b):
            raise UngueltigeBegriffe(
                f"„{b[:40]}“: 3 bis 100 Zeichen, ohne Anführungszeichen und Backslash.")
        if b.casefold() not in {s.casefold() for s in sauber}:
            sauber.append(b)
    if len(sauber) > MAX_BEGRIFFE:
        raise UngueltigeBegriffe(f"Höchstens {MAX_BEGRIFFE} Begriffe.")
    return sauber


def einstellung(config: dict | None) -> dict:
    roh = (config or {}).get(SCHLUESSEL) or {}
    return {"aktiv": bool(roh.get("aktiv")), "begriffe": list(roh.get("begriffe") or [])}


def unterstuetzt(mode: str | None) -> bool:
    return (mode or "claude_code") in MODI


def fuer_proxy(agent) -> dict | None:
    """Was der Proxy für diesen Agenten braucht — ``None``, wenn der Anonymisierer aus ist."""
    e = einstellung(agent.config)
    if not e["aktiv"] or not unterstuetzt(agent.mode):
        return None
    from app.core.encryption import agent_schluessel

    return {"schluessel": agent_schluessel(ZWECK, agent.id), "begriffe": e["begriffe"], "ziele": list(ZIELE)}


#: Für Agenten mit Anonymisierer: Claude Code schickt keine Nutzungsdaten und Fehlerberichte
#: an weitere Ziele (Statsig, Sentry) — die deckt der Proxy nicht ab (Sicherheitsreview).
UMGEBUNG = {
    "DISABLE_TELEMETRY": "1",
    "DISABLE_ERROR_REPORTING": "1",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
}


def umgebung(agent) -> dict[str, str]:
    """Zusätzliche Umgebung beim Erstellen des Containers — leer, wenn aus."""
    e = einstellung(getattr(agent, "config", None))
    return dict(UMGEBUNG) if e["aktiv"] and unterstuetzt(getattr(agent, "mode", None)) else {}


def modell_hinweis() -> str:
    """Für die Agenten-Anleitung, wenn der Anonymisierer an ist (alle Laufzeiten gleich)."""
    return (
        "## Anonymisierte Personendaten\n"
        "- Für diesen Agenten ersetzt die Plattform Personendaten (Namen aus einer Liste, "
        "E-Mail-Adressen, Telefonnummern, IBAN, Kartennummern) durch Platzhalter wie "
        "`[EMAIL-3f9a1c2b]` oder `[NAME-0a1b2c3d]`, bevor der Text das Modell erreicht, und "
        "setzt sie in deiner Antwort und in Werkzeugaufrufen wieder ein.\n"
        "- Verwende Platzhalter unverändert und vollständig (mit Klammern), auch in "
        "Werkzeugaufrufen. Rate nie, welcher Wert dahinter steht, und erfinde keine eigenen.\n\n"
    )
