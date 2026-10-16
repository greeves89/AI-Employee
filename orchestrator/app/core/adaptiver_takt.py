"""Der Takt eines wiederkehrenden Zeitplans, der sich selbst anpasst (#941).

Ein Agent, der etwas beobachtet (offene PRs, Issues, Postfach), lief bisher in
einem FESTEN Takt: Gibt es tagelang nichts zu tun, laeuft er trotzdem alle 30
Minuten und erzeugt leere Chat-Karten; macht man ihn langsamer, reagiert er zu
spaet, wenn ploetzlich etwas anfaellt. Ein Mensch wuerde seinen Rhythmus selbst
anpassen — genau das steht hier.

Die Regel in einem Satz: Leere Laeufe zaehlen hoch, ab der Schwelle geht es je
leerem Lauf eine Stufe langsamer, und EIN Lauf mit Arbeit setzt alles auf den
Grundtakt zurueck.

Gemessen an der Abnahme aus #941 (Schwelle 3, Stufen 30 Min / 2 h / 4 h / 8 h):

    Grundtakt         alle 30 Min
    idle  (1. leer)   alle 30 Min   — unter der Schwelle
    idle  (2. leer)   alle 30 Min   — unter der Schwelle
    idle  (3. leer)   alle  2 Std   — Schwelle erreicht: Stufe 1
    idle  (4. leer)   alle  4 Std   — Stufe 2
    idle  (5. leer)   alle  8 Std   — Stufe 3 (letzte, haelt danach)
    worked            alle 30 Min   — sofort zurueck auf den Grundtakt

Bewusst ohne Datenbank und ohne Uhrzeit-Magie: Diese Datei rechnet nur. Wer sie
anwendet (``app.services.takt_service``), haelt das Ergebnis am Zeitplan fest.
``interval_seconds`` bleibt dabei das fuehrende Feld — der Scheduler rechnet
``next_run_at`` weiterhin daraus, und keine seiner Sonderwege (Retry, busy,
Ueberlast) muessen etwas von Adaptivitaet wissen.
"""

import math
from datetime import datetime, timedelta, timezone

#: Die Stufenleiter aus #941: 30 Min, 2 Std, 4 Std, 8 Std. Stufe 0 ist der
#: Grundtakt — er kommt aus ``basis_intervall_s`` und nicht von hier, damit ein
#: Zeitplan mit abweichendem Grundtakt (z. B. 15 Min) seinen behaelt.
STUFEN_STANDARD: tuple[int, ...] = (1800, 7200, 14400, 28800)

#: So viele leere Laeufe hintereinander, BEVOR es langsamer wird.
LEER_SCHWELLE_STANDARD = 3

#: Was ein Lauf gemeldet haben kann. ``waiting`` ("ich warte auf eine
#: Entscheidung des Nutzers") zaehlt wie ``idle``: Es gab keine Arbeit, die der
#: Agent allein erledigen konnte — schneller nachzusehen bringt dann nichts.
ERGEBNISSE = ("worked", "idle", "waiting")

#: Grenzen fuer alles, was von aussen kommt. Ein Agent (oder ein Mensch im
#: Formular) darf seinen Takt setzen, aber nicht in Bereiche, die den Scheduler
#: oder die Anlage belasten: unter einer Minute feuert er dauernd, ueber einer
#: Woche kommt er nie wieder.
MIN_INTERVALL_S = 60
MAX_INTERVALL_S = 7 * 24 * 3600
MAX_STUFEN = 8
MAX_SCHWELLE = 50


#: Zeitplaene, die die Plattform selbst fuehrt: ``[Proactive]`` und
#: ``[Rhythmus]`` bekommen ihr Intervall bei jedem Abgleich aus der
#: Agenten-Konfiguration (``app.core.eigeninitiative.abgleichen``), ``[Plan]``
#: sind Einmal-Laeufe ohne Takt, und ``[Projekt]`` ist der Arbeitstakt eines
#: Projekts: ``app.core.projekt_takt.abgleichen`` setzt bei JEDEM Schreiben am
#: Projekt ``interval_seconds`` und ``next_run_at`` neu. Ein adaptiver Takt
#: wuerde sich mit dem Abgleich gegenseitig ueberschreiben — deshalb ist
#: adaptiv dort verboten (#941, Punkt 4). Das Praefix steht hier woertlich und
#: nicht als Import, damit dieses Modul ohne Projekt-Modell ladbar bleibt;
#: ``test_report_run_zeitplan`` haelt es gegen ``projekt_takt.TAKT_PRAEFIX``.
FREMDGETAKTETE_PRAEFIXE = ("[Proactive]", "[Rhythmus]", "[Plan]", "[Projekt]")


def fremdgetaktet(name: str | None) -> bool:
    """Fuehrt die Plattform den Takt dieses Zeitplans selbst?"""
    return bool(name) and name.startswith(FREMDGETAKTETE_PRAEFIXE)


