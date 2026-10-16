"""Lauf-Meldung eines Agenten auf den Takt seines Zeitplans anwenden (#941).

Zwei Dinge passieren hier, und sie sind absichtlich getrennt:

1. **Zuordnen** (``zeitplan_des_laufs``): Welcher Zeitplan gehoert zu dem Lauf,
   der GERADE arbeitet? Der Agent nennt keine Zeitplan-Kennung — er meldet nur,
   wie sein Lauf ausging. Die Zuordnung laeuft ueber den Auftrag: Jeder Lauf
   eines Zeitplans traegt ``metadata.schedule_id`` (siehe
   ``scheduler_service._execute_schedule``). Das ist auch die Zugriffsgrenze:
   Wer keinen laufenden Zeitplan-Auftrag hat, kann keinen Takt veraendern —
   auch nicht den eigenen.
2. **Rechnen und festhalten** (``takt_anwenden``): Die Rechenregel steht in
   ``app.core.adaptiver_takt`` und kennt keine Datenbank.

Der Taktwechsel landet in den Metadaten des Auftrags (Lauf-Verlauf) und im
Protokoll — ausdruecklich NICHT als Benachrichtigung: Ein Agent, der sich
dreimal am Tag selbst umtaktet, darf dem Nutzer nicht dreimal klingeln (#941,
Punkt 4).
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.adaptiver_takt import (
    basis_von,
    beschreibe,
    ist_leer,
    naechster_lauf,
    schwelle_von,
    stufen_von,
    takt_nach_meldung,
)
from app.core.log_redaction import scrub_log
from app.models.schedule import Schedule
from app.models.task import Task, TaskStatus

logger = logging.getLogger(__name__)


class KeinZeitplanLauf(LookupError):
    """Die Meldung gehoert zu keinem Lauf eines Zeitplans.

    Eigene Fehlerklasse, damit der Endpunkt sie in eine klare Antwort
    uebersetzen kann statt sie still zu verschlucken (#941, Abnahme).
    """


async def zeitplan_des_laufs(
    db, agent_id: str | None, task_id: str | None = None
) -> tuple[Schedule, Task]:
    """Der Zeitplan, zu dem der laufende Auftrag dieses Agenten gehoert.

    Mit ``task_id`` (der Agent kennt sie aus seinem Auftragstext) wird genau
    dieser Auftrag genommen; ohne sie der passendste unter den LAUFENDEN
    Auftraegen des Agenten, die zu einem Zeitplan gehoeren — nach der Rangfolge
    weiter unten, nicht einfach der juengste. Beides muss dem Agenten gehoeren —
    eine fremde Auftragskennung fuehrt in denselben Fehler wie eine unbekannte,
    damit sie nicht zum Suchwerkzeug fuer fremde Auftraege wird.
    """
    if not agent_id:
        raise KeinZeitplanLauf(
            "Kein Agent zum Melden erkennbar: report_run gehört in den Lauf eines "
            "Zeitplans."
        )
    if task_id:
        task = await db.get(Task, task_id)
        if task is None or task.agent_id != agent_id:
            raise KeinZeitplanLauf(
                f"Auftrag {task_id} gehört nicht zu diesem Agenten."
            )
        # Dieselbe Bedingung wie auf dem Weg OHNE Auftragsangabe (dort filtert
        # die Abfrage auf RUNNING): Gemeldet wird das Ergebnis des Laufs, der
        # GERADE laeuft. Ohne diese Pruefung liesse sich ein beliebig alter
        # eigener Zeitplan-Lauf nachtraeglich melden und der Takt damit von
        # aussen verstellen (#941, Punkt 1).
        if task.status != TaskStatus.RUNNING:
            raise KeinZeitplanLauf(
                f"Auftrag {task_id} läuft nicht mehr (Status {task.status.value}). "
                "report_run meldet das Ergebnis des laufenden Zeitplan-Laufs."
            )
        kandidaten = [task]
    else:
        kandidaten = list((await db.execute(
            select(Task)
            .where(Task.agent_id == agent_id, Task.status == TaskStatus.RUNNING)
            .order_by(Task.started_at.desc().nullslast(), Task.created_at.desc())
            .limit(20)
        )).scalars().all())
        if not kandidaten:
            raise KeinZeitplanLauf(
                "Kein laufender Auftrag gefunden. report_run meldet das Ergebnis "
                "eines ZEITPLAN-Laufs — im Chat gibt es nichts zu melden."
            )

    # Ohne Auftragsangabe ist der JUENGSTE laufende Zeitplan-Auftrag nicht
    # automatisch der gemeinte: Ein Agent kann mehrere gleichzeitig haben —
    # neben dem adaptiven Beobachter etwa den Arbeitstakt eines Projekts oder
    # einen Probelauf von Hand („Jetzt ausfuehren"), der dieselbe
    # ``schedule_id`` traegt. Ist so einer juenger, landete die Meldung auf
    # einem Zeitplan, der sie nicht braucht: der echte Lauf bekam „nicht
    # adaptiv, Takt bleibt" bzw. „Probelauf zaehlt nicht" zu hoeren, durfte
    # wegen ``schon_gemeldet`` nicht nachfassen, und der adaptive Zeitplan
    # taktete nie.
    #
    # Entschieden wird deshalb am MELDE-AUFTRAG und nicht am Zustand des
    # Zeitplans: ``scheduler_service`` stempelt ``adaptive`` in die Metadaten
    # JEDES Laufs, den es absetzt, und haengt genau diesen Laeufen den
    # Melde-Hinweis an den Prompt (``MELDE_HINWEIS``). Ein Probelauf von Hand
    # bekommt beides nicht. Damit ist „dieser Lauf soll melden" eine Eigenschaft
    # des Laufs, keine Vermutung.
    #
    # Gibt es MEHRERE solche Laeufe, wird nicht geraten: Welcher von ihnen
    # gerade meldet, ist von aussen nicht entscheidbar, und ein Fehlgriff
    # zaehlte einem fremden Zeitplan einen Leerlauf an UND verbraucht dessen
    # echte Meldung (``schon_gemeldet``). Dann lieber 409 mit der Bitte um die
    # Auftragskennung — die steht im Auftragstext.
    treffer: list[tuple[int, Schedule, Task]] = []
    for task in kandidaten:
        meta = task.metadata_ or {}
        schedule_id = meta.get("schedule_id")
        if not schedule_id:
            continue
        schedule = await db.get(Schedule, schedule_id)
        if schedule is None:
            continue
        if task_id:
            return schedule, task
        # Ein Zeitplan, der inzwischen einem ANDEREN Agenten gehoert
        # (``update_schedule(agent_id=…)``), kommt zuletzt: Sonst gewaenne ein
        # noch laufender Alt-Auftrag, und die Zugriffspruefung im Endpunkt
        # beantwortete jede Meldung dieses Agenten mit 403 — auch die seines
        # eigenen Laufs.
        fremder_zeitplan = schedule.agent_id is not None and schedule.agent_id != agent_id
        melde_auftrag = bool(meta.get("adaptive")) and not meta.get("manual_trigger")
        rang = (0 if melde_auftrag else 1) + (2 if fremder_zeitplan else 0)
        treffer.append((rang, schedule, task))

    melde_auftraege = [eintrag for eintrag in treffer if eintrag[0] == 0]
    if len(melde_auftraege) > 1:
        raise KeinZeitplanLauf(
            f"Dieser Agent hat gerade {len(melde_auftraege)} Läufe adaptiver "
            "Zeitpläne offen — welcher meldet, ist von hier aus nicht "
            "entscheidbar. Rufe report_run mit der task_id deines eigenen Laufs "
            "auf (sie steht in deinem Auftrag); geraten wird hier nicht."
        )
    if treffer:
        # Stabil: bei gleichem Rang bleibt die Reihenfolge der Abfrage
        # (juengster Lauf zuerst) erhalten.
        treffer.sort(key=lambda eintrag: eintrag[0])
        return treffer[0][1], treffer[0][2]

    raise KeinZeitplanLauf(
        "Dieser Lauf gehört zu keinem Zeitplan — es gibt keinen Takt, der "
        "angepasst werden könnte. report_run brauchst du nur am Ende eines "
        "wiederkehrenden Zeitplan-Laufs."
    )


def takt_anwenden(schedule: Schedule, ergebnis: str, jetzt: datetime | None = None) -> dict:
    """Die Meldung auf den Zeitplan anwenden. Gibt die Veraenderung zurueck.

    Zaehler und Stufe werden IMMER gefuehrt, sobald der Zeitplan adaptiv ist —
    auch wenn sich das Intervall dadurch (noch) nicht aendert. ``next_run_at``
    wird nur angefasst, wenn der Takt tatsaechlich wechselt: Sonst wanderte die
    Phase des Zeitplans bei jeder Meldung ein Stueck weiter.
    """
    jetzt = jetzt or datetime.now(timezone.utc)
    vorher = schedule.interval_seconds

    if not getattr(schedule, "adaptive", False):
        return {
            "adaptive": False,
            "geaendert": False,
            "ergebnis": ergebnis,
            "intervall_vorher_s": vorher,
            "intervall_s": vorher,
            "leerlaeufe": int(getattr(schedule, "leerlaeufe", 0) or 0),
            "stufe": int(getattr(schedule, "stufe", 0) or 0),
            "hinweis": (
                "Dieser Zeitplan ist nicht adaptiv — der Takt bleibt, wie er ist. "
                "Mit update_schedule(adaptive=true) stellst du ihn um."
            ),
        }
    if schedule.cron_expression or not schedule.interval_seconds:
        # Darf nach dem Anlegen nicht mehr vorkommen (die Schemas lassen es
        # nicht zu), wird aber nicht geraten: ein Cron-Zeitplan hat kein
        # Intervall, das man verdoppeln koennte.
        return {
            "adaptive": True,
            "geaendert": False,
            "ergebnis": ergebnis,
            "intervall_vorher_s": vorher,
            "intervall_s": vorher,
            "leerlaeufe": int(getattr(schedule, "leerlaeufe", 0) or 0),
            "stufe": int(getattr(schedule, "stufe", 0) or 0),
            "hinweis": "Adaptiv gilt nur für Intervall-Zeitpläne; dieser läuft nach Cron.",
        }

    basis = basis_von(schedule)
    stufen = stufen_von(schedule, basis)
    schwelle = schwelle_von(schedule)
    leerlaeufe, stufe = takt_nach_meldung(
        ergebnis, int(getattr(schedule, "leerlaeufe", 0) or 0), schwelle, len(stufen)
    )
    neues = stufen[stufe]

    schedule.basis_intervall_s = basis
    schedule.leerlaeufe = leerlaeufe
    schedule.stufe = stufe
    if neues != vorher:
        schedule.interval_seconds = neues
        # Anker ist der Lauf, der gerade gemeldet wird — nicht ``next_run_at``,
        # das der Scheduler beim Feuern schon um den ALTEN Takt weitergestellt
        # hat (siehe adaptiver_takt.naechster_lauf). ``last_run_at`` ist der
        # SLOT des Laufs, nicht der Zeitpunkt, an dem der Agent ihn abgeholt
        # hat — deshalb nicht ``task.started_at``, das bei Wartezeit oder
        # Neustart Stunden spaeter liegt und die Phase wandern liesse.
        schedule.next_run_at = naechster_lauf(schedule.last_run_at, jetzt, neues)

    return {
        "adaptive": True,
        "geaendert": neues != vorher,
        "ergebnis": ergebnis,
        "leer": ist_leer(ergebnis),
        "intervall_vorher_s": vorher,
        "intervall_s": neues,
        "leerlaeufe": leerlaeufe,
        "leer_schwelle": schwelle,
        "stufe": stufe,
        "stufen_s": stufen,
        "basis_intervall_s": basis,
        "next_run_at": schedule.next_run_at,
        "takt": beschreibe(schedule),
    }


def wechsel_in_den_lauf_schreiben(task: Task, wechsel: dict) -> None:
    """Den Taktwechsel am Auftrag festhalten — der Lauf-Verlauf des Zeitplans.

    Bewusst keine Benachrichtigung: Taktwechsel sind Routine und sollen niemanden
    wecken (#941, Punkt 4). Neues dict, weil SQLAlchemy eine Veraenderung IN
    einem JSON-Feld nicht bemerkt.
    """
    task.metadata_ = {
        **(task.metadata_ or {}),
        "lauf_ergebnis": wechsel.get("ergebnis"),
        "takt_wechsel": {
            "von_s": wechsel.get("intervall_vorher_s"),
            "auf_s": wechsel.get("intervall_s"),
            "leerlaeufe": wechsel.get("leerlaeufe"),
            "stufe": wechsel.get("stufe"),
            "adaptive": bool(wechsel.get("adaptive")),
            "gemeldet_am": datetime.now(timezone.utc).isoformat(),
        },
    }


def schon_gemeldet(task: Task, ergebnis: str | None = None) -> dict | None:
    """Die bereits abgelegte Meldung dieses Laufs — oder ``None`` (#941, Punkt 1).

    ``report_run`` ist nicht von Natur aus idempotent: Ein Transportfehler
    laesst das Modell denselben Aufruf wiederholen, und drei gleiche Meldungen
    aus EINEM Lauf ergaeben drei Leerlaeufe — der Takt faellt dann nach einem
    einzigen stillen Tag auf 8 Stunden. Der Auftrag ist der natuerliche
    Schluessel dagegen: Ein Lauf meldet genau einmal. Jede weitere Meldung
    liefert dasselbe Ergebnis zurueck, ohne erneut zu zaehlen.
    """
    frueher = (task.metadata_ or {}).get("takt_wechsel")
    if not isinstance(frueher, dict):
        return None
    # EINE Ausnahme: „worked" nach einer leeren Meldung ist keine Dublette,
    # sondern eine Korrektur in die ungefaehrliche Richtung — der Lauf hat doch
    # noch Arbeit gefunden. Sie macht den Zeitplan hoechstens schneller, also
    # genau das, was auch ohne adaptiven Takt gaelte. Verschluckt man sie,
    # bleibt ein arbeitender Beobachter auf dem Leerlauf-Takt stehen.
    if ergebnis == "worked" and (task.metadata_ or {}).get("lauf_ergebnis") != "worked":
        return None
    return {
        "adaptive": bool(frueher.get("adaptive")),
        "geaendert": False,
        "doppelmeldung": True,
        "ergebnis": (task.metadata_ or {}).get("lauf_ergebnis"),
        "intervall_vorher_s": frueher.get("von_s"),
        "intervall_s": frueher.get("auf_s"),
        "leerlaeufe": frueher.get("leerlaeufe"),
        "stufe": frueher.get("stufe"),
        "hinweis": (
            "Dieser Lauf hat sein Ergebnis bereits gemeldet — die Meldung wurde "
            "nicht doppelt gezählt. Pro Lauf genügt ein report_run."
        ),
    }


async def lauf_melden(db, schedule: Schedule, task: Task, ergebnis: str,
                      jetzt: datetime | None = None) -> dict:
    """Meldung anwenden, festhalten, speichern."""
    # Ein Probelauf von Hand („Jetzt ausführen") ist kein Takt-Lauf: Er faellt
    # nicht in den Rhythmus des Zeitplans, und als Leerlauf gezaehlt wuerde er
    # den Beobachter ausbremsen, nur weil jemand nachgesehen hat.
    if (task.metadata_ or {}).get("manual_trigger"):
        return {
            "adaptive": bool(getattr(schedule, "adaptive", False)),
            "geaendert": False,
            "ergebnis": ergebnis,
            "intervall_vorher_s": schedule.interval_seconds,
            "intervall_s": schedule.interval_seconds,
            "leerlaeufe": int(getattr(schedule, "leerlaeufe", 0) or 0),
            "stufe": int(getattr(schedule, "stufe", 0) or 0),
            "hinweis": (
                "Das war ein Probelauf von Hand — er zählt nicht für den Takt. "
                "Der Takt folgt nur den regulären Läufen des Zeitplans."
            ),
        }
    doppelt = schon_gemeldet(task, ergebnis)
    if doppelt is not None:
        logger.info(
            "[Takt] Zeitplan '%s': zweite Meldung zum selben Lauf — nicht gezählt",
            scrub_log(schedule.name),
        )
        return doppelt
    wechsel = takt_anwenden(schedule, ergebnis, jetzt)
    wechsel_in_den_lauf_schreiben(task, wechsel)
    await db.commit()
    await db.refresh(schedule)
    if wechsel.get("geaendert"):
        logger.info(
            "[Takt] Zeitplan '%s': %s -> Takt %ss (vorher %ss), %s Leerläufe, Stufe %s",
            scrub_log(schedule.name), scrub_log(ergebnis), wechsel["intervall_s"],
            wechsel["intervall_vorher_s"], wechsel["leerlaeufe"], wechsel["stufe"],
        )
    return wechsel
