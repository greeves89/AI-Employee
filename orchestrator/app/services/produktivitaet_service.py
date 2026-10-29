"""Produktivitaetsmonitor: Koordination gegen Fortschritt je Gruppe (#882).

## Die Fehlerklasse

Arbeiten mehrere Agenten lange zusammen, waechst der Anteil an Koordination —
Rueckfragen, Uebergaben, Meetings, Folgemeetings, Freigaben, Wiederholungen —
waehrend der tatsaechliche Fortschritt stagniert. Jeder einzelne Schritt ist
lokal sinnvoll: nach einem Fehler kommt eine Pruefung dazu, dann eine Pruefung
der Pruefung. Kein einzelner Agent kann das sehen, weil keiner die Summe kennt.
Im Projekt ist das belegt: ``MAX_FOLLOWUP_DEPTH = 3`` in ``api/meeting_rooms.py``
existiert, weil Folgemeetings sich sonst endlos gegenseitig erzeugten.

``loop_detector.py`` im Agenten sieht nur Schleifen EINES Agenten in EINEM Lauf,
``self_test_service`` nur haengende Auftraege, ``/health/auto-metrics`` nur
Erfolgsquote, Kosten und Dauer. Das Verhaeltnis Koordination zu Fortschritt sieht
bisher niemand.

## Was gezaehlt wird

**Koordination** (jedes Ereignis zaehlt einfach, je Agent zugeordnet):

* Agentennachrichten (``agent_messages``, der SENDER zaehlt)
* Meeting-Beitraege (``meeting_rooms.messages``, Eintraege mit ``agent_id`` im Fenster)
* Folgemeetings (Raeume mit ``parent_room_id``, im Fenster angelegt)
* angelegte Todos (``agent_todos``)
* von einem Agenten angelegte Auftraege (``tasks`` mit ``metadata.created_by_agent``).
  Zugeordnet wird ueber den EMPFAENGER (``Task.agent_id``). Ein Auftrag, den ein
  Agent der Gruppe nach AUSSEN gibt — an einen Agenten, den der Aufrufer nicht
  sehen darf —, zaehlt daher nicht mit. Das ist bewusst so: um ihn zu zaehlen,
  muesste diese Stelle den Auftragsdatensatz eines fremden Agenten lesen.
* Wiederholungen der Selbstheilung (``tasks`` mit ``metadata.heal_attempt``)
* Freigabe-Anfragen (``command_approvals``)

**Fortschritt**:

* abgeschlossene Auftraege MIT Ergebnis — ``status == completed`` und
  ``len(result) >= MIN_ERGEBNIS_ZEICHEN``. Die Laengenpruefung ist noetig, weil
  ein abgebrochener Lauf ebenfalls ``completed`` meldet, aber ein leeres oder
  sehr kurzes Ergebnisfeld hat (dieselbe Zustandspruefung wie bei der
  Ausfall-Klassifikation). Ein Auftrag, den der Nutzer ausdruecklich als NICHT
  erfuellt bewertet hat (``task_ratings.fulfilled is False``), zaehlt NICHT.
Meeting-Ergebnisse (``meeting_rooms.deliverable_integrated``) zaehlen bewusst
NICHT mit: das Feld ist ein Schalter ohne Zeitstempel. Ueber ``updated_at``
ausgewaehlt haette ein vor zwei Monaten uebernommenes Ergebnis als heutiger
Fortschritt gezaehlt, sobald im Raum irgendetwas passiert — und derselbe Raum
haette in jeder beteiligten Gruppe gezaehlt. Ein falsch gezaehltes Ergebnis ist
hier besonders teuer: es unterdrueckt die Warnung.

## Warum genau zwei Schwellen

``min_koordination`` ist die absolute Untergrenze: unter ihr alarmiert nichts,
damit ein ruhiger Tag ruhig bleibt. ``verhaeltnis`` ist der eigentliche Befund —
Koordinationsereignisse je Fortschritt. Ein dritter Knopf („Fortschritt unter X")
waere bei diesen Vorgaben fast deckungsgleich mit dem Verhaeltnis und muesste
zusaetzlich geeicht werden; stattdessen unterdrueckt echter Fortschritt den Alarm
anteilig: 100 Koordinationsereignisse bei 13 gelieferten Ergebnissen ergeben 7,7
und bleiben still, dieselben 100 bei 0 Ergebnissen ergeben 100 und melden sich.

Die Trennschaerfe der Vorgabewerte ist in
``tests/test_produktivitaet_monitor.py`` gemessen und als Randabstand
festgehalten (``max(ruhig) < min(auffaellig)``), nicht als Mittelwert.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select

logger = logging.getLogger(__name__)

#: Ab wie vielen Zeichen ein Ergebnisfeld als geliefertes Ergebnis gilt.
#: Ein abgebrochener Lauf meldet ebenfalls ``completed``, schreibt aber keinen
#: Bericht. Ohne diese Pruefung zaehlten Ausfaelle als Fortschritt — und der
#: Monitor haette ausgerechnet dann geschwiegen, wenn es am noetigsten ist.
MIN_ERGEBNIS_ZEICHEN = 200

#: Vorgabewerte. Ueberschreibbar je Anlage ueber ``platform_settings``
#: (Schluessel siehe ``_SETTINGS_KEY_MAP``), dokumentiert in
#: ``docs/OPERATIONS.md``.
DEFAULTS: dict[str, Any] = {
    # Beobachtungsfenster in Stunden.
    "fenster_stunden": 24,
    # Absolute Untergrenze: so viele Koordinationsereignisse muss eine Gruppe im
    # Fenster haben, bevor ueberhaupt bewertet wird.
    "min_koordination": 20,
    # Koordinationsereignisse je geliefertem Ergebnis, ab denen gewarnt wird.
    "verhaeltnis": 8.0,
    # Wie lange nach einer Meldung dieselbe Gruppe nicht erneut gemeldet wird.
    # Ohne Pause meldete der Waechter denselben Zustand bei jedem Durchlauf —
    # und eine taeglich wiederholte Warnung liest niemand mehr.
    "melde_pause_stunden": 24,
    # Zusaetzlich zur Warnung einen Analyse-Auftrag an den Team-Lead stellen.
    # Standard AUS: der Auftrag kostet einen Modelllauf, und die Warnung mit
    # Aufschluesselung steht auch ohne ihn auf der Health-Seite.
    "analyse_auftrag": False,
}

#: Erlaubter Bereich je Zahl-Einstellung. Ohne diese Grenzen machte ein
#: ``produktivitaet_fenster_stunden = 0`` den Waechter STILL blind: das Fenster
#: waere leer, die Koordination immer 0, und nichts im Protokoll haette darauf
#: hingewiesen. Werte ausserhalb werden auf den Rand gezogen und gemeldet.
GRENZEN: dict[str, tuple[float, float]] = {
    "fenster_stunden": (1, 720),
    "min_koordination": (1, 100000),
    "verhaeltnis": (1.0, 100000.0),
    # 0 ist hier erlaubt und heisst ausdruecklich „keine Pause" (jede Pruefung
    # meldet erneut) — dokumentiert in docs/OPERATIONS.md.
    "melde_pause_stunden": (0, 8760),
}

#: ``platform_settings``-Schluessel → Schluessel in :data:`DEFAULTS`.
_SETTINGS_KEY_MAP = {
    "produktivitaet_fenster_stunden": "fenster_stunden",
    "produktivitaet_min_koordination": "min_koordination",
    "produktivitaet_verhaeltnis": "verhaeltnis",
    "produktivitaet_melde_pause_stunden": "melde_pause_stunden",
    "produktivitaet_analyse_auftrag": "analyse_auftrag",
}

#: Wie oft der Hintergrundwaechter nachsieht.
_PRUEF_INTERVALL_SEKUNDEN = 3600

#: Marke in ``Notification.meta``, an der die Meldepause ihre eigenen
#: Vormeldungen wiedererkennt.
MELDUNG_ART = "produktivitaet_koordination"

#: Wie viele Beispiele je Gruppe mitgeliefert werden (Drill-down).
MAX_BEISPIELE = 5


@dataclass
class AgentAnteil:
    """Der Beitrag EINES Agenten zur Bilanz seiner Gruppe."""

    agent_id: str
    agent_name: str
    koordination: int = 0
    fortschritt: int = 0
    arten: dict[str, int] = field(default_factory=dict)

    def zu_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "koordination": self.koordination,
            "fortschritt": self.fortschritt,
            "arten": dict(sorted(self.arten.items())),
        }


@dataclass
class Bilanz:
    """Koordination gegen Fortschritt fuer eine Menge von Agenten."""

    fenster_stunden: int
    seit: datetime
    koordination: int = 0
    fortschritt: int = 0
    arten: dict[str, int] = field(default_factory=dict)
    je_agent: list[AgentAnteil] = field(default_factory=list)
    beispiele: list[dict] = field(default_factory=list)

    @property
    def verhaeltnis(self) -> float:
        """Koordinationsereignisse je geliefertem Ergebnis.

        Ohne Fortschritt wird durch 1 geteilt, nicht durch 0 — das Ergebnis ist
        dann die Zahl der Koordinationsereignisse selbst. Ein gerundeter Wert
        waere hier falsch: er wuerde knapp unter der Schwelle liegende Gruppen
        hochrunden.
        """
        return self.koordination / max(self.fortschritt, 1)

    def zu_dict(self) -> dict:
        return {
            "fenster_stunden": self.fenster_stunden,
            "seit": self.seit.isoformat(),
            "koordination": self.koordination,
            "fortschritt": self.fortschritt,
            "verhaeltnis": round(self.verhaeltnis, 2),
            "arten": dict(sorted(self.arten.items())),
            "je_agent": [a.zu_dict() for a in sorted(
                self.je_agent, key=lambda x: (-x.koordination, x.agent_name)
            )],
            "beispiele": self.beispiele,
        }


@dataclass
class Befund:
    """Das Urteil zu einer Gruppe."""

    gruppe_id: str
    gruppe_name: str
    art: str  # "team" | "nutzer"
    bilanz: Bilanz
    warnung: bool
    begruendung: str
    melde_agent_id: str | None = None
    #: Alle sichtbaren Mitglieder der Gruppe. Die Meldepause sucht ihre
    #: Vormeldung ueber ALLE davon: haengt sie nur am Empfaenger, meldet ein
    #: Lead-Wechsel (oder ein neu angelegter Agent, der in der Sortierung
    #: vorrueckt) denselben Zustand sofort ein zweites Mal.
    mitglieder: frozenset[str] = frozenset()

    def zu_dict(self) -> dict:
        return {
            "gruppe_id": self.gruppe_id,
            "gruppe_name": self.gruppe_name,
            "art": self.art,
            "warnung": self.warnung,
            "begruendung": self.begruendung,
            **self.bilanz.zu_dict(),
        }


async def lade_schwellen(db) -> dict[str, Any]:
    """Vorgaben, ueberschrieben durch ``platform_settings``.

    Unbrauchbare Werte lassen die Vorgabe stehen und werden protokolliert — eine
    Zahl-Einstellung, in der Text steht, faellt sonst erst beim Rechnen auf.
    """
    from app.models.platform_settings import PlatformSettings

    schwellen = dict(DEFAULTS)
    try:
        zeilen = await db.execute(
            select(PlatformSettings).where(
                PlatformSettings.key.in_(list(_SETTINGS_KEY_MAP.keys()))
            )
        )
        rows = list(zeilen.scalars().all())
    except Exception:  # pragma: no cover - Ablage nicht erreichbar
        logger.debug("[Produktivitaet] Einstellungen nicht lesbar — Vorgaben", exc_info=True)
        return schwellen

    for row in rows:
        ziel = _SETTINGS_KEY_MAP.get(row.key)
        if not ziel or not row.value:
            continue
        vorgabe = DEFAULTS[ziel]
        try:
            # bool VOR int pruefen: in Python ist ein bool ein int.
            if isinstance(vorgabe, bool):
                schwellen[ziel] = str(row.value).strip().lower() in ("true", "1", "yes", "ja")
            elif isinstance(vorgabe, int):
                schwellen[ziel] = int(row.value)
            elif isinstance(vorgabe, float):
                schwellen[ziel] = float(row.value)
        except (TypeError, ValueError):
            logger.warning(
                "[Produktivitaet] Einstellung %s ist unbrauchbar (%r) — Vorgabe %r bleibt",
                row.key, row.value, vorgabe,
            )

    for ziel, (unten, oben) in GRENZEN.items():
        wert = schwellen[ziel]
        gezogen = min(max(wert, unten), oben)
        if gezogen != wert:
            logger.warning(
                "[Produktivitaet] %s = %r liegt ausserhalb von [%s, %s] — gerechnet wird mit %r",
                ziel, wert, unten, oben, gezogen,
            )
            schwellen[ziel] = type(DEFAULTS[ziel])(gezogen)
    return schwellen


def _ist_im_fenster(wert: Any, seit: datetime) -> bool:
    """Zeitstempel aus JSON (Text) oder Datenbank (datetime) gegen das Fenster.

    Ein Zeitstempel ohne Zone wird als UTC gelesen; die Alternative waere, ihn zu
    verwerfen, und dann faellt ein ganzer Meetingraum still aus der Zaehlung.
    """
    if not wert:
        return False
    if isinstance(wert, str):
        try:
            wert = datetime.fromisoformat(wert.replace("Z", "+00:00"))
        except ValueError:
            return False
    if not isinstance(wert, datetime):
        return False
    if wert.tzinfo is None:
        wert = wert.replace(tzinfo=timezone.utc)
    return wert >= seit


async def lade_raeume(db, seit: datetime) -> list:
    """Alle im Fenster veraenderten Meetingraeume — EINMAL je Durchlauf.

    Auf ``agent_ids`` kann nicht in SQL gefiltert werden (JSON-Liste), die
    Schnittmenge entsteht in Python. Darum wird diese Abfrage NICHT je Gruppe
    wiederholt: sie liest die vollstaendigen Nachrichtenverlaeufe mit, und bei
    zwanzig Gruppen war das zwanzigmal dieselbe grosse Lesung.
    """
    from app.models.meeting_room import MeetingRoom

    return list((await db.execute(
        select(MeetingRoom).where(MeetingRoom.updated_at >= seit)
    )).scalars().all())


async def erhebe_bilanz(
    db,
    agent_ids: set[str],
    namen: dict[str, str],
    fenster_stunden: int,
    jetzt: datetime | None = None,
    raeume: list | None = None,
) -> Bilanz:
    """Zaehlt Koordination und Fortschritt fuer genau diese Agenten.

    ``agent_ids`` ist immer eine EXPLIZITE Menge — nie ``None``. Die
    Sichtbarkeitsentscheidung faellt beim Aufrufer (``visible_agent_ids``);
    dieser Dienst bekommt nur noch die bereits gefilterte Menge, damit er sie
    nicht versehentlich aufweiten kann.

    ``raeume`` kann vorgeladen uebergeben werden (siehe :func:`lade_raeume`);
    fehlt es, laedt diese Funktion selbst.
    """
    from app.models.agent_message import AgentMessage
    from app.models.agent_todo import AgentTodo
    from app.models.command_approval import CommandApproval
    from app.models.task import Task, TaskStatus
    from app.models.task_rating import TaskRating

    jetzt = jetzt or datetime.now(timezone.utc)
    seit = jetzt - timedelta(hours=fenster_stunden)
    bilanz = Bilanz(fenster_stunden=fenster_stunden, seit=seit)
    if not agent_ids:
        return bilanz

    anteile: dict[str, AgentAnteil] = {
        aid: AgentAnteil(agent_id=aid, agent_name=namen.get(aid, aid)) for aid in agent_ids
    }

    def buche(agent_id: str | None, art: str, n: int = 1) -> None:
        bilanz.koordination += n
        bilanz.arten[art] = bilanz.arten.get(art, 0) + n
        if agent_id in anteile:
            anteile[agent_id].koordination += n
            anteile[agent_id].arten[art] = anteile[agent_id].arten.get(art, 0) + n

    def beispiel(art: str, text: str, agent_id: str | None = None) -> None:
        if len(bilanz.beispiele) >= MAX_BEISPIELE:
            return
        bilanz.beispiele.append({
            "art": art,
            "agent": namen.get(agent_id or "", agent_id or ""),
            "text": (text or "").strip().replace("\n", " ")[:160],
        })

    # --- Koordination: Agentennachrichten (der Sender zaehlt) ---
    nachrichten = (await db.execute(
        select(AgentMessage)
        .where(AgentMessage.from_agent_id.in_(agent_ids))
        .where(AgentMessage.timestamp >= seit)
    )).scalars().all()
    for m in nachrichten:
        art = f"nachricht:{m.message_type or 'message'}"
        buche(m.from_agent_id, art)
        if (m.message_type or "") in ("question", "handoff"):
            beispiel(art, m.text, m.from_agent_id)

    # --- Koordination: Meeting-Beitraege und Folgemeetings ---
    if raeume is None:
        raeume = await lade_raeume(db, seit)
    for raum in raeume:
        # ``agent_ids`` des Raums ist eine JSON-Liste — die Schnittmenge muss in
        # Python gebildet werden, ein SQL-Filter darauf ist nicht portabel.
        beteiligt = set(raum.agent_ids or []) & agent_ids
        if not beteiligt:
            continue
        for eintrag in raum.messages or []:
            if not isinstance(eintrag, dict):
                continue
            aid = eintrag.get("agent_id")
            if aid in beteiligt and _ist_im_fenster(eintrag.get("timestamp"), seit):
                buche(aid, "meeting:beitrag")
        if raum.parent_room_id and _ist_im_fenster(raum.created_at, seit):
            # Ein Folgemeeting ist das Kernsymptom dieser Fehlerklasse: ein
            # Meeting, das aus einem Meeting entstand.
            for aid in sorted(beteiligt):
                buche(aid, "meeting:folgemeeting")
            beispiel("meeting:folgemeeting", raum.name or raum.id, sorted(beteiligt)[0])

    # --- Koordination: Todos ---
    todos = (await db.execute(
        select(AgentTodo)
        .where(AgentTodo.agent_id.in_(agent_ids))
        .where(AgentTodo.created_at >= seit)
    )).scalars().all()
    for t in todos:
        buche(t.agent_id, "todo:angelegt")

    # --- Koordination: Freigabe-Anfragen ---
    freigaben = (await db.execute(
        select(CommandApproval)
        .where(CommandApproval.agent_id.in_(agent_ids))
        .where(CommandApproval.created_at >= seit)
    )).scalars().all()
    for f in freigaben:
        buche(f.agent_id, "freigabe:angefragt")

    # --- Auftraege: im Fenster angelegt (Koordination) ---
    # Nur die drei gebrauchten Spalten: ``select(Task)`` zieht ``prompt`` und
    # ``result`` als Volltext mit, und das zweimal je Gruppe.
    angelegt = (await db.execute(
        select(Task.id, Task.agent_id, Task.title, Task.metadata_)
        .where(Task.agent_id.in_(agent_ids))
        .where(Task.created_at >= seit)
    )).all()
    for t_id, t_agent, t_titel, t_meta in angelegt:
        meta = t_meta or {}
        if meta.get("heal_attempt"):
            # Wiederholung der Selbstheilung: derselbe Auftrag ein zweites Mal.
            buche(t_agent, "auftrag:wiederholung")
            beispiel("auftrag:wiederholung", t_titel or t_id, t_agent)
        elif meta.get("created_by_agent"):
            # Von einem Agenten fuer einen Agenten angelegt — Delegation.
            # Vom Nutzer angelegte Auftraege sind KEINE Koordination der Gruppe.
            buche(t_agent, "auftrag:delegiert")

    # --- Fortschritt: abgeschlossene Auftraege mit Ergebnis ---
    # Die Ergebnislaenge rechnet die Datenbank; den Text braucht hier niemand.
    fertig = (await db.execute(
        select(Task.id, Task.agent_id, func.length(func.trim(Task.result)))
        .where(Task.agent_id.in_(agent_ids))
        .where(Task.completed_at >= seit)
        .where(Task.status == TaskStatus.COMPLETED)
    )).all()
    # Die Bewertungen werden ueber die AGENTEN geholt, nicht ueber die
    # Auftragskennungen: ``task_id.in_(fertig_ids)`` band einen Parameter je
    # abgeschlossenem Auftrag, und jenseits von ~32.000 Parametern bricht der
    # Postgres-Treiber hart ab. Ueber die Agenten ist die Zahl der Parameter
    # durch die Gruppengroesse begrenzt.
    nicht_erfuellt = {
        tid for (tid,) in (await db.execute(
            select(TaskRating.task_id)
            .where(TaskRating.agent_id.in_(agent_ids))
            .where(TaskRating.fulfilled.is_(False))
        )).all()
    }
    for t_id, t_agent, laenge in fertig:
        if t_id in nicht_erfuellt:
            continue
        if (laenge or 0) < MIN_ERGEBNIS_ZEICHEN:
            continue
        bilanz.fortschritt += 1
        if t_agent in anteile:
            anteile[t_agent].fortschritt += 1

    bilanz.je_agent = [a for a in anteile.values() if a.koordination or a.fortschritt]
    return bilanz


def bewerte(bilanz: Bilanz, schwellen: dict[str, Any]) -> tuple[bool, str]:
    """Warnung ja/nein plus eine Begruendung in Nutzersprache."""
    min_koordination = int(schwellen["min_koordination"])
    verhaeltnis_schwelle = float(schwellen["verhaeltnis"])
    verhaeltnis = bilanz.verhaeltnis

    if bilanz.koordination < min_koordination:
        return False, (
            f"{bilanz.koordination} Koordinationsereignisse in {bilanz.fenster_stunden} h — "
            f"unter der Untergrenze von {min_koordination}. Ruhig."
        )
    if verhaeltnis < verhaeltnis_schwelle:
        return False, (
            f"{bilanz.koordination} Koordinationsereignisse auf {bilanz.fortschritt} "
            f"gelieferte Ergebnisse ({verhaeltnis:.1f} je Ergebnis, Schwelle "
            f"{verhaeltnis_schwelle:g}). Im Rahmen."
        )
    return True, (
        f"{bilanz.koordination} Koordinationsereignisse in {bilanz.fenster_stunden} h, "
        f"aber nur {bilanz.fortschritt} gelieferte Ergebnisse — {verhaeltnis:.1f} "
        f"Koordinationsschritte je Ergebnis (Schwelle {verhaeltnis_schwelle:g})."
    )


def _abdruck(user_id: str) -> str:
    """Stabiler, nicht rueckrechenbarer Kurzabdruck einer Nutzerkennung."""
    return hashlib.sha256(user_id.encode()).hexdigest()[:12]


def _lautester(bilanz: Bilanz, mitglieder: set[str]) -> str:
    """Der Agent mit der meisten Koordination — er ist der Anlass der Meldung.

    Vorher war es der alphabetisch erste; das traf regelmaessig einen Agenten,
    der mit der Sache nichts zu tun hatte, und wechselte, sobald ein neuer
    Agent mit kleinerer Kennung dazukam.
    """
    kandidaten = [a for a in bilanz.je_agent if a.agent_id in mitglieder]
    if kandidaten:
        return max(kandidaten, key=lambda a: (a.koordination, a.agent_id)).agent_id
    return min(mitglieder)


async def befunde_fuer(
    db,
    agent_ids: set[str] | None,
    schwellen: dict[str, Any] | None = None,
    jetzt: datetime | None = None,
) -> list[Befund]:
    """Ein Befund je Gruppe — Teams zuerst, dann Agenten ohne Team je Nutzer.

    ``agent_ids=None`` heisst Administrator (kein Filter). Eine LEERE Menge
    heisst „nichts" und liefert eine leere Liste — nie „alles".
    """
    from app.models.agent import Agent
    from app.models.team import Team

    schwellen = schwellen or await lade_schwellen(db)
    fenster = int(schwellen["fenster_stunden"])

    agenten_stmt = select(Agent)
    if agent_ids is not None:
        agenten_stmt = agenten_stmt.where(Agent.id.in_(agent_ids))
    agenten = list((await db.execute(agenten_stmt)).scalars().all())
    if not agenten:
        return []
    sichtbar = {a.id for a in agenten}
    namen = {a.id: a.name for a in agenten}

    teams = list((await db.execute(
        select(Team).where(Team.is_active.is_(True))
    )).scalars().all())

    befunde: list[Befund] = []
    schon_erfasst: set[str] = set()

    # Die Meetingraeume EINMAL fuer alle Gruppen, nicht je Gruppe erneut.
    seit = (jetzt or datetime.now(timezone.utc)) - timedelta(hours=fenster)
    raeume = await lade_raeume(db, seit)

    for team in teams:
        # Nur die Mitglieder, die der Aufrufer sehen darf. Ein Team kann Agenten
        # enthalten, die zu einem anderen Nutzer gehoeren — deren Namen und
        # Zahlen gehoeren nicht in diese Antwort.
        mitglieder = {str(m) for m in (team.member_agent_ids or [])} & sichtbar
        if team.lead_agent_id and team.lead_agent_id in sichtbar:
            mitglieder.add(team.lead_agent_id)
        # In JEDEM Fall als erfasst vermerken, auch wenn die Gruppe gleich
        # uebersprungen wird: ein Agent MIT Team darf nicht zusaetzlich in der
        # Gruppe „Agenten ohne Team" seines Nutzers auftauchen.
        schon_erfasst |= mitglieder
        if len(mitglieder) < 2:
            # Eine Gruppe aus einem Agenten hat keine Koordination im Sinne
            # dieser Kennzahl; ihre Nachrichten gehen an niemanden im Team.
            continue
        bilanz = await erhebe_bilanz(db, mitglieder, namen, fenster, jetzt, raeume=raeume)
        warnung, grund = bewerte(bilanz, schwellen)
        melde_agent = (
            team.lead_agent_id if team.lead_agent_id in mitglieder
            else _lautester(bilanz, mitglieder)
        )
        befunde.append(Befund(
            gruppe_id=team.id, gruppe_name=team.name, art="team",
            bilanz=bilanz, warnung=warnung, begruendung=grund,
            melde_agent_id=melde_agent, mitglieder=frozenset(mitglieder),
        ))

    # Agenten ohne Team: je Nutzer eine Gruppe. Ohne diesen Zweig blieben genau
    # die haeufigsten Anlagen unbeobachtet — mehrere Agenten, kein Team.
    ohne_team: dict[str, set[str]] = {}
    for a in agenten:
        if a.id in schon_erfasst:
            continue
        ohne_team.setdefault(str(a.user_id or "ohne-nutzer"), set()).add(a.id)
    for user_id, mitglieder in sorted(ohne_team.items()):
        if len(mitglieder) < 2:
            continue
        bilanz = await erhebe_bilanz(db, mitglieder, namen, fenster, jetzt, raeume=raeume)
        warnung, grund = bewerte(bilanz, schwellen)
        befunde.append(Befund(
            # Die Nutzerkennung wird NICHT durchgereicht: wem ein Agent nur
            # freigegeben wurde, muss nicht erfahren, wem er gehoert. Der
            # Abdruck ist stabil, damit die Meldepause die Gruppe wiedererkennt.
            gruppe_id=f"nutzer:{_abdruck(user_id)}", gruppe_name="Agenten ohne Team",
            art="nutzer", bilanz=bilanz, warnung=warnung, begruendung=grund,
            melde_agent_id=_lautester(bilanz, mitglieder),
            mitglieder=frozenset(mitglieder),
        ))

    return befunde


class ProduktivitaetsMonitor:
    """Hintergrundwaechter: warnt, wenn Koordination den Fortschritt ueberwaechst.

    Empfaenger ist der Team-Lead; hat die Gruppe keinen (Agenten ohne Team),
    der Agent mit der meisten Koordination — er ist der Anlass der Meldung. Die
    Benachrichtigung haengt bewusst an einem AGENTEN: die Benachrichtigungen
    werden genau den Nutzern gezeigt, die diesen Agenten besitzen oder
    freigeschaltet bekamen (``api/notifications.py``, ``api/ws.py``).

    Auf Wunsch kommt ein Analyse-Auftrag fuer genau diesen Agenten dazu — nicht
    fuer einen beliebigen, der eine Analyse ohne Kontext eher verschlimmert.

    **Nur eine Instanz sollte melden.** Ein Fuehrungsschloss gibt es hier nicht;
    bei mehreren Repliken schuetzt allein die Meldepause (sie liegt in der
    Datenbank), und zwei gleichzeitig startende Repliken koennten dieselbe
    Meldung doppelt schreiben. Fuer den Betrieb mit Repliken siehe
    ``docs/HIGH_AVAILABILITY.md``.
    """

    def __init__(self, session_factory, redis=None, docker_service=None) -> None:
        self._sf = session_factory
        self._redis = redis
        self.docker = docker_service
        self._running = True

    def stop(self) -> None:
        self._running = False

    async def run(self) -> None:
        logger.info("[Produktivitaet] Waechter gestartet (alle %d s)", _PRUEF_INTERVALL_SEKUNDEN)
        # Erst nach dem Hochlauf messen: direkt beim Start sind Caches leer und
        # die Datenbank beschaeftigt.
        await asyncio.sleep(180)
        while self._running:
            try:
                await self.einmal_pruefen()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.error("[Produktivitaet] Durchlauf gescheitert", exc_info=True)
            await asyncio.sleep(_PRUEF_INTERVALL_SEKUNDEN)

    async def einmal_pruefen(self) -> list[Befund]:
        """Ein Durchlauf ueber alle Gruppen. Gibt die gemeldeten Befunde zurueck."""
        from app.db.session import resilient_session

        gemeldet: list[Befund] = []
        async with resilient_session(session_factory=self._sf) as db:
            schwellen = await lade_schwellen(db)
            # Der Waechter laeuft ohne Nutzer, also ueber alle Agenten (None).
            # Die Zuordnung zu Nutzern passiert ueber die Gruppen.
            befunde = await befunde_fuer(db, None, schwellen)
            schon_benachrichtigt: set[str] = set()
            for befund in befunde:
                if not befund.warnung or not befund.melde_agent_id:
                    continue
                # Ein Agent kann in mehreren Teams sitzen. Zwei Meldungen an
                # dieselbe Stelle im selben Durchlauf sind fuer den Leser
                # dieselbe Sache zweimal — die zweite bleibt in der Bilanz auf
                # der Health-Seite sichtbar, aber sie klingelt nicht erneut.
                if befund.melde_agent_id in schon_benachrichtigt:
                    logger.info(
                        "[Produktivitaet] %s warnt ebenfalls, Meldung an %s entfaellt "
                        "(in diesem Durchlauf schon benachrichtigt)",
                        befund.gruppe_id, befund.melde_agent_id,
                    )
                    continue
                if await self._pause_aktiv(db, befund, schwellen):
                    continue
                await self._melden(db, befund, schwellen)
                schon_benachrichtigt.add(befund.melde_agent_id)
                gemeldet.append(befund)
            await db.commit()
        return gemeldet

    async def _pause_aktiv(self, db, befund: Befund, schwellen: dict[str, Any]) -> bool:
        """Wurde diese Gruppe innerhalb der Meldepause schon gemeldet?"""
        from app.models.notification import Notification

        pause = int(schwellen["melde_pause_stunden"])
        if pause <= 0:
            # Ausdruecklich erlaubt und dokumentiert: 0 heisst „keine Pause".
            return False
        seit = datetime.now(timezone.utc) - timedelta(hours=pause)
        # Gesucht wird bei ALLEN Mitgliedern, nicht nur beim heutigen
        # Empfaenger: wechselt der Lead oder kommt ein Agent dazu, der in der
        # Auswahl vorrueckt, waere die Vormeldung sonst unsichtbar und derselbe
        # Zustand wuerde ein zweites Mal gemeldet.
        empfaenger = set(befund.mitglieder) | {befund.melde_agent_id}
        letzte = (await db.execute(
            select(Notification)
            .where(Notification.agent_id.in_(empfaenger))
            .where(Notification.created_at >= seit)
            .order_by(Notification.created_at.desc())
        )).scalars().all()
        for n in letzte:
            meta = n.meta or {}
            if meta.get("type") == MELDUNG_ART and meta.get("gruppe_id") == befund.gruppe_id:
                return True
        return False

    async def _melden(self, db, befund: Befund, schwellen: dict[str, Any]) -> None:
        from app.models.notification import Notification

        titel = f"{befund.gruppe_name}: viel Koordination, wenig Fortschritt"
        oben = ", ".join(
            f"{art} {n}x" for art, n in sorted(befund.bilanz.arten.items(), key=lambda x: -x[1])[:3]
        )
        nachricht = f"{befund.begruendung} Haeufigste Arten: {oben}." if oben else befund.begruendung
        db.add(Notification(
            agent_id=befund.melde_agent_id,
            type="warning",
            title=titel[:200],
            message=nachricht[:240],
            priority="normal",
            action_url="/health",
            meta={
                "type": MELDUNG_ART,
                "gruppe_id": befund.gruppe_id,
                "gruppe_name": befund.gruppe_name,
                "koordination": befund.bilanz.koordination,
                "fortschritt": befund.bilanz.fortschritt,
                "verhaeltnis": round(befund.bilanz.verhaeltnis, 2),
                "fenster_stunden": befund.bilanz.fenster_stunden,
            },
        ))
        logger.warning(
            "[Produktivitaet] %s: %d Koordination / %d Fortschritt (%.1f je Ergebnis)",
            befund.gruppe_id, befund.bilanz.koordination, befund.bilanz.fortschritt,
            befund.bilanz.verhaeltnis,
        )
        if schwellen.get("analyse_auftrag") and befund.art == "team":
            await self._analyse_auftrag(db, befund)

    async def _analyse_auftrag(self, db, befund: Befund) -> None:
        """Analyse-Auftrag an den Team-Lead — nie an einen beliebigen Agenten.

        Der Auftrag wird eingereiht wie jeder andere: ``QUEUED`` plus
        ``push_task`` (und ein Wecken, falls der Agent steht). Ein blosser
        Datenbankeintrag mit ``PENDING`` wird von NIEMANDEM abgeholt — fuer
        diesen Zustand gibt es im Haus keinen Aufraeumpfad, und ueber
        ``Task.team_id`` haette er dauerhaft als laufende Arbeit des Teams auf
        dem Projektbrett gestanden. Laesst sich nicht einreihen, wird der
        Eintrag wieder entfernt: ein Auftrag, der nie laeuft, ist schlimmer als
        keiner, weil er Arbeit vortaeuscht.
        """
        import json
        import uuid

        from app.models.task import Task, TaskStatus

        arten = ", ".join(f"{art}: {n}" for art, n in sorted(befund.bilanz.arten.items()))
        prompt = (
            f"Produktivitaetsmonitor: In den letzten {befund.bilanz.fenster_stunden} Stunden "
            f"stehen in deinem Team {befund.bilanz.koordination} Koordinationsereignisse "
            f"gegen {befund.bilanz.fortschritt} gelieferte Ergebnisse "
            f"({befund.bilanz.verhaeltnis:.1f} Koordinationsschritte je Ergebnis).\n\n"
            f"Aufschluesselung: {arten}\n\n"
            "Pruefe, welche dieser Schritte Arbeit ERSETZT statt sie vorzubereiten "
            "(Rueckfragen zu bereits Beantwortetem, Folgemeetings, Pruefungen von "
            "Pruefungen, wiederholte Auftraege) und benenne die drei, die du als "
            "naechstes streichen oder zusammenlegen wuerdest. Keine neuen Meetings "
            "ansetzen, um das zu klaeren."
        )
        if self._redis is None:
            logger.warning(
                "[Produktivitaet] Analyse-Auftrag fuer %s entfaellt: keine Warteschlange "
                "verfuegbar. Ein Eintrag ohne Einreihung wuerde nie laufen.",
                befund.gruppe_id,
            )
            return

        auftrag_id = f"t{uuid.uuid4().hex[:9]}"
        titel = f"Koordinationsbilanz pruefen: {befund.gruppe_name}"[:200]
        auftrag = Task(
            id=auftrag_id,
            title=titel,
            prompt=prompt,
            status=TaskStatus.QUEUED,
            agent_id=befund.melde_agent_id,
            team_id=befund.gruppe_id if befund.art == "team" else None,
            metadata_={"source": "produktivitaet_monitor", "gruppe_id": befund.gruppe_id},
        )
        db.add(auftrag)
        await db.flush()

        if self.docker is not None:
            try:
                from app.services.user_lifecycle import wake_agent
                await wake_agent(db, self.docker, befund.melde_agent_id)
            except Exception:
                logger.warning(
                    "[Produktivitaet] %s liess sich nicht wecken — der Auftrag bleibt "
                    "in der Warteschlange", befund.melde_agent_id, exc_info=True,
                )
        try:
            await self._redis.push_task(befund.melde_agent_id, json.dumps({
                "id": auftrag_id,
                "prompt": prompt,
                "title": titel,
                "model": None,
                "priority": "low",
            }))
        except Exception:
            logger.warning(
                "[Produktivitaet] Analyse-Auftrag %s liess sich nicht einreihen — "
                "Eintrag wird verworfen", auftrag_id, exc_info=True,
            )
            await db.delete(auftrag)
