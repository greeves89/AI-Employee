"""Persoenliche Oberflaechen-Einstellungen am Konto (``users.ui_preferences``).

Ein JSON-Objekt je Person, aufgeteilt in Schluessel je Bereich der Oberflaeche
(vorerst nur ``agents_page``). Ein Schreibvorgang ersetzt immer genau die
uebergebenen Schluessel und laesst alle anderen stehen — so koennen spaetere
Bereiche ihre Einstellungen ablegen, ohne sich gegenseitig zu ueberschreiben.

Der Inhalt eines Schluessels gehoert der Oberflaeche; der Server prueft nur, was
er schuetzen muss: bekannte Schluessel, ein Objekt als Wert und eine Obergrenze
fuer die Gesamtgroesse (die Spalte ist kein Ablageplatz).
"""

from __future__ import annotations

import json

#: Bereiche, die Einstellungen ablegen duerfen.
ERLAUBTE_SCHLUESSEL = frozenset({"agents_page"})

#: Obergrenze fuer das gesamte gespeicherte Objekt, serialisiert in Bytes.
MAX_BYTES = 32 * 1024


class UngueltigeEinstellungen(ValueError):
    """Die Aenderung verletzt eine Regel — der Endpunkt macht daraus eine 422."""


def groesse(einstellungen: dict) -> int:
    """Groesse in Bytes, so kompakt serialisiert, wie sie gespeichert wird."""
    return len(json.dumps(einstellungen, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def zusammenfuehren(gespeichert: dict | None, aenderung: dict) -> dict:
    """Neues Gesamtobjekt: die Schluessel aus ``aenderung`` ersetzen, der Rest bleibt.

    Wirft ``UngueltigeEinstellungen`` bei leerer Aenderung, unbekanntem
    Schluessel, einem Wert, der kein Objekt ist, oder wenn das Ergebnis die
    Groessengrenze uebersteigt. ``gespeichert`` wird nicht veraendert.
    """
    if not aenderung:
        raise UngueltigeEinstellungen("Mindestens ein Schlüssel erwartet.")
    unbekannt = sorted(set(aenderung) - ERLAUBTE_SCHLUESSEL)
    if unbekannt:
        raise UngueltigeEinstellungen(f"Unbekannter Schlüssel: {', '.join(unbekannt)}")
    for schluessel, wert in aenderung.items():
        if not isinstance(wert, dict):
            raise UngueltigeEinstellungen(f"„{schluessel}“ muss ein Objekt sein.")
    neu = {**(gespeichert or {}), **aenderung}
    if groesse(neu) > MAX_BYTES:
        raise UngueltigeEinstellungen(
            f"Einstellungen zu groß (höchstens {MAX_BYTES // 1024} KB)."
        )
    return neu
