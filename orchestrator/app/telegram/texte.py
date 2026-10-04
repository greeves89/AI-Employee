"""Texte des Telegram-Bots — an EINER Stelle, deutsch und ohne Emojis (#902).

Sammel-Bot (``handlers/commands.py``) und Agenten-Bot (``agent_bot.py``) melden
Zustände, Bewertungen und die Kennzahlen eines Laufs gleich. Vorher stand dort je
eine eigene Liste farbiger Kreise und eine Fußzeile aus Bildzeichen mit
„3.2s | 2 turns“.
"""

from __future__ import annotations

import re

from app.core.kosten import betrag_anzeigen

_ZUSTAENDE = {
    "created": "startet",
    "running": "läuft",
    "idle": "bereit",
    "working": "arbeitet",
    "stopped": "gestoppt",
    "error": "Fehler",
}


def zustand_wort(zustand: str | None) -> str:
    """Zustand eines Agenten als Wort statt als farbiger Kreis."""
    return _ZUSTAENDE.get(zustand or "", "unbekannt")


#: Dieselben Wörter wie im Browser (``frontend/src/lib/aufgaben-anzeige.ts``).
_AUFGABEN_STATUS = {
    "pending": "Wartet",
    "queued": "In der Warteschlange",
    "running": "Läuft",
    "completed": "Erledigt",
    "failed": "Fehlgeschlagen",
    "cancelled": "Abgebrochen",
}


def aufgaben_status(status: str | None) -> str:
    """Status einer Aufgabe als Wort; Unbekanntes bleibt, wie es kommt."""
    return _AUFGABEN_STATUS.get(status or "", status or "unbekannt")


def bewertung(sterne: int) -> str:
    """Bewertung als „4 von 5“ statt einer Reihe Sternchen."""
    return f"{int(sterne)} von 5"


def _zahl(wert: float, stellen: int = 1) -> str:
    return f"{wert:.{stellen}f}".replace(".", ",")


def kennzahlen(dauer_ms: float | None, kosten_usd: float | None, runden: int | None) -> str:
    """„Dauer 3,2 s · Kosten 0,01 $ · 2 Runden“ — leer, wenn keine Dauer bekannt ist.

    Der Betrag kommt in der Anzeigewährung der Anlage (``core.kosten.betrag_anzeigen``),
    wie im Browser."""
    if not dauer_ms:
        return ""
    teile = [f"Dauer {_zahl(float(dauer_ms) / 1000)} s"]
    if kosten_usd:
        teile.append(f"Kosten {betrag_anzeigen(float(kosten_usd))}")
    if runden:
        teile.append(f"{int(runden)} {'Runde' if int(runden) == 1 else 'Runden'}")
    return " · ".join(teile)


_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B50\u2B06\u2B07\u23E9-\u23FA"
    "\u231A\u231B\u2139\u2705\u274C\u2753\u203C\u2049\uFE0F\u200D]"
)


def ohne_emojis(text: str) -> str:
    """Emojis aus Systemmeldungen entfernen, die über Telegram hinausgehen.

    Sicherheitsnetz für Meldungen, die andere Teile des Servers für Telegram
    schreiben (Zeitpläne, Selbsttest, Bereitschaft …). Text, Umlaute, Anführungs-
    und Währungszeichen bleiben stehen; übrig gebliebene Doppel-Leerzeichen und
    Leerzeichen am Zeilenanfang fallen weg."""
    if not text:
        return text
    sauber = _EMOJI.sub("", text)
    zeilen = [re.sub(r" {2,}", " ", z).strip(" ") for z in sauber.split("\n")]
    return "\n".join(zeilen)
