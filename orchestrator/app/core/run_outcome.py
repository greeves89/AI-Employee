"""Ob ein Lauf wirklich gearbeitet hat — oder nur so aussieht.

Befund #680: Vom 27.08. bis 30.08.2026 hat 76 Stunden lang KEIN Zeitplan-Lauf
eines Agenten Arbeit geleistet. Trotzdem standen alle 94 Laeufe in der
Oberflaeche auf ``completed``, ``error`` war leer. Erst im Ergebnisfeld stand,
was wirklich passiert war:

    401 OAuth access token has expired. Re-authenticate to continue.   (71x)
    You've hit your limit - resets 1pm (Europe/Berlin)                 (23x)

Der Nutzer sah drei Tage lang eine Oberflaeche voller gruener Haken, hinter
denen nichts stand: kein Podcast, kein Tagesplan, kein Morgencheck.

Der Grund ist eine Zuschreibung, keine Panne: Der Status kam ausschliesslich aus
dem gemeldeten ``status``-Feld. Ein Lauf, der binnen Sekunden mit einem
Anmeldefehler zurueckkommt, meldet formal „fertig" — und ist damit fuer jede
Ueberwachung unsichtbar, die auf ``failed`` filtert.

**Diese Pruefung gehoert in den Orchestrator, nicht in den Agenten.** Das
bisherige Sicherheitsnetz — ein Kontrolllauf um 08:00 — lief im selben Container
mit derselben Anmeldung und starb am selben 401 (belegt an drei Kennungen). Ein
Selbsttest kann einen Anmeldeausfall grundsaetzlich nicht auffangen.
"""

from __future__ import annotations

import re

#: Wortlaute, die einen Lauf trotz „fertig" als Fehlschlag ausweisen. Bewusst
#: eng gehalten: jede Signatur hier stammt aus einem echten Vorfall. Zu breit
#: gefasst wuerde ein Lauf rot, der ueber einen 401 nur BERICHTET — etwa ein
#: Bericht ueber Anmeldefehler.
_SIGNATUREN: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Zugang abgelaufen", re.compile(
        r"(OAuth access token has expired|Failed to authenticate\.?\s*API Error:\s*401)", re.I)),
    ("Kontingent erschoepft", re.compile(
        r"(You'?ve hit your limit|rate.?limit(ed)? exceeded|429 Too Many Requests)", re.I)),
    ("Zugang abgelehnt", re.compile(
        r"(invalid_grant|refresh_token_reused|credit balance is too low)", re.I)),
)

#: Ein leeres Ergebnis ist nur dann verdaechtig, wenn der Lauf gar keine Zeit
#: hatte, etwas zu tun. Ein langer Lauf ohne Text kann echte Arbeit gewesen sein
#: (etwa eine Datei geschrieben), ein halbsekuendiger nicht.
_ZU_SCHNELL_MS = 10_000


def warum_kein_erfolg(ergebnis: str | None, dauer_ms: int | None) -> str | None:
    """Der Grund, warum ein als „fertig" gemeldeter Lauf keiner war — sonst ``None``.

    Bewusst eine reine Funktion: so sind die Grenzfaelle ohne Datenbank, ohne
    Redis und ohne Agenten pruefbar.
    """
    text = (ergebnis or "").strip()

    for grund, muster in _SIGNATUREN:
        if muster.search(text):
            return grund

    if not text and dauer_ms is not None and 0 <= dauer_ms < _ZU_SCHNELL_MS:
        return "Leeres Ergebnis nach weniger als 10 Sekunden"

    return None


def ist_zugangsproblem(grund: str | None) -> bool:
    """Ob der Grund am Zugang liegt — dann hilft kein Wiederholen, nur Anmelden."""
    return grund in {"Zugang abgelaufen", "Zugang abgelehnt"}


#: Ab wie vielen Fehlschlaegen in Folge gemeldet wird. Ein einzelner Ausrutscher
#: ist Rauschen — 71 in Folge waren ein dreitaegiger Totalausfall, den niemand
#: bemerkt hat. Drei ist die Grenze, ab der ein Muster erkennbar ist, ohne bei
#: jedem Schluckauf zu laermen.
SERIE_MELDEN_AB = 3


def serie_gebrochen(gruende: list[str | None]) -> bool:
    """Bricht diese Folge von Ergebnissen die Schwelle?

    ``gruende`` sind die Ausfallgruende der juengsten Laeufe, neueste zuerst;
    ``None`` steht fuer einen gelungenen Lauf. Gemeldet wird nur, wenn die
    juengsten ``SERIE_MELDEN_AB`` Laeufe ALLE gescheitert sind — ein
    zwischendurch gelungener Lauf beendet die Serie.
    """
    juengste = gruende[:SERIE_MELDEN_AB]
    return len(juengste) >= SERIE_MELDEN_AB and all(g for g in juengste)
