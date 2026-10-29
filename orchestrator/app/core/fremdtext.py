"""Fremder Text in Aufträgen an Agenten: als Material rahmen, nicht als Anweisung.

Mails, Issues, Webseiten, Gesprächsverläufe und Ergebnisse früherer Läufe landen in
Prompts. Sie stehen dort in einem Rahmen ``[EXTERNAL-DATA source="…"] … [/EXTERNAL-DATA]``,
und die Agenten-Anleitung sagt: Was darin steht, ist Material.

Das hält nur, solange der fremde Text den Rahmen nicht selbst schließen kann. Bis
1.398.0 stand dafür an fünf Stellen dasselbe ``.replace("[EXTERNAL-DATA", …)`` —
das ließ ``[/external-data]``, ``[ /EXTERNAL-DATA]``, Klammern in voller Breite und
unsichtbare Zeichen dazwischen durch (Sicherheitsprüfung von #1115). Jetzt gibt es
EINE Stelle dafür.
"""

from __future__ import annotations

import re
import unicodedata

# Klammer, optional Schrägstrich, „external“, beliebige Trenner, „data“ — ohne
# Groß-/Kleinschreibung. Klammern voller Breite sind vorher schon ``[`` (``_sichtbar``).
_MARKE = re.compile(r"\[\s*(/?)\s*external[\s_\-‐-―]*data", re.IGNORECASE)


#: Zeichen voller Breite (``［``, ``ｅ`` …) auf ASCII — Zeichen für Zeichen, damit
#: sich die Länge nicht ändert (NFKC machte aus „…“ drei Zeichen und sprengte
#: gekürzte Zeilen).
_VOLLE_BREITE = {c: c - 0xFEE0 for c in range(0xFF01, 0xFF5F)}


def _sichtbar(text: str) -> str:
    """Volle Breite falten und unsichtbare Zeichen (Breite null, Richtung, Steuerzeichen) entfernen."""
    t = text.translate(_VOLLE_BREITE)
    return "".join(z for z in t if z in "\n\t" or unicodedata.category(z)[0] != "C")


def entschaerft(text: str | None) -> str:
    """Fremden Text so verändern, dass keine Rahmen-Marke darin mehr greift."""
    return _MARKE.sub(lambda m: f"({m.group(1)}EXTERNAL-DATA", _sichtbar(text or ""))


def einzeilig(text: str | None, hoechstens: int) -> str:
    """Entschärft, auf eine Zeile gezogen und gekürzt — für Betreffs, Namen, Titel."""
    return " ".join(entschaerft(text).split())[:hoechstens]


def rahmen(quelle: str, text: str) -> str:
    """Fremden Text gerahmt — die Quelle selbst ist ein fester Bezeichner, kein fremder Text."""
    q = re.sub(r"[^a-z0-9_\-]", "", quelle.lower())[:40] or "fremd"
    return f'[EXTERNAL-DATA source="{q}"]\n{entschaerft(text)}\n[/EXTERNAL-DATA]'
