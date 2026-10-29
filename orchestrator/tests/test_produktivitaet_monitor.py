"""Produktivitaetsmonitor (#882): Koordination gegen Fortschritt je Gruppe.

Gegen ECHTES SQL (in-memory SQLite), nicht gegen einen Mock — die
Sichtbarkeits- und Fensterfilterung passiert in der Abfrage; ein Stub wuerde
genau den Schutz wegtesten, der hier geprueft werden soll (gleiches Muster wie
``test_activity_timeline.py``).

Die Trennschaerfe der Vorgabewerte wird hier GEMESSEN und nicht behauptet:
``test_trennschaerfe_der_vorgaben`` vergleicht die RAENDER beider Klassen
(max der ruhigen gegen min der auffaelligen), nicht ihre Mittelwerte.
Ueberlappen die Raender, trennt keine Schwelle — dann waere nicht die Zahl
falsch, sondern die Messgroesse.
"""

import unittest
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.agent import Agent
from app.models.agent_access import AgentAccess
from app.models.agent_message import AgentMessage
from app.models.agent_todo import AgentTodo
from app.models.audit_log import AuditLog
from app.models.command_approval import CommandApproval
from app.models.meeting_room import MeetingRoom
from app.models.notification import Notification
from app.models.platform_settings import PlatformSettings
from app.models.task import Task, TaskStatus
from app.models.task_rating import TaskRating
from app.models.team import Team
from app.models.user import UserRole
from app.services import produktivitaet_service as ps

#: Bezugszeit der Testdaten. Bewusst die ECHTE Jetzt-Zeit und kein fester
#: Zeitpunkt: die Dienstfunktionen bekommen ``jetzt`` ausdruecklich, der
#: Endpunkt dagegen rechnet mit ``datetime.now`` — gegen ein fest
#: eingetragenes Datum lagen die Testdaten in der Zukunft und jedes Fenster
#: traf, egal wie klein es war.
JETZT = datetime.now(timezone.utc)
ERGEBNIS = "x" * (ps.MIN_ERGEBNIS_ZEICHEN + 10)

TABELLEN = [
    Agent.__table__, AgentAccess.__table__, AgentMessage.__table__, AgentTodo.__table__,
    AuditLog.__table__, CommandApproval.__table__, MeetingRoom.__table__,
    Notification.__table__,
    PlatformSettings.__table__, Task.__table__, TaskRating.__table__, Team.__table__,
]


class _Warteschlange:
    """Warteschlangen-Double, das AUCH scheitern kann.

    Ein Double, das immer gelingt, beweist nichts: der interessante Fall ist
    die abgelehnte Einreihung, nach der kein Auftragseintrag zurueckbleiben
    darf.
    """

    def __init__(self, scheitert=False):
        self.scheitert = scheitert
        self.eingereiht: list[tuple[str, dict]] = []

    async def push_task(self, agent_id, payload):
        if self.scheitert:
            raise RuntimeError("Warteschlange nicht erreichbar")
        import json
        self.eingereiht.append((agent_id, json.loads(payload)))


def _agent(agent_id, name, user_id):
    return Agent(id=agent_id, name=name, user_id=user_id, config={})


def _nachricht(von, nach, art="question", wann=None):
    return AgentMessage(
        message_id=uuid.uuid4().hex, from_agent_id=von, from_agent_name=von,
        to_agent_id=nach, text=f"Rueckfrage von {von}", message_type=art,
        timestamp=wann or JETZT - timedelta(hours=1),
    )


def _auftrag(agent_id, status=TaskStatus.COMPLETED, result=ERGEBNIS, meta=None,
            erstellt=None, fertig=None):
    erstellt = erstellt or JETZT - timedelta(hours=2)
    return Task(
        id=f"t{uuid.uuid4().hex[:9]}", title="Arbeit", prompt="p", agent_id=agent_id,
        status=status, result=result, metadata_=meta or {},
        created_at=erstellt,
        completed_at=fertig if fertig is not None else (
            JETZT - timedelta(hours=1) if status == TaskStatus.COMPLETED else None
        ),
    )


def _raum(raum_id, agent_ids, beitraege, parent=None, integriert=False, wann=None):
    wann = wann or JETZT - timedelta(hours=1)
    return MeetingRoom(
        id=raum_id, name=f"Raum {raum_id}", agent_ids=list(agent_ids),
        parent_room_id=parent, deliverable_integrated=integriert,
        messages=[
            {"role": "agent", "agent_id": aid, "content": "beitrag",
             "timestamp": (wann).isoformat(), "round": i}
            for i, aid in enumerate(beitraege)
        ],
        created_at=wann, updated_at=wann,
    )


class Basis(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Agent.metadata.create_all, tables=TABELLEN)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        self.db = self.Session()

    async def asyncTearDown(self):
        await self.db.close()
        await self.engine.dispose()

    async def _anlegen(self, *objekte):
        for o in objekte:
            self.db.add(o)
        await self.db.commit()

    async def _bilanz(self, agent_ids, namen=None, stunden=24):
        return await ps.erhebe_bilanz(
            self.db, set(agent_ids), namen or {a: a for a in agent_ids},
            stunden, jetzt=JETZT,
        )


