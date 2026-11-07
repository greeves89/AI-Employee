"""„Fertig“ nur mit Beleg — eine Regel, eine Erkennung, überall gleich.

Ein Agent, der „erledigt“ meldet, nennt den Beleg: was er geprüft hat und mit welchem
Ergebnis (Test, Befehl und Ausgabe, Datei, Adresse, Bildschirmfoto). Konnte er nicht
prüfen, sagt er das ausdrücklich („Ungeprüft: …“) statt Erfolg zu behaupten.

Genutzt von:

* der Agenten-Anleitung (alle Laufzeiten, ``agent_manager._render_claude_md``),
* dem Ziel-Modus (``ziel``: „ZIEL ERREICHT“ ohne Beleg gilt nicht beim ersten Mal),
* Aufträgen und Board-Karten (``task_router``: ``task.metadata_["beleg"]``; das Board
  zeigt im Review „ohne Beleg“ bzw. „ungeprüft“).

Die Erkennung ist absichtlich einfach und für Menschen nachvollziehbar: eine Zeile, die
mit ``Beleg:`` oder ``Ungeprüft:`` beginnt (Markdown-Zeichen davor zählen nicht).
"""

from __future__ import annotations

import re

BELEG, UNGEPRUEFT, FEHLT = "beleg", "ungeprueft", "fehlt"

#: Für Agenten — in Anleitung, Kartenauftrag und Ziel-Regeln wortgleich.
REGEL = (
    "Meldest du etwas als erledigt (Auftrag, Karte, Ziel, Aufgabe), schreib am Ende eine "
    "eigene Zeile „Beleg: …“: was du geprüft hast und mit welchem Ergebnis (Test mit Ergebnis, "
    "Befehl und Ausgabe, Datei, Adresse, Bildschirmfoto). Konntest du nicht prüfen, "
    "schreib stattdessen „Ungeprüft: …“ mit dem Grund — nie Erfolg behaupten, den du nicht "
    "gesehen hast."
)

#: „Beleg:“ am Zeilenanfang (auch hinter Markdown-Zeichen) ODER nach einem Trenner im Satz
#: („… angelegt — Beleg: cat ergab 42“, live so gesehen). Nicht mitten im Satz („Den Beleg:
#: liefere ich später“): davor muss Zeilenanfang, Gedankenstrich, Satzzeichen oder eine schließende Klammer stehen.
_ZEILE = re.compile(
    r"(?:^|[—–;.!?)\]]|\s-)[\s>*_`#\-–—•]*(beleg|ungeprüft|ungeprueft)[*_`]*\s*:[*_`]*\s*\S",
    re.IGNORECASE | re.MULTILINE,
)


def stand(text: str | None) -> str:
    """``beleg`` | ``ungeprueft`` | ``fehlt`` — ein Beleg zählt vor „ungeprüft“."""
    treffer = [m.group(1).lower() for m in _ZEILE.finditer(text or "")]
    if "beleg" in treffer:
        return BELEG
    if treffer:
        return UNGEPRUEFT
    return FEHLT
