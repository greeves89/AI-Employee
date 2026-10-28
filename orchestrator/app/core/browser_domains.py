"""Freigabeliste fuer den Agenten-Browser bereinigen (#828, Schritt 5).

Eigenes Modul und absichtlich ohne FastAPI/SQLAlchemy: Der Wert kommt vom
Nutzer, geht als Umgebungsvariable ``BROWSER_ALLOWED_DOMAINS`` in einen
Container und wird dort an Kommas getrennt. Damit ist er ein Eingabewert,
der eine Prozessgrenze ueberschreitet -- und soll deshalb auch ohne das
schwere Umfeld der App pruefbar sein.

Bereinigt wird, NICHT nur vertraut:

* Nur Hostnamen. Ein Eintrag mit Schema, Pfad, Benutzerangabe (``@``) oder
  Platzhalter waere eine Zusage, die der Abgleich im Agenten nicht einhaelt:
  dort wird ausschliesslich der Hostname verglichen. ``https://example.com/x``
  wuerde nie passen und der Besitzer saehe eine Liste, die nichts freigibt.
* Kein Komma, kein Zeilenumbruch, kein Leerzeichen. Das Komma ist das
  Trennzeichen auf der Agentenseite, der Rest hat in einem Hostnamen nichts
  zu suchen und macht aus einem Eintrag still zwei (oder einen kaputten).
* Obergrenzen fuer Anzahl und Laenge, damit die Variable nicht unbegrenzt
  waechst.
"""

from __future__ import annotations

#: Mehr Eintraege sind kein Anwendungsfall, sondern ein Versehen.
MAX_EINTRAEGE = 100
#: Laenge eines Hostnamens nach RFC 1035.
MAX_LAENGE = 253

_VERBOTENE_ZEICHEN = set(' \t\r\n,/\\@?#"\'`;*')


class UngueltigeDomain(ValueError):
    """Ein Eintrag ist kein Hostname -- mit Begruendung fuer den Aufrufer."""


def bereinigen(eintraege: list[str] | None) -> list[str]:
    """Freigabeliste normalisieren. Leer/``None`` heisst unbeschraenkt.

    Wirft ``UngueltigeDomain`` statt einen unbrauchbaren Eintrag still zu
    schlucken: Eine Freigabeliste, die anders wirkt als eingetippt, ist bei
    einer Sicherheitseinstellung schlimmer als eine Fehlermeldung.
    """
    if not eintraege:
        return []

    sauber: list[str] = []
    for roh in eintraege:
        if roh is None:
            continue
        wert = str(roh).strip().lower().lstrip(".")
        if not wert:
            continue
        if any(z in _VERBOTENE_ZEICHEN for z in wert):
            raise UngueltigeDomain(
                f"'{roh}' ist kein Hostname. Erwartet wird nur der Host, "
                f"z. B. 'example.com' — ohne Schema, Pfad oder Platzhalter.")
        if len(wert) > MAX_LAENGE:
            raise UngueltigeDomain(f"Eintrag ist laenger als {MAX_LAENGE} Zeichen.")
        if "." not in wert and wert != "localhost":
            raise UngueltigeDomain(
                f"'{roh}' sieht nicht wie ein Hostname aus (kein Punkt darin).")
        if wert not in sauber:
            sauber.append(wert)

    if len(sauber) > MAX_EINTRAEGE:
        raise UngueltigeDomain(f"Hoechstens {MAX_EINTRAEGE} Eintraege.")
    return sauber