class Zaehlung(Basis):
    """Was als Koordination und was als Fortschritt zaehlt."""

    async def test_nachrichten_meetings_todos_freigaben_zaehlen_als_koordination(self):
        await self._anlegen(
            _nachricht("a1", "a2"), _nachricht("a1", "a2", "handoff"),
            _nachricht("a2", "a1", "response"),
            _raum("r1", ["a1", "a2"], ["a1", "a2", "a1"]),
            AgentTodo(agent_id="a1", title="noch eine Pruefung",
                      created_at=JETZT - timedelta(hours=3)),
            CommandApproval(agent_id="a2", command="rm", created_at=JETZT - timedelta(hours=3)),
        )
        b = await self._bilanz(["a1", "a2"])
        self.assertEqual(b.koordination, 3 + 3 + 1 + 1)
        self.assertEqual(b.arten["meeting:beitrag"], 3)
        self.assertEqual(b.arten["todo:angelegt"], 1)
        self.assertEqual(b.arten["freigabe:angefragt"], 1)
        self.assertEqual(b.fortschritt, 0)

    async def test_folgemeeting_zaehlt_zusaetzlich(self):
        """Ein Meeting, das aus einem Meeting entstand, ist das Kernsymptom."""
        await self._anlegen(
            _raum("r1", ["a1", "a2"], ["a1"]),
            _raum("r2", ["a1", "a2"], ["a1"], parent="r1"),
        )
        b = await self._bilanz(["a1", "a2"])
        self.assertEqual(b.arten.get("meeting:folgemeeting"), 2, "je beteiligtem Agenten einmal")
        self.assertEqual(b.arten.get("meeting:beitrag"), 2)

    async def test_wiederholung_und_delegation_zaehlen_der_nutzerauftrag_nicht(self):
        await self._anlegen(
            _auftrag("a1", status=TaskStatus.FAILED, result=None, meta={"heal_attempt": 2}),
            _auftrag("a1", status=TaskStatus.PENDING, result=None, meta={"created_by_agent": "a2"}),
            _auftrag("a1", status=TaskStatus.PENDING, result=None, meta={}),
        )
        b = await self._bilanz(["a1", "a2"])
        self.assertEqual(b.arten.get("auftrag:wiederholung"), 1)
        self.assertEqual(b.arten.get("auftrag:delegiert"), 1)
        self.assertEqual(b.koordination, 2, "der vom Nutzer angelegte Auftrag ist keine Koordination")

    async def test_abgebrochener_lauf_zaehlt_nicht_als_fortschritt(self):
        """``completed`` mit leerem Ergebnis ist ein Ausfall, kein Ergebnis.

        Ohne diese Pruefung haette der Monitor ausgerechnet dann geschwiegen,
        wenn die Gruppe nur noch Abbrueche produziert.
        """
        await self._anlegen(
            _auftrag("a1", result=ERGEBNIS),
            _auftrag("a1", result=""),
            _auftrag("a1", result="abgebrochen"),
        )
        b = await self._bilanz(["a1", "a2"])
        self.assertEqual(b.fortschritt, 1)

    async def test_als_nicht_erfuellt_bewerteter_auftrag_zaehlt_nicht(self):
        auftrag = _auftrag("a1", result=ERGEBNIS)
        await self._anlegen(auftrag)
        await self._anlegen(TaskRating(task_id=auftrag.id, agent_id="a1", rating=2, fulfilled=False))
        b = await self._bilanz(["a1", "a2"])
        self.assertEqual(b.fortschritt, 0)

    async def test_uebernommenes_meeting_ergebnis_zaehlt_NICHT(self):
        """``deliverable_integrated`` ist ein Schalter ohne Zeitstempel.

        Ueber ``updated_at`` ausgewaehlt haette eine vor zwei Monaten
        uebernommene Lieferung als heutiger Fortschritt gezaehlt, sobald im
        Raum irgendetwas passiert — und derselbe Raum haette in jeder
        beteiligten Gruppe gezaehlt. Ein zu hoher Fortschritt ist hier
        besonders teuer: er unterdrueckt die Warnung.
        """
        alt = _raum("r_alt", ["a1", "a2"], [], integriert=True,
                    wann=JETZT - timedelta(days=60))
        alt.updated_at = JETZT - timedelta(hours=1)   # heute wieder angefasst
        await self._anlegen(alt)
        b = await self._bilanz(["a1", "a2"])
        self.assertEqual(b.fortschritt, 0)

    async def test_fortschritt_je_agent_summiert_sich_zur_gruppe(self):
        """Sonst zeigt die Aufklappung „0 Ergebnisse" bei jedem Agenten,
        waehrend oben eine Zahl steht."""
        await self._anlegen(
            _auftrag("a1", result=ERGEBNIS), _auftrag("a2", result=ERGEBNIS),
            _raum("r1", ["a1", "a2"], [], integriert=True),
        )
        b = await self._bilanz(["a1", "a2"])
        self.assertEqual(b.fortschritt, sum(a.fortschritt for a in b.je_agent))
        self.assertEqual(b.fortschritt, 2)

    async def test_ergebnis_aus_leerzeichen_zaehlt_nicht(self):
        """Die Laenge wird nach dem Abschneiden von Leerraum gemessen."""
        await self._anlegen(_auftrag("a1", result=" " * (ps.MIN_ERGEBNIS_ZEICHEN + 50)))
        self.assertEqual((await self._bilanz(["a1", "a2"])).fortschritt, 0)

    async def test_ereignisse_vor_dem_fenster_zaehlen_nicht(self):
        alt = JETZT - timedelta(hours=30)
        await self._anlegen(
            _nachricht("a1", "a2", wann=alt),
            _raum("r_alt", ["a1", "a2"], ["a1", "a1"], wann=alt),
            _auftrag("a1", erstellt=alt, fertig=alt, meta={"created_by_agent": "a2"}),
        )
        b = await self._bilanz(["a1", "a2"], stunden=24)
        self.assertEqual(b.koordination, 0)
        self.assertEqual(b.fortschritt, 0)

    async def test_raum_im_fenster_zaehlt_nur_beitraege_im_fenster(self):
        """Ein lange laufender Raum darf nicht seine ganze Vorgeschichte einbringen."""
        raum = _raum("r1", ["a1", "a2"], ["a1"])
        raum.messages = list(raum.messages) + [{
            "role": "agent", "agent_id": "a2", "content": "alt",
            "timestamp": (JETZT - timedelta(hours=40)).isoformat(),
        }]
        await self._anlegen(raum)
        b = await self._bilanz(["a1", "a2"])
        self.assertEqual(b.arten.get("meeting:beitrag"), 1)