FREMDGETAKTET_GRUND = (
    "Diesen Zeitplan taktet die Plattform selbst ([Proactive]/[Rhythmus] aus der "
    "Agenten-Konfiguration, [Plan] ist ein Einmal-Lauf, [Projekt] der Arbeitstakt "
    "eines Projekts). Ein adaptiver Takt würde "
    "sich damit gegenseitig überschreiben. Lege für einen Beobachtungs-Job einen "
    "eigenen Zeitplan an."
)


#: Dieser Satz haengt am Prompt JEDES adaptiven Zeitplan-Laufs (#941, Punkt 3).
#: Ohne ihn erfaehrt der Lauf nie, dass er melden muss — Rechenregel und
#: Werkzeug waeren korrekt, der Takt bewegte sich trotzdem nie, und nichts
#: wuerde davor warnen.
MELDE_HINWEIS = (
    "\n\n---\n"
    "**Takt melden (Pflicht, genau EINMAL am Ende dieses Laufs):** Dieser Zeitplan "
    "taktet sich selbst. Rufe als letzte Handlung `report_run` auf:\n"
    "- `outcome=\"worked\"` — es gab Arbeit und du hast sie erledigt (zurück auf den Grundtakt)\n"
    "- `outcome=\"idle\"` — es gab nichts zu tun\n"
    "- `outcome=\"waiting\"` — du wartest auf eine Entscheidung des Nutzers (zählt wie idle)\n"
    "Ohne diese Meldung bleibt der Takt für immer stehen. Ein Aufruf genügt; "
    "eine zweite Meldung zum selben Lauf wird nicht gezählt."
)


def ist_leer(ergebnis: str) -> bool:
    """``idle`` und ``waiting`` sind leere Laeufe, ``worked`` ist Arbeit."""
    return ergebnis in ("idle", "waiting")


def stufen_pruefen(rohwert) -> list[int]:
    """Eine Stufenleiter aus fremder Eingabe — oder ``ValueError``.

    Absichtlich streng und NICHT rettend: Ein stillschweigend zurechtgebogener
    Takt (``"30"`` als Zeichenkette, 0 Sekunden, 400 Stufen) ist schlimmer als
    eine Fehlermeldung, weil er erst Wochen spaeter als merkwuerdiger Rhythmus
    auffaellt. ``bool`` ist in Python ein ``int`` — hier aber sicher ein Irrtum.
    """
    if not isinstance(rohwert, (list, tuple)):
        raise ValueError("stufen muss eine Liste von Sekundenwerten sein")
    werte: list[int] = []
    for eintrag in rohwert:
        if isinstance(eintrag, bool) or not isinstance(eintrag, int):
            raise ValueError("stufen darf nur ganze Sekundenwerte enthalten")
        if not MIN_INTERVALL_S <= eintrag <= MAX_INTERVALL_S:
            raise ValueError(
                f"Jede Stufe muss zwischen {MIN_INTERVALL_S} und {MAX_INTERVALL_S} "
                f"Sekunden liegen (war {eintrag})"
            )
        werte.append(eintrag)
    if not werte:
        raise ValueError("stufen braucht mindestens eine Stufe")
    if len(werte) > MAX_STUFEN:
        raise ValueError(f"Hoechstens {MAX_STUFEN} Stufen")
    if any(b <= a for a, b in zip(werte, werte[1:])):
        raise ValueError("stufen muss aufsteigend sein (jede Stufe langsamer als die davor)")
    return werte


def stufen_von(schedule, basis: int | None = None) -> list[int]:
    """Die Stufenleiter eines Zeitplans — mit Rueckfall auf den Standard.

    Hier wird NICHT geprueft: Was in der Datenbank steht, hat die Pruefung beim
    Schreiben schon bestanden. Unbrauchbares (Altbestand, von Hand verbogen)
    faellt auf den Standard zurueck, statt den Lauf scheitern zu lassen — ein
    Zeitplan, der wegen eines kaputten Feldes gar nicht mehr feuert, waere der
    schlimmere Ausgang.
    """
    try:
        werte = stufen_pruefen(getattr(schedule, "stufen", None))
    except (ValueError, TypeError):
        werte = list(STUFEN_STANDARD)
    grund = basis if basis is not None else basis_von(schedule)
    # Stufe 0 IST der Grundtakt. Steht an der Leiter etwas anderes, gewinnt der
    # Grundtakt: sonst liefe ein Zeitplan mit 15-Min-Grundtakt nach dem ersten
    # „worked" ploetzlich mit 30 Min weiter.
    #
    # Danach muss die Leiter AUFSTEIGEND bleiben, sonst kehrt sich der ganze
    # Zweck um: Bei Grundtakt 8 Std entstuende [28800, 7200, 14400, 28800] und
    # damit die Folge 8 Std, 2 Std, 4 Std, 8 Std — der Beobachter liefe im
    # Leerlauf VIERMAL haeufiger als im Grundtakt. Jede Stufe, die nicht
    # langsamer als der Grundtakt ist, faellt deshalb weg. Bleibt nur der
    # Grundtakt uebrig, ist das die richtige Antwort: Langsamer als der eigene
    # Grundtakt soll es nicht gehen.
    return [grund] + [wert for wert in werte if wert > grund]


