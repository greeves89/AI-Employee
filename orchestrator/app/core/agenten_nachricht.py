"""Rueckfragen und Uebergaben zwischen Agenten brauchen Kontext (#884).

Holt sich ein Agent bei einem Kollegen eine Entscheidung, hat der Fragende die
Details und der Antwortende das grosse Bild — beide handeln vernuenftig, und
die Entscheidung ist trotzdem schlecht, weil die Informationen nie
zusammenkamen. Deshalb muss eine Frage oder Uebergabe sagen, WARUM sie gestellt
wird und WAS davon abhaengt; der Server haengt an, an welchem Auftrag der
Absender gerade arbeitet.

Geprueft wird auf dem Server, nicht im Werkzeug: so gilt es fuer alle
Laufzeiten, auch fuer Agenten, deren Container noch ein aelteres Werkzeug
kennt. Die Angaben duerfen deshalb als Felder ODER als Zeilen im Text kommen.
"""

import re

#: Nur diese beiden verlangen Kontext. Antworten, Statusmeldungen und
#: einfache Nachrichten bleiben frei — sonst wird aus der Regel Buerokratie.
PFLICHT_TYPEN = ("question", "handoff")
MIN_LAENGE = 8

_ZEILE = {
    "anlass": re.compile(r"^\s*(?:anlass|warum|grund)\s*:\s*(.+)$", re.I | re.M),
    "auswirkung": re.compile(r"^\s*(?:auswirkung|betrifft|folge)\s*:\s*(.+)$", re.I | re.M),
}


class KontextFehlt(ValueError):
    """Die Nachricht nennt Anlass oder Auswirkung nicht."""

    def __init__(self, fehlend: list[str]):
        self.fehlend = fehlend
        super().__init__(
            "Eine Rückfrage oder Übergabe braucht Kontext, damit der Kollege richtig "
            f"entscheiden kann. Es fehlt: {', '.join(fehlend)}. Gib 'anlass' (warum du fragst) "
            "und 'auswirkung' (was davon abhängt) an — als Felder oder als Zeilen "
            "„Anlass: …\" und „Auswirkung: …\" im Text."
        )


def kontext(message_type: str | None, text: str, anlass: str | None, auswirkung: str | None) -> dict:
    """Anlass und Auswirkung einer Nachricht — aus den Feldern oder aus dem Text.

    Wirft ``KontextFehlt``, wenn der Typ Kontext verlangt und er fehlt. Fuer
    alle anderen Typen kommt zurueck, was da ist (auch nichts).
    """
    werte = {"anlass": (anlass or "").strip(), "auswirkung": (auswirkung or "").strip()}
    for name, muster in _ZEILE.items():
        if not werte[name]:
            treffer = muster.search(text or "")
            if treffer:
                werte[name] = treffer.group(1).strip()
    if (message_type or "message") in PFLICHT_TYPEN:
        fehlend = [name for name, wert in werte.items() if len(wert) < MIN_LAENGE]
        if fehlend:
            raise KontextFehlt(fehlend)
    return werte


def mit_kontext(text: str, werte: dict, auftrag: str = "") -> str:
    """Die Nachricht, wie der Empfaenger sie liest: Text, dann Kontext, dann der
    Auftrag des Absenders. Was schon als Zeile im Text steht, wird nicht doppelt
    angehaengt."""
    zusatz = []
    for name, etikett in (("anlass", "Anlass"), ("auswirkung", "Auswirkung")):
        wert = werte.get(name) or ""
        if wert and not _ZEILE[name].search(text or ""):
            zusatz.append(f"{etikett}: {wert}")
    teile = [(text or "").rstrip()]
    if zusatz:
        teile.append("\n".join(zusatz))
    if auftrag:
        teile.append("Der Absender arbeitet an:\n" + auftrag.strip())
    return "\n\n".join(t for t in teile if t)