class Bewertung(unittest.TestCase):
    """Die Schwellenlogik, ohne Datenbank."""

    def _bilanz(self, koordination, fortschritt):
        b = ps.Bilanz(fenster_stunden=24, seit=JETZT - timedelta(hours=24))
        b.koordination = koordination
        b.fortschritt = fortschritt
        return b

    def test_ruhiger_tag_alarmiert_nicht(self):
        warnung, grund = ps.bewerte(self._bilanz(19, 0), ps.DEFAULTS)
        self.assertFalse(warnung)
        self.assertIn("Untergrenze", grund)

    def test_viel_koordination_ohne_abschluss_warnt(self):
        warnung, grund = ps.bewerte(self._bilanz(40, 0), ps.DEFAULTS)
        self.assertTrue(warnung)
        self.assertIn("40", grund)

    def test_echter_fortschritt_unterdrueckt_den_alarm(self):
        """Dieselbe Koordinationsmenge, aber Ergebnisse — kein Alarm."""
        self.assertFalse(ps.bewerte(self._bilanz(100, 13), ps.DEFAULTS)[0])
        self.assertTrue(ps.bewerte(self._bilanz(100, 12), ps.DEFAULTS)[0])

    def test_mittleres_verhaeltnis_bleibt_still(self):
        """Viel Abstimmung UND viel geliefert: kein Alarm.

        Trennt „Schwelle fehlt" von „Verhaeltnis gerundet": ein Verhaeltnis von
        5,0 bleibt auch gerundet unter 8, schlaegt aber sofort an, wenn die
        Schwellenpruefung ganz entfaellt. Ohne diesen Fall haetten beide Fehler
        dieselben Tests rot gemacht und waeren nicht unterscheidbar gewesen.
        """
        warnung, grund = ps.bewerte(self._bilanz(50, 10), ps.DEFAULTS)
        self.assertFalse(warnung)
        self.assertIn("Im Rahmen", grund)

    def test_verhaeltnis_wird_nicht_gerundet(self):
        """7,99 liegt unter 8 und darf nicht hochgerundet werden."""
        b = self._bilanz(799, 100)
        self.assertAlmostEqual(b.verhaeltnis, 7.99)
        self.assertFalse(ps.bewerte(b, ps.DEFAULTS)[0])

    def test_trennschaerfe_der_vorgaben(self):
        """Die RAENDER beider Klassen, nicht ihre Mittelwerte.

        Ruhige Lagen (echte Arbeit, auch mit viel Abstimmung) gegen auffaellige
        (Koordination ohne Ergebnis). Ueberlappen die Verhaeltnisse, trennt
        keine Schwelle und es fehlte eine Dimension, keine bessere Zahl.
        """
        ruhig = [(19, 0), (25, 5), (40, 8), (100, 13), (60, 10), (200, 30)]
        auffaellig = [(20, 0), (40, 0), (30, 1), (60, 2), (120, 5), (200, 10)]

        r = [self._bilanz(k, f).verhaeltnis for k, f in ruhig if k >= ps.DEFAULTS["min_koordination"]]
        a = [self._bilanz(k, f).verhaeltnis for k, f in auffaellig]
        self.assertLess(max(r), min(a), f"Raender ueberlappen: ruhig<={max(r)}, auffaellig>={min(a)}")
        self.assertLessEqual(max(r), ps.DEFAULTS["verhaeltnis"])
        self.assertGreaterEqual(min(a), ps.DEFAULTS["verhaeltnis"])
        # Und keine der ruhigen Lagen loest aus, keine der auffaelligen schweigt.
        self.assertEqual([ps.bewerte(self._bilanz(k, f), ps.DEFAULTS)[0] for k, f in ruhig],
                         [False] * len(ruhig))
        self.assertEqual([ps.bewerte(self._bilanz(k, f), ps.DEFAULTS)[0] for k, f in auffaellig],
                         [True] * len(auffaellig))