def basis_von(schedule) -> int:
    """Der Grundtakt: ``basis_intervall_s``, sonst das aktuelle Intervall."""
    for wert in (getattr(schedule, "basis_intervall_s", None),
                 getattr(schedule, "interval_seconds", None)):
        if isinstance(wert, int) and wert >= MIN_INTERVALL_S:
            return wert
    return STUFEN_STANDARD[0]


def schwelle_von(schedule) -> int:
    wert = getattr(schedule, "leer_schwelle", None)
    if isinstance(wert, int) and 1 <= wert <= MAX_SCHWELLE:
        return wert
    return LEER_SCHWELLE_STANDARD


def stufe_fuer(leerlaeufe: int, schwelle: int, anzahl_stufen: int) -> int:
    """Welche Stufe nach ``leerlaeufe`` leeren Laeufen gilt.

    Unter der Schwelle Stufe 0 (Grundtakt). Ab der Schwelle je weiterer leerer
    Lauf eine Stufe tiefer, bis die letzte erreicht ist — danach haelt sie. So
    ergibt die Folge aus #941 genau 30 Min, 30 Min, 2 h, 4 h, 8 h, 8 h, …
    """
    if leerlaeufe < schwelle:
        return 0
    return min(leerlaeufe - schwelle + 1, max(anzahl_stufen - 1, 0))


def takt_nach_meldung(ergebnis: str, leerlaeufe: int, schwelle: int,
                      anzahl_stufen: int) -> tuple[int, int]:
    """(leerlaeufe, stufe) nach einer Lauf-Meldung."""
    if ergebnis not in ERGEBNISSE:
        raise ValueError(f"Unbekanntes Lauf-Ergebnis: {ergebnis!r}")
    if not ist_leer(ergebnis):
        return 0, 0
    neu = max(leerlaeufe, 0) + 1
    return neu, stufe_fuer(neu, schwelle, anzahl_stufen)


def naechster_lauf(anker: datetime | None, jetzt: datetime, intervall: int) -> datetime:
    """Der naechste Lauf ``intervall`` nach dem Lauf, der gerade gemeldet wurde.

    ``anker`` ist der ZULETZT gefeuerte Lauf (``last_run_at``) — nicht
    ``next_run_at``: Der Scheduler hat den beim Feuern schon um den ALTEN Takt
    weitergestellt, und von dort weiterzurechnen ergaebe nach einem Wechsel auf
    8 Std einen Abstand von 25 Minuten. Ganzzahlige Schritte wie in
    ``scheduler_service._calc_next_run``, damit die Phase nicht wandert, und
    immer in der Zukunft.
    """
    intervall = max(int(intervall), MIN_INTERVALL_S)
    if anker is None:
        return jetzt + timedelta(seconds=intervall)
    if anker.tzinfo is None:  # SQLite verliert tzinfo beim Umlauf, Postgres nie
        anker = anker.replace(tzinfo=timezone.utc)
    schritte = max(math.floor((jetzt - anker).total_seconds() / intervall) + 1, 1)
    return anker + timedelta(seconds=schritte * intervall)


def beschreibe(schedule) -> str:
    """Die Taktzeile fuer die Zeitplan-Ansicht (#941, Punkt 4).

    „alle 4 Std (5 Leerläufe), Grundtakt alle 30 Min" — leer, wenn der Zeitplan
    nicht adaptiv ist, damit die Oberflaeche ihre bisherige Zeile behaelt.
    """
    if not getattr(schedule, "adaptive", False):
        return ""
    from app.core.plan_rhythm import describe_schedule
    jetzt = describe_schedule(schedule)
    leer = int(getattr(schedule, "leerlaeufe", 0) or 0)
    basis = basis_von(schedule)
    grund = describe_schedule(_NurIntervall(basis))
    zeile = jetzt
    if leer:
        zeile += f" ({leer} {'Leerlauf' if leer == 1 else 'Leerläufe'})"
    if basis != (getattr(schedule, "interval_seconds", 0) or 0):
        zeile += f", Grundtakt {grund}"
    return zeile


class _NurIntervall:
    """Hilfsgestalt, damit ``describe_schedule`` den Grundtakt formulieren kann."""

    def __init__(self, sekunden: int):
        self.interval_seconds = sekunden
        self.cron_expression = None