class SynthetischeLage(Basis):
    """Abnahmekriterium: viele Nachrichten/Meetings ohne Abschluss -> Warnung,
    normale Arbeit -> keine Warnung. Beide Lagen aus echten Datensaetzen."""

    async def _lage_warnt(self, nachrichten, meeting_runden, abschluesse):
        await self._anlegen(
            _agent("a1", "Eins", "u1"), _agent("a2", "Zwei", "u1"),
            Team(id="team1", name="Team", member_agent_ids=["a1", "a2"], lead_agent_id="a1"),
        )
        for i in range(nachrichten):
            self.db.add(_nachricht("a1" if i % 2 else "a2", "a2" if i % 2 else "a1"))
        if meeting_runden:
            self.db.add(_raum("r1", ["a1", "a2"], ["a1", "a2"] * (meeting_runden // 2 or 1)))
        for _ in range(abschluesse):
            self.db.add(_auftrag("a1", result=ERGEBNIS))
        await self.db.commit()
        befunde = await ps.befunde_fuer(self.db, None, jetzt=JETZT)
        self.assertEqual(len(befunde), 1, "ein Befund fuer das Team")
        return befunde[0]

    async def test_buerokratie_warnt(self):
        befund = await self._lage_warnt(nachrichten=30, meeting_runden=8, abschluesse=0)
        self.assertTrue(befund.warnung, befund.begruendung)
        self.assertEqual(befund.gruppe_name, "Team")
        self.assertGreaterEqual(befund.bilanz.koordination, 30)
        self.assertEqual(befund.bilanz.fortschritt, 0)
        # Aufschluesselung und Beispiele fuer den Drill-down sind da.
        self.assertIn("nachricht:question", befund.bilanz.arten)
        self.assertTrue(befund.bilanz.beispiele)
        self.assertEqual({a.agent_id for a in befund.bilanz.je_agent}, {"a1", "a2"})

    async def test_normale_arbeit_warnt_nicht(self):
        befund = await self._lage_warnt(nachrichten=12, meeting_runden=4, abschluesse=6)
        self.assertFalse(befund.warnung, befund.begruendung)
        self.assertEqual(befund.bilanz.fortschritt, 6)


class Sichtbarkeit(Basis):
    """„Nutzer B sieht die Kennzahlen von Nutzer A nicht."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self._anlegen(
            _agent("a1", "A-Eins", "ua"), _agent("a2", "A-Zwei", "ua"),
            _agent("b1", "B-Eins", "ub"), _agent("b2", "B-Zwei", "ub"),
            Team(id="ta", name="Team A", member_agent_ids=["a1", "a2"], lead_agent_id="a1"),
            Team(id="tb", name="Team B", member_agent_ids=["b1", "b2"], lead_agent_id="b1"),
        )
        for i in range(30):
            self.db.add(_nachricht("a1", "a2") if i % 2 else _nachricht("a2", "a1"))
        for i in range(30):
            self.db.add(_nachricht("b1", "b2") if i % 2 else _nachricht("b2", "b1"))
        await self.db.commit()

    def _namen(self, befunde):
        """Welche Agentennamen die Antwort ueberhaupt nennt.

        ``je_agent`` fuehrt nur Agenten mit Aktivitaet — ein Mitglied ohne
        Ereignis im Fenster taucht zu Recht nicht auf. Fuer die Leckpruefung
        zaehlt, dass KEIN fremder Name darin steht.
        """
        return {n["agent_name"] for b in befunde for n in b.zu_dict()["je_agent"]}

    async def test_nutzer_b_sieht_a_nicht(self):
        befunde = await ps.befunde_fuer(self.db, {"b1", "b2"}, jetzt=JETZT)
        self.assertEqual([b.gruppe_name for b in befunde], ["Team B"])
        self.assertEqual(self._namen(befunde), {"B-Eins", "B-Zwei"})
        self.assertNotIn("A-Eins", str(befunde[0].zu_dict()))
        self.assertEqual(befunde[0].bilanz.koordination, 30, "nicht die 60 beider Teams")

    async def test_leere_menge_heisst_nichts_nicht_alles(self):
        self.assertEqual(await ps.befunde_fuer(self.db, set(), jetzt=JETZT), [])

    async def test_administrator_sieht_alle(self):
        befunde = await ps.befunde_fuer(self.db, None, jetzt=JETZT)
        self.assertEqual(sorted(b.gruppe_name for b in befunde), ["Team A", "Team B"])

    async def test_fremder_agent_im_eigenen_team_bleibt_verborgen(self):
        """Ein Team kann einen Agenten enthalten, den der Aufrufer nicht sehen darf.

        Dessen Name und Zahlen gehoeren nicht in die Antwort — sonst waere das
        Team der Umweg um die Sichtbarkeitspruefung.
        """
        team = await self.db.get(Team, "tb")
        team.member_agent_ids = ["b1", "b2", "a1"]
        await self.db.commit()
        befunde = await ps.befunde_fuer(self.db, {"b1", "b2"}, jetzt=JETZT)
        self.assertEqual(self._namen(befunde), {"B-Eins", "B-Zwei"})
        self.assertEqual(befunde[0].bilanz.koordination, 30)

    async def test_agenten_ohne_team_werden_je_nutzer_gruppiert(self):
        for t in ("ta", "tb"):
            await self.db.delete(await self.db.get(Team, t))
        await self.db.commit()
        befunde = await ps.befunde_fuer(self.db, {"b1", "b2"}, jetzt=JETZT)
        self.assertEqual([b.art for b in befunde], ["nutzer"])
        # Die Nutzerkennung selbst steht NICHT in der Antwort: wem ein Agent
        # nur freigegeben wurde, muss nicht erfahren, wem er gehoert.
        self.assertNotIn("ub", befunde[0].gruppe_id)
        self.assertTrue(befunde[0].gruppe_id.startswith("nutzer:"))
        # Stabil, sonst erkennt die Meldepause die Gruppe nicht wieder.
        wieder = await ps.befunde_fuer(self.db, {"b1", "b2"}, jetzt=JETZT)
        self.assertEqual(befunde[0].gruppe_id, wieder[0].gruppe_id)
        self.assertEqual(self._namen(befunde), {"B-Eins", "B-Zwei"})

    async def test_einzelner_agent_ist_keine_gruppe(self):
        """Ein Agent allein hat keine Koordination im Sinne dieser Kennzahl."""
        team = await self.db.get(Team, "tb")
        team.member_agent_ids = ["b1"]
        team.lead_agent_id = "b1"
        await self.db.commit()
        befunde = await ps.befunde_fuer(self.db, {"b1"}, jetzt=JETZT)
        self.assertEqual(befunde, [])


class RaumMandanten(Basis):
    """Meetingraeume sind die EINZIGE Stelle, an der der Mandantenschutz in
    Python passiert (``agent_ids`` ist eine JSON-Liste, SQL kann nicht
    filtern) — also die schwaechste. Hier sitzt darum ausdruecklich ein NICHT
    sichtbarer Agent im Raum.
    """

    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self._anlegen(
            _agent("b1", "B-Eins", "ub"), _agent("b2", "B-Zwei", "ub"),
            _agent("a1", "A-Eins", "ua"),
        )

    async def test_beitraege_fremder_agenten_zaehlen_nicht_mit(self):
        # Ein Raum mit drei Beteiligten, zwei Beitraege davon vom fremden a1.
        await self._anlegen(_raum("r1", ["b1", "b2", "a1"], ["b1", "a1", "a1"]))
        b = await self._bilanz(["b1", "b2"], namen={"b1": "B-Eins", "b2": "B-Zwei"})
        self.assertEqual(b.arten.get("meeting:beitrag"), 1, "nur der Beitrag von b1")
        self.assertNotIn("a1", {a.agent_id for a in b.je_agent})

    async def test_folgemeeting_zaehlt_nur_fuer_sichtbare_beteiligte(self):
        await self._anlegen(
            _raum("r1", ["b1", "a1"], []),
            _raum("r2", ["b1", "a1"], [], parent="r1"),
        )
        b = await self._bilanz(["b1", "b2"], namen={"b1": "B-Eins", "b2": "B-Zwei"})
        self.assertEqual(b.arten.get("meeting:folgemeeting"), 1, "nur fuer b1, nicht fuer a1")

    async def test_kein_fremder_name_in_der_antwort(self):
        await self._anlegen(
            _raum("r1", ["b1", "a1"], ["b1", "a1"]),
            _raum("r2", ["b1", "a1"], ["a1"], parent="r1"),
        )
        befunde = await ps.befunde_fuer(self.db, {"b1", "b2"}, jetzt=JETZT)
        self.assertNotIn("A-Eins", str([b.zu_dict() for b in befunde]))

    async def test_raum_ganz_ohne_sichtbare_beteiligte_zaehlt_nicht(self):
        await self._anlegen(_raum("r1", ["a1"], ["a1", "a1", "a1"]))
        b = await self._bilanz(["b1", "b2"], namen={"b1": "B-Eins", "b2": "B-Zwei"})
        self.assertEqual(b.koordination, 0)


class UeberlappendeGruppen(Basis):
    """Ein Agent kann in mehreren Teams sitzen, und ein Team kann aus Sicht des
    Aufrufers auf einen Agenten zusammenschrumpfen."""

    async def test_agent_mit_team_landet_nicht_zusaetzlich_bei_ohne_team(self):
        """Ein Zweier-Team, von dem der Aufrufer nur EINEN Agenten sieht, wird
        uebersprungen — sein Mitglied hat aber ein Team und gehoert nicht in
        die Gruppe „Agenten ohne Team"."""
        await self._anlegen(
            _agent("b1", "B-Eins", "ub"), _agent("b2", "B-Zwei", "ub"),
            _agent("a1", "A-Eins", "ua"),
            Team(id="t1", name="Gemischt", member_agent_ids=["b1", "a1"], lead_agent_id="a1"),
        )
        for i in range(30):
            self.db.add(_nachricht("b1", "b2") if i % 2 else _nachricht("b2", "b1"))
        await self.db.commit()
        befunde = await ps.befunde_fuer(self.db, {"b1", "b2"}, jetzt=JETZT)
        self.assertEqual(befunde, [], "b1 hat ein Team, b2 allein ist keine Gruppe")

    async def test_derselbe_agent_wird_nur_einmal_benachrichtigt(self):
        """Zwei Teams, dieselbe Person am anderen Ende: einmal klingeln."""
        await self._anlegen(
            _agent("a1", "Lead", "ua"), _agent("a2", "Zwei", "ua"), _agent("a3", "Drei", "ua"),
            Team(id="t1", name="T1", member_agent_ids=["a1", "a2"], lead_agent_id="a1"),
            Team(id="t2", name="T2", member_agent_ids=["a1", "a3"], lead_agent_id="a1"),
        )
        for _ in range(30):
            self.db.add(_nachricht("a1", "a2"))
        await self.db.commit()

        import contextlib
        from unittest.mock import patch

        @contextlib.asynccontextmanager
        async def _sitzung(*a, **k):
            yield self.db

        monitor = ps.ProduktivitaetsMonitor(self.Session, redis=_Warteschlange())
        with patch("app.db.session.resilient_session", _sitzung):
            gemeldet = await monitor.einmal_pruefen()

        from sqlalchemy import select
        meldungen = list((await self.db.execute(
            select(Notification).where(Notification.agent_id == "a1")
        )).scalars().all())
        self.assertEqual(len(meldungen), 1, f"beide Gruppen warnten: {[b.gruppe_id for b in gemeldet]}")
        # Auf der Health-Seite bleiben trotzdem BEIDE Gruppen sichtbar.
        befunde = await ps.befunde_fuer(self.db, None, jetzt=JETZT)
        self.assertEqual(sorted(b.gruppe_id for b in befunde if b.warnung), ["t1", "t2"])


class Grenzen(Basis):
    """Eine Zahl-Einstellung ausserhalb ihres Bereichs darf den Waechter nicht
    STILL blind machen."""

    async def test_fenster_null_wird_auf_eine_stunde_gezogen(self):
        await self._anlegen(PlatformSettings(key="produktivitaet_fenster_stunden", value="0"))
        with self.assertLogs("app.services.produktivitaet_service", level="WARNING") as protokoll:
            s = await ps.lade_schwellen(self.db)
        self.assertEqual(s["fenster_stunden"], 1)
        self.assertIn("ausserhalb", "".join(protokoll.output))

    async def test_negatives_fenster_wird_gezogen(self):
        await self._anlegen(PlatformSettings(key="produktivitaet_fenster_stunden", value="-5"))
        self.assertEqual((await ps.lade_schwellen(self.db))["fenster_stunden"], 1)

    async def test_ueberhohes_fenster_wird_gekappt(self):
        """Sonst umgeht ein Einstellwert die Obergrenze des Endpunkts."""
        await self._anlegen(PlatformSettings(key="produktivitaet_fenster_stunden", value="100000"))
        self.assertEqual((await ps.lade_schwellen(self.db))["fenster_stunden"], 720)

    async def test_untergrenze_null_wird_auf_eins_gezogen(self):
        await self._anlegen(PlatformSettings(key="produktivitaet_min_koordination", value="0"))
        self.assertEqual((await ps.lade_schwellen(self.db))["min_koordination"], 1)

    async def test_meldepause_null_bleibt_erlaubt(self):
        """0 heisst ausdruecklich „keine Pause" und ist dokumentiert."""
        await self._anlegen(PlatformSettings(key="produktivitaet_melde_pause_stunden", value="0"))
        self.assertEqual((await ps.lade_schwellen(self.db))["melde_pause_stunden"], 0)

    async def test_die_grenzen_behalten_ihren_typ(self):
        """Ein float in einer int-Einstellung faellt erst beim Rechnen auf."""
        await self._anlegen(PlatformSettings(key="produktivitaet_fenster_stunden", value="0"))
        s = await ps.lade_schwellen(self.db)
        self.assertIsInstance(s["fenster_stunden"], int)
        self.assertIsInstance(s["verhaeltnis"], float)


class Endpunkt(Basis):
    """Der Endpunkt filtert ueber ``visible_agent_ids`` und nicht ueber sich selbst."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self._anlegen(
            _agent("a1", "A-Eins", "ua"), _agent("a2", "A-Zwei", "ua"),
            _agent("b1", "B-Eins", "ub"), _agent("b2", "B-Zwei", "ub"),
            Team(id="ta", name="Team A", member_agent_ids=["a1", "a2"], lead_agent_id="a1"),
            Team(id="tb", name="Team B", member_agent_ids=["b1", "b2"], lead_agent_id="b1"),
        )
        # Bewusst ZWEI Stunden zurueck: so trennt das Ein-Stunden-Fenster unten
        # wirklich. Die Fenstergrenze selbst ist einschliessend (``>= seit``) —
        # ein Ereignis genau auf der Grenze zaehlt noch mit.
        for _ in range(30):
            self.db.add(_nachricht("a1", "a2", wann=JETZT - timedelta(hours=2)))
        await self.db.commit()

    async def test_mitglied_bekommt_nur_eigene_gruppen(self):
        from app.api.health import get_produktivitaet

        antwort = await get_produktivitaet(
            stunden=0, user=SimpleNamespace(id="ub", role=UserRole.MEMBER), db=self.db,
        )
        self.assertEqual([g["gruppe_name"] for g in antwort["gruppen"]], ["Team B"])
        self.assertEqual(antwort["warnungen"], 0)
        self.assertEqual(antwort["fenster_stunden"], ps.DEFAULTS["fenster_stunden"])

    async def test_administrator_sieht_warnung_mit_aufschluesselung(self):
        from app.api.health import get_produktivitaet

        antwort = await get_produktivitaet(
            stunden=0, user=SimpleNamespace(id="adm", role=UserRole.ADMIN), db=self.db,
        )
        self.assertEqual(antwort["warnungen"], 1)
        treffer = [g for g in antwort["gruppen"] if g["warnung"]]
        self.assertEqual(treffer[0]["gruppe_name"], "Team A")
        self.assertEqual(treffer[0]["arten"]["nachricht:question"], 30)
        self.assertEqual(antwort["schwellen"]["verhaeltnis"], ps.DEFAULTS["verhaeltnis"])

    async def test_fenster_aus_der_anfrage_schlaegt_die_einstellung(self):
        from app.api.health import get_produktivitaet

        antwort = await get_produktivitaet(
            stunden=1, user=SimpleNamespace(id="adm", role=UserRole.ADMIN), db=self.db,
        )
        self.assertEqual(antwort["fenster_stunden"], 1)
        # Die 30 Nachrichten liegen zwei Stunden zurueck und fallen heraus.
        self.assertEqual(antwort["warnungen"], 0)


class Einstellungen(Basis):
    async def test_vorgaben_wenn_nichts_abgelegt_ist(self):
        self.assertEqual(await ps.lade_schwellen(self.db), ps.DEFAULTS)

    async def test_abgelegte_werte_schlagen_die_vorgaben(self):
        await self._anlegen(
            PlatformSettings(key="produktivitaet_min_koordination", value="5"),
            PlatformSettings(key="produktivitaet_verhaeltnis", value="2.5"),
            PlatformSettings(key="produktivitaet_analyse_auftrag", value="true"),
        )
        s = await ps.lade_schwellen(self.db)
        self.assertEqual(s["min_koordination"], 5)
        self.assertEqual(s["verhaeltnis"], 2.5)
        self.assertIs(s["analyse_auftrag"], True)
        self.assertEqual(s["fenster_stunden"], ps.DEFAULTS["fenster_stunden"])

    async def test_unbrauchbarer_wert_laesst_die_vorgabe_stehen(self):
        """Eine Zahl-Einstellung mit Text darin darf den Waechter nicht kippen."""
        await self._anlegen(PlatformSettings(key="produktivitaet_min_koordination", value="viele"))
        s = await ps.lade_schwellen(self.db)
        self.assertEqual(s["min_koordination"], ps.DEFAULTS["min_koordination"])

    async def test_schalter_nur_bei_echtem_ja(self):
        await self._anlegen(PlatformSettings(key="produktivitaet_analyse_auftrag", value="False"))
        self.assertIs((await ps.lade_schwellen(self.db))["analyse_auftrag"], False)


class Waechter(Basis):
    """Der Hintergrundwaechter: Benachrichtigung, Meldepause, Analyse-Auftrag."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self._anlegen(
            _agent("a1", "Lead", "ua"), _agent("a2", "Zwei", "ua"),
            Team(id="ta", name="Team A", member_agent_ids=["a1", "a2"], lead_agent_id="a1"),
        )
        for _ in range(30):
            self.db.add(_nachricht("a1", "a2"))
        await self.db.commit()
        self.redis = _Warteschlange()
        self.monitor = ps.ProduktivitaetsMonitor(self.Session, redis=self.redis)

    async def _pruefen(self):
        """``einmal_pruefen`` mit der Sitzung dieses Tests."""
        import contextlib

        from unittest.mock import patch

        @contextlib.asynccontextmanager
        async def _sitzung(*a, **k):
            yield self.db

        with patch("app.db.session.resilient_session", _sitzung):
            return await self.monitor.einmal_pruefen()

    async def _meldungen(self):
        from sqlalchemy import select
        return list((await self.db.execute(
            select(Notification).where(Notification.agent_id == "a1")
        )).scalars().all())

    async def test_warnung_wird_an_den_lead_gemeldet(self):
        gemeldet = await self._pruefen()
        self.assertEqual([b.gruppe_id for b in gemeldet], ["ta"])
        meldungen = await self._meldungen()
        self.assertEqual(len(meldungen), 1)
        self.assertEqual(meldungen[0].type, "warning")
        self.assertEqual(meldungen[0].action_url, "/health")
        self.assertEqual(meldungen[0].meta["type"], ps.MELDUNG_ART)
        self.assertEqual(meldungen[0].meta["gruppe_id"], "ta")
        self.assertEqual(meldungen[0].meta["fortschritt"], 0)

    async def test_meldepause_verhindert_die_taegliche_wiederholung(self):
        """Derselbe Zustand darf nicht bei jedem Durchlauf erneut melden."""
        await self._pruefen()
        await self._pruefen()
        await self._pruefen()
        self.assertEqual(len(await self._meldungen()), 1)

    async def test_fremde_meldung_blockiert_die_eigene_nicht(self):
        """Die Pause erkennt ihre EIGENEN Vormeldungen, nicht jede Meldung."""
        await self._anlegen(Notification(
            agent_id="a1", type="info", title="etwas anderes", meta={"type": "disk_quota"},
        ))
        self.assertEqual(len(await self._pruefen()), 1)

    async def _auftraege(self):
        from sqlalchemy import select
        return list((await self.db.execute(select(Task))).scalars().all())

    async def _pause_loeschen(self):
        """Vormeldungen entfernen, damit der naechste Durchlauf wieder meldet."""
        for n in await self._meldungen():
            if (n.meta or {}).get("type") == ps.MELDUNG_ART:
                await self.db.delete(n)
        await self.db.commit()

    async def test_analyse_auftrag_nur_wenn_eingeschaltet(self):
        await self._pruefen()
        self.assertEqual(await self._auftraege(), [],
                         "Standard ist AUS — kein Modelllauf ohne Freigabe")

        await self._anlegen(PlatformSettings(key="produktivitaet_analyse_auftrag", value="true"))
        await self._pause_loeschen()
        await self._pruefen()

        offen = await self._auftraege()
        self.assertEqual(len(offen), 1)
        self.assertEqual(offen[0].agent_id, "a1", "an den Lead, nicht an einen beliebigen Agenten")
        self.assertEqual(offen[0].team_id, "ta")
        self.assertIn("Koordinationsschritte je Ergebnis", offen[0].prompt)

    async def test_analyse_auftrag_wird_eingereiht_nicht_nur_eingetragen(self):
        """Ein Eintrag mit ``PENDING`` wird von NIEMANDEM abgeholt.

        Es gibt im Haus keinen Aufraeumpfad fuer diesen Zustand, und ueber
        ``Task.team_id`` zaehlt er dauerhaft als laufende Arbeit des Teams —
        ein Auftrag, der nie laeuft, aber Arbeit vortaeuscht.
        """
        await self._anlegen(PlatformSettings(key="produktivitaet_analyse_auftrag", value="true"))
        await self._pruefen()

        offen = await self._auftraege()
        self.assertEqual(offen[0].status, TaskStatus.QUEUED)
        self.assertEqual([aid for aid, _ in self.redis.eingereiht], ["a1"])
        self.assertEqual(self.redis.eingereiht[0][1]["id"], offen[0].id)

    async def test_nicht_einreihbarer_auftrag_bleibt_nicht_liegen(self):
        """Scheitert die Einreihung, darf kein Eintrag zurueckbleiben."""
        self.monitor._redis = _Warteschlange(scheitert=True)
        await self._anlegen(PlatformSettings(key="produktivitaet_analyse_auftrag", value="true"))
        await self._pruefen()
        self.assertEqual(await self._auftraege(), [])
        self.assertEqual(len(await self._meldungen()), 1, "die Warnung selbst bleibt")

    async def test_ohne_warteschlange_entsteht_kein_auftrag(self):
        self.monitor._redis = None
        await self._anlegen(PlatformSettings(key="produktivitaet_analyse_auftrag", value="true"))
        await self._pruefen()
        self.assertEqual(await self._auftraege(), [])

    async def test_empfaenger_ist_der_lead_auch_wenn_er_nicht_der_erste_ist(self):
        """Im Aufbau oben ist der Lead zufaellig auch der alphabetisch erste
        Agent — damit waere „an den Lead" von „an den ersten" nicht zu
        unterscheiden. Hier ist der Lead ausdruecklich der ZWEITE."""
        team = await self.db.get(Team, "ta")
        team.lead_agent_id = "a2"
        await self.db.commit()

        gemeldet = await self._pruefen()
        self.assertEqual([b.melde_agent_id for b in gemeldet], ["a2"])
        from sqlalchemy import select
        alle = list((await self.db.execute(select(Notification))).scalars().all())
        self.assertEqual([n.agent_id for n in alle], ["a2"])

    async def test_ohne_lead_meldet_es_beim_lautesten_nicht_beim_ersten(self):
        """Gruppe ohne Lead (Agenten ohne Team): Empfaenger ist der Agent mit
        der meisten Koordination — er ist der Anlass. Der alphabetisch erste
        traf regelmaessig einen, der mit der Sache nichts zu tun hatte."""
        await self.db.delete(await self.db.get(Team, "ta"))
        # a2 ist der Lautere: die 30 Nachrichten aus dem Aufbau kommen von a1,
        # hier kommen 60 von a2 dazu.
        for _ in range(60):
            self.db.add(_nachricht("a2", "a1"))
        await self.db.commit()

        gemeldet = await self._pruefen()
        self.assertEqual([b.melde_agent_id for b in gemeldet], ["a2"],
                         "a1 waere der alphabetisch erste, a2 der lautere")

    async def test_lead_wechsel_meldet_denselben_zustand_nicht_erneut(self):
        """Die Pause gehoert zur GRUPPE, nicht zum Empfaenger.

        Haengt sie am Empfaenger, meldet ein Lead-Wechsel — oder ein neu
        angelegter Agent, der in der Auswahl vorrueckt — denselben Zustand
        sofort ein zweites Mal.
        """
        await self._pruefen()
        self.assertEqual(len(await self._meldungen()), 1)

        team = await self.db.get(Team, "ta")
        team.lead_agent_id = "a2"
        await self.db.commit()

        self.assertEqual(await self._pruefen(), [], "die Vormeldung muss gefunden werden")
        from sqlalchemy import select
        alle = list((await self.db.execute(select(Notification))).scalars().all())
        self.assertEqual(len(alle), 1, f"Empfaenger: {[n.agent_id for n in alle]}")

    async def test_nach_der_pause_wird_wieder_gemeldet(self):
        """Gegenprobe: die Pause darf nicht dauerhaft stummschalten."""
        await self._pruefen()
        for n in await self._meldungen():
            n.created_at = JETZT - timedelta(hours=30)
        await self.db.commit()
        self.assertEqual(len(await self._pruefen()), 1)

    async def test_ruhige_gruppe_wird_nicht_gemeldet(self):
        from sqlalchemy import select

        for n in (await self.db.execute(select(AgentMessage))).scalars().all():
            await self.db.delete(n)
        await self.db.commit()
        self.assertEqual(await self._pruefen(), [])
        self.assertEqual(await self._meldungen(), [])


if __name__ == "__main__":
    unittest.main()


class EinstellungsWeg(Basis):
    """Der Weg von der Oberflaeche bis zum Waechter — als echter Durchlauf.

    Die erlaubten Schluessel stehen an mehreren Stellen (``ALLOWED_KEYS``,
    ``SettingsUpdate``, ``SettingsResponse``, Schreibblock im Endpunkt). Kennt
    eine Stelle den Namen nicht, wird der Wert beim Speichern STILL verworfen
    und der Nutzer sieht „Gespeichert." ohne Wirkung (dieselbe Doppel-Falle wie
    bei ``test_entra_tenant_setting.py``). Darum wird hier geschrieben und
    wieder gelesen, nicht im Quelltext nach Zeichenketten gesucht.
    """

    SCHLUESSEL = (
        "produktivitaet_fenster_stunden", "produktivitaet_min_koordination",
        "produktivitaet_verhaeltnis", "produktivitaet_melde_pause_stunden",
        "produktivitaet_analyse_auftrag",
    )

    async def test_jeder_schluessel_ist_ueberhaupt_ablegbar(self):
        from app.services.settings_service import SettingsService

        svc = SettingsService(self.db)
        for key in self.SCHLUESSEL:
            # ``set`` wirft bei unbekanntem Schluessel — genau das ist die Falle.
            await svc.set(key, "1")
        await self.db.commit()

    async def test_geschriebene_werte_erreichen_den_waechter(self):
        from app.api.settings import update_settings
        from app.schemas.settings import SettingsUpdate

        await update_settings(
            data=SettingsUpdate(
                produktivitaet_fenster_stunden=48,
                produktivitaet_min_koordination=7,
                produktivitaet_verhaeltnis=3.5,
                produktivitaet_melde_pause_stunden=6,
                produktivitaet_analyse_auftrag=True,
            ),
            user=SimpleNamespace(id="adm", role=UserRole.ADMIN, email="adm@example.com"),
            db=self.db,
        )
        s = await ps.lade_schwellen(self.db)
        self.assertEqual(s["fenster_stunden"], 48)
        self.assertEqual(s["min_koordination"], 7)
        self.assertEqual(s["verhaeltnis"], 3.5)
        self.assertEqual(s["melde_pause_stunden"], 6)
        self.assertIs(s["analyse_auftrag"], True, "str(True) ergibt 'True' — ein Leser, der nur 'true' kennt, liest still AUS")

    async def test_der_schalter_laesst_sich_auch_wieder_ausschalten(self):
        from app.api.settings import update_settings
        from app.schemas.settings import SettingsUpdate

        nutzer = SimpleNamespace(id="adm", role=UserRole.ADMIN, email="adm@example.com")
        await update_settings(data=SettingsUpdate(produktivitaet_analyse_auftrag=True),
                              user=nutzer, db=self.db)
        await update_settings(data=SettingsUpdate(produktivitaet_analyse_auftrag=False),
                              user=nutzer, db=self.db)
        self.assertIs((await ps.lade_schwellen(self.db))["analyse_auftrag"], False)

    async def test_die_anzeige_gibt_die_werte_zurueck(self):
        """Ohne Feld in der Antwort kann die Oberflaeche den Stand nicht zeigen."""
        from app.schemas.settings import SettingsResponse

        for key in self.SCHLUESSEL:
            self.assertIn(key, SettingsResponse.model_fields)
