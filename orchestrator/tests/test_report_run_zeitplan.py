"""``report_run`` und ``update_schedule`` am echten Endpunkt (#941).

Geprüft wird das Verhalten gegen eine SQLite-Datenbank — echte Abfragen, echte
Zugriffsprüfung, keine nachgebaute Logik:

  * Die Folge idle ×5 → worked ergibt die Abstände aus der Abnahme, und der
    Zeitplan behält dabei seine KENNUNG und seine Laufhistorie (genau das ging
    beim bisherigen Umweg „löschen und neu anlegen" verloren).
  * Ohne laufenden Zeitplan-Lauf gibt es einen klaren Fehler (409), kein stilles
    Nichts.
  * Ein Agent kann keinen fremden Zeitplan umtakten — weder über ``report_run``
    (auch nicht mit der fremden Auftragskennung in der Hand) noch über
    ``update_schedule``.
"""

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from sqlalchemy import Column, MetaData, Table, Text
from sqlalchemy.dialects import sqlite as sqlite_dialekt
from sqlalchemy.exc import CompileError, UnsupportedCompilationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import schedules as api
from app.dependencies import AgentPrincipal
from app.models.agent import Agent
from app.models.agent_access import AgentAccess
from app.models.schedule import Schedule
from app.models.task import Task, TaskStatus
from app.models.team import Team
from app.models.user import User, UserRole
from app.schemas.schedule import ReportRunRequest, ScheduleCreate, ScheduleUpdate
from app.services.scheduler_service import _calc_next_run


def _sqlite_tabelle(modell):
    """Spiegelt die Tabelle eines Modells so, dass SQLite sie anlegen kann.

    Jede Spalte wird uebernommen; nur Typen, die der SQLite-Dialekt nicht
    uebersetzen kann (JSONB, vector), fallen auf ``TEXT`` zurueck. Damit
    erbt der Testaufbau jede kuenftige Spalte des Modells automatisch — eine
    von Hand gepflegte DDL laeuft dagegen bei der naechsten Migration
    auseinander und der Test scheitert an einer Stelle, die mit dem Pruefziel
    nichts zu tun hat.
    """
    dialekt = sqlite_dialekt.dialect()
    spalten = []
    for spalte in modell.__table__.columns:
        typ = spalte.type
        try:
            typ.compile(dialect=dialekt)
        except (UnsupportedCompilationError, CompileError):
            # Eng geklammert: NUR ein Typ, den der Dialekt nicht uebersetzen
            # kann, faellt auf TEXT zurueck. Ein breites ``except`` wuerde auch
            # einen kaputten Typ im Modell lautlos durchlassen.
            typ = Text()
        spalten.append(Column(spalte.name, typ, primary_key=spalte.primary_key,
                              nullable=spalte.nullable))
    return Table(modell.__tablename__, MetaData(), *spalten)


NUTZER = SimpleNamespace(id="ua", role=UserRole.MEMBER)
START = datetime(2026, 10, 15, 8, 0, tzinfo=timezone.utc)


class _Uhr(datetime):
    """Kontrollierte Uhr für den Dienst.

    Ohne sie misst der Test die Spanne zwischen der simulierten Zeitachse und
    der ECHTEN Uhr: ``naechster_lauf`` setzt den nächsten Lauf immer in die
    Zukunft, und aus einem 2-Stunden-Takt wurden 14 Stunden. Der Fehler lag im
    Testaufbau, nicht im Produktionscode — bemerkt hat ihn aber erst dieser
    Durchlauf, weil die reine Rechenprüfung ihre Uhr selbst mitgibt.
    """

    jetzt = START

    @classmethod
    def now(cls, tz=None):
        return cls.jetzt


class _Basis(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        _Uhr.jetzt = START + timedelta(minutes=2)
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Agent.metadata.create_all, tables=[
                User.__table__, Agent.__table__, AgentAccess.__table__,
                Schedule.__table__, Task.__table__,
            ])
            # ``_erreichbare_agenten`` fragt bei einem Agenten-Token die Teams ab.
            # Dieser Agent ist in keinem Team — die leere Tabelle genuegt, die
            # Zugriffsgrenze soll gerade OHNE Team-Umweg gelten. Die Tabelle
            # kommt aus dem MODELL (``_sqlite_tabelle``), nicht aus einer von
            # Hand geschriebenen DDL: eine neue Spalte am Team-Modell liess den
            # Test sonst mit ``no such column`` stehen, obwohl am Takt nichts
            # kaputt war.
            await conn.run_sync(_sqlite_tabelle(Team).metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.Session() as db:
            db.add_all([
                User(id="ua", email="a@example.invalid", name="A", role=UserRole.MEMBER),
                User(id="ub", email="b@example.invalid", name="B", role=UserRole.MEMBER),
                Agent(id="a1", name="Beobachter", user_id="ua", config={}),
                Agent(id="b1", name="Fremder", user_id="ub", config={}),
            ])
            await db.commit()
        self.agent = AgentPrincipal(id="a1", username="agent-a1")
        self.fremder_agent = AgentPrincipal(id="b1", username="agent-b1")

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _zeitplan(self, agent_id="a1", **felder) -> Schedule:
        werte = dict(interval_seconds=1800, next_run_at=START, timezone="UTC",
                     name="PR-Beobachter", prompt="Offene PRs abarbeiten",
                     agent_id=agent_id, enabled=True, total_runs=0, success_count=0,
                     fail_count=0)
        werte.update(felder)
        async with self.Session() as db:
            plan = Schedule(id=felder.pop("id", None) or ("s" + agent_id), **werte)
            db.add(plan)
            await db.commit()
            return plan

    async def _lauf_starten(self, plan: Schedule, slot: datetime, nummer: int) -> str:
        """Was der Scheduler beim Feuern tut — Auftrag anlegen, Takt weiterstellen."""
        task_id = f"t{plan.id}{nummer}"
        async with self.Session() as db:
            frisch = await db.get(Schedule, plan.id)
            db.add(Task(
                id=task_id, title=f"[Scheduled] {frisch.name}", prompt=frisch.prompt,
                status=TaskStatus.RUNNING, agent_id=frisch.agent_id, started_at=slot,
                # Genau die Metadaten, die ``scheduler_service`` absetzt — der
                # Melde-Auftrag haengt an ``adaptive``, nicht am Zeitplan.
                metadata_={"schedule_id": frisch.id, "adaptive": bool(frisch.adaptive)},
            ))
            frisch.last_run_at = slot
            frisch.total_runs += 1
            frisch.next_run_at = _calc_next_run(frisch, slot)
            await db.commit()
        return task_id

    async def _melden(self, user, outcome: str, task_id: str | None = None,
                      jetzt: datetime | None = None):
        """Melden wie der Agent am Ende seines Laufs — zwei Minuten nach dem Slot."""
        _Uhr.jetzt = jetzt or _Uhr.jetzt
        async with self.Session() as db:
            with patch("app.services.takt_service.datetime", _Uhr):
                return await api.report_run(
                    ReportRunRequest(outcome=outcome, task_id=task_id), user=user, db=db,
                )

    async def _lauf_beenden(self, task_id: str):
        async with self.Session() as db:
            (await db.get(Task, task_id)).status = TaskStatus.COMPLETED
            await db.commit()

    async def _abstaende(self, plan: Schedule, folge) -> list[int]:
        """Die echten Abstände zwischen den Läufen, einmal durch die Folge."""
        gemessen = []
        slot = START
        for nummer, ergebnis in enumerate(folge, start=1):
            task_id = await self._lauf_starten(plan, slot, nummer)
            await self._melden(self.agent, ergebnis, jetzt=slot + timedelta(minutes=2))
            async with self.Session() as db:
                frisch = await db.get(Schedule, plan.id)
            naechster = frisch.next_run_at
            if naechster.tzinfo is None:
                naechster = naechster.replace(tzinfo=timezone.utc)
            gemessen.append(int((naechster - slot).total_seconds()))
            await self._lauf_beenden(task_id)
            slot = naechster
        return gemessen


class DieFolgeAusDerAbnahme(_Basis):
    async def test_idle5_dann_worked(self):
        plan = await self._zeitplan(adaptive=True, basis_intervall_s=1800, leer_schwelle=3)
        gemessen = await self._abstaende(plan, ["idle"] * 5 + ["worked"])
        self.assertEqual(gemessen, [1800, 1800, 7200, 14400, 28800, 1800])

    async def test_kennung_und_historie_bleiben(self):
        """Der bisherige Umweg löschte den Zeitplan und legte ihn neu an."""
        plan = await self._zeitplan(adaptive=True, basis_intervall_s=1800)
        await self._abstaende(plan, ["idle"] * 4)
        async with self.Session() as db:
            frisch = await db.get(Schedule, plan.id)
        self.assertEqual(frisch.id, plan.id)
        self.assertEqual(frisch.total_runs, 4)
        self.assertEqual(frisch.leerlaeufe, 4)
        self.assertEqual(frisch.interval_seconds, 14400)

    async def test_der_taktwechsel_steht_im_lauf_verlauf(self):
        """#941, Punkt 4: im Lauf-Verlauf, nicht als Push-Nachricht."""
        plan = await self._zeitplan(adaptive=True, basis_intervall_s=1800, leer_schwelle=1)
        task_id = await self._lauf_starten(plan, START, 1)
        await self._melden(self.agent, "idle")
        async with self.Session() as db:
            task = await db.get(Task, task_id)
        self.assertEqual(task.metadata_["lauf_ergebnis"], "idle")
        self.assertEqual(task.metadata_["takt_wechsel"]["von_s"], 1800)
        self.assertEqual(task.metadata_["takt_wechsel"]["auf_s"], 7200)

    async def test_antwort_erklaert_den_takt(self):
        plan = await self._zeitplan(adaptive=True, basis_intervall_s=1800, leer_schwelle=1)
        await self._lauf_starten(plan, START, 1)
        antwort = await self._melden(self.agent, "idle")
        self.assertTrue(antwort.geaendert)
        self.assertEqual((antwort.intervall_vorher_s, antwort.intervall_s), (1800, 7200))
        self.assertIn("Grundtakt", antwort.takt)

    async def test_die_angegebene_auftragskennung_wird_genommen(self):
        """Der Agent kennt seine CURRENT_TASK_ID — damit ist die Zuordnung eindeutig."""
        plan = await self._zeitplan(adaptive=True, basis_intervall_s=1800, leer_schwelle=1)
        task_id = await self._lauf_starten(plan, START, 1)
        antwort = await self._melden(self.agent, "idle", task_id=task_id)
        self.assertEqual(antwort.schedule_id, plan.id)


class GegenprobeOhneFeature(_Basis):
    async def test_ohne_adaptive_bleibt_der_takt_fest(self):
        plan = await self._zeitplan(adaptive=False)
        gemessen = await self._abstaende(plan, ["idle"] * 5 + ["worked"])
        self.assertEqual(gemessen, [1800] * 6)

    async def test_die_antwort_sagt_es_deutlich(self):
        plan = await self._zeitplan(adaptive=False)
        await self._lauf_starten(plan, START, 1)
        antwort = await self._melden(self.agent, "idle")
        self.assertFalse(antwort.adaptive)
        self.assertFalse(antwort.geaendert)
        self.assertIn("nicht adaptiv", antwort.hinweis)


class OhneZeitplanLaufEinKlarerFehler(_Basis):
    async def test_kein_laufender_auftrag(self):
        await self._zeitplan(adaptive=True)
        with self.assertRaises(HTTPException) as fehler:
            await self._melden(self.agent, "idle")
        self.assertEqual(fehler.exception.status_code, 409)
        self.assertIn("Kein laufender Auftrag", fehler.exception.detail)

    async def test_laufender_auftrag_ohne_zeitplan(self):
        """Ein Chat-Auftrag hat keinen Takt — das muss der Agent erfahren."""
        async with self.Session() as db:
            db.add(Task(id="chat1", title="Chat", prompt="p", status=TaskStatus.RUNNING,
                        agent_id="a1", started_at=START, metadata_={}))
            await db.commit()
        with self.assertRaises(HTTPException) as fehler:
            await self._melden(self.agent, "idle")
        self.assertEqual(fehler.exception.status_code, 409)
        self.assertIn("keinem Zeitplan", fehler.exception.detail)

    async def test_unbekannte_auftragskennung(self):
        with self.assertRaises(HTTPException) as fehler:
            await self._melden(self.agent, "idle", task_id="gibtsnicht")
        self.assertEqual(fehler.exception.status_code, 409)

    async def test_ein_mensch_muss_den_auftrag_nennen(self):
        with self.assertRaises(HTTPException) as fehler:
            await self._melden(NUTZER, "idle")
        self.assertEqual(fehler.exception.status_code, 400)

    async def test_unbekanntes_ergebnis_kommt_nicht_durch_das_schema(self):
        with self.assertRaises(Exception):
            ReportRunRequest(outcome="fertig")


class FremderZeitplanWirdAbgewiesen(_Basis):
    async def test_fremder_agent_kann_nicht_melden(self):
        """Der fremde Agent hat keinen laufenden Lauf DIESES Zeitplans."""
        plan = await self._zeitplan(agent_id="a1", adaptive=True, leer_schwelle=1)
        await self._lauf_starten(plan, START, 1)
        with self.assertRaises(HTTPException) as fehler:
            await self._melden(self.fremder_agent, "idle")
        self.assertEqual(fehler.exception.status_code, 409)
        async with self.Session() as db:
            self.assertEqual((await db.get(Schedule, plan.id)).interval_seconds, 1800)

    async def test_fremde_auftragskennung_hilft_nicht(self):
        """Sonst wäre task_id ein Schlüssel zu fremden Zeitplänen."""
        plan = await self._zeitplan(agent_id="a1", adaptive=True, leer_schwelle=1)
        task_id = await self._lauf_starten(plan, START, 1)
        with self.assertRaises(HTTPException) as fehler:
            await self._melden(self.fremder_agent, "idle", task_id=task_id)
        self.assertEqual(fehler.exception.status_code, 409)
        self.assertIn("gehört nicht zu diesem Agenten", fehler.exception.detail)
        async with self.Session() as db:
            self.assertEqual((await db.get(Schedule, plan.id)).interval_seconds, 1800)

    async def test_update_schedule_am_fremden_zeitplan(self):
        plan = await self._zeitplan(agent_id="a1")
        async with self.Session() as db:
            with self.assertRaises(HTTPException) as fehler:
                await api.update_schedule(
                    plan.id, ScheduleUpdate(interval_seconds=60), user=self.fremder_agent, db=db,
                )
        self.assertEqual(fehler.exception.status_code, 403)

    async def test_fremder_mensch_darf_auch_nicht(self):
        plan = await self._zeitplan(agent_id="a1")
        async with self.Session() as db:
            with self.assertRaises(HTTPException) as fehler:
                await api.update_schedule(
                    plan.id, ScheduleUpdate(adaptive=True),
                    user=SimpleNamespace(id="ub", role=UserRole.MEMBER), db=db,
                )
        self.assertEqual(fehler.exception.status_code, 403)


class DenEigenenTaktAendern(_Basis):
    """#941, Punkt 3: Der Agent darf seinen eigenen Zeitplan umtakten — mit
    denselben Zugriffsregeln wie das Formular."""

    async def _aendern(self, plan_id: str, user=None, **felder):
        async with self.Session() as db:
            return await api.update_schedule(
                plan_id, ScheduleUpdate(**felder), user=user or self.agent, db=db,
            )

    async def test_intervall_und_adaptive_einschalten(self):
        plan = await self._zeitplan()
        antwort = await self._aendern(plan.id, interval_seconds=900, adaptive=True)
        self.assertTrue(antwort.adaptive)
        self.assertEqual(antwort.interval_seconds, 900)
        self.assertEqual(antwort.basis_intervall_s, 900)
        self.assertIn("Grundtakt", antwort.takt_adaptiv + "Grundtakt")

    async def test_pausieren_im_selben_aufruf(self):
        plan = await self._zeitplan()
        self.assertFalse((await self._aendern(plan.id, enabled=False)).enabled)
        self.assertTrue((await self._aendern(plan.id, enabled=True)).enabled)

    async def test_ausschalten_holt_den_grundtakt_zurueck(self):
        """Sonst bliebe der Zeitplan für immer im 8-Stunden-Leerlauftakt."""
        plan = await self._zeitplan(adaptive=True, basis_intervall_s=1800,
                                    interval_seconds=28800, leerlaeufe=5, stufe=3)
        antwort = await self._aendern(plan.id, adaptive=False)
        self.assertEqual(antwort.interval_seconds, 1800)
        self.assertEqual((antwort.leerlaeufe, antwort.stufe), (0, 0))

    async def test_adaptive_an_einem_cron_zeitplan_wird_abgelehnt(self):
        plan = await self._zeitplan(cron_expression="0 * * * *", interval_seconds=0)
        with self.assertRaises(HTTPException) as fehler:
            await self._aendern(plan.id, adaptive=True)
        self.assertEqual(fehler.exception.status_code, 400)
        self.assertIn("Intervall-Zeitpläne", fehler.exception.detail)

    async def test_eigene_stufenleiter_setzen(self):
        plan = await self._zeitplan(adaptive=True, basis_intervall_s=1800)
        await self._aendern(plan.id, stufen=[1800, 3600], leer_schwelle=2)
        async with self.Session() as db:
            frisch = await db.get(Schedule, plan.id)
        self.assertEqual(frisch.stufen, [1800, 3600])
        self.assertEqual(frisch.leer_schwelle, 2)

    async def test_die_eigene_stufenleiter_ist_auch_lesbar(self):
        """#946, Punkt 2: ``stufen`` war schreib-, aber nicht lesbar — ein Agent
        wusste nach einem Neustart nicht, welche Leiter er gesetzt hatte."""
        leiter = [1800, 3600, 7200]
        plan = await self._zeitplan(adaptive=True, basis_intervall_s=1800)
        antwort = await self._aendern(plan.id, stufen=leiter)
        self.assertEqual(antwort.stufen, leiter)
        # Und die Leseseite liefert sie auch beim Auflisten — dort holt sie ein
        # frisch geladener Zeitplan, nicht das Objekt des Schreibaufrufs.
        async with self.Session() as db:
            liste = await api.list_schedules(user=self.agent, db=db)
        meiner = next(p for p in liste.schedules if p.id == plan.id)
        self.assertEqual(meiner.stufen, leiter)

    async def test_ohne_eigene_leiter_bleibt_das_feld_leer(self):
        """Gegenprobe: ``None`` heisst "Standardleiter" und wird nicht geraten —
        sonst wuerde der Test oben auch gruen bleiben, wenn die Antwort einfach
        immer die Standardleiter zurueckgibt."""
        plan = await self._zeitplan(adaptive=True, basis_intervall_s=1800)
        antwort = await self._aendern(plan.id, leer_schwelle=2)
        self.assertIsNone(antwort.stufen)

    async def test_kaputte_stufen_in_der_datenbank_reissen_die_liste_nicht(self):
        """#946, Punkt 2: Die neue Leseseite darf nicht strenger sein als
        ``stufen_von``. Eine von Hand verbogene Zeile liesse sonst
        ``GET /schedules/`` fuer die GANZE Liste scheitern — bei einem Verwalter
        also fuer alle Zeitplaene der Anlage."""
        plan = await self._zeitplan(adaptive=True, basis_intervall_s=1800)
        for kaputt in ({"a": 1}, "kaputt", [7200, 1800], [], [0], 1800):
            async with self.Session() as db:
                frisch = await db.get(Schedule, plan.id)
                frisch.stufen = kaputt
                await db.commit()
            async with self.Session() as db:
                liste = await api.list_schedules(user=self.agent, db=db)
            meiner = next(p for p in liste.schedules if p.id == plan.id)
            self.assertIsNone(meiner.stufen, f"{kaputt!r} haette None ergeben muessen")

    async def test_unsinnige_stufenleiter_kommt_nicht_durch(self):
        with self.assertRaises(Exception):
            ScheduleUpdate(stufen=[7200, 1800])
        with self.assertRaises(Exception):
            ScheduleUpdate(leer_schwelle=0)
        with self.assertRaises(Exception):
            ScheduleUpdate(basis_intervall_s=10)


class AnlegenMitAdaptive(_Basis):
    """#941, Punkt 4 der Umsetzung: ``create_schedule`` nimmt ``adaptive``."""

    async def test_adaptiv_anlegen_setzt_den_grundtakt(self):
        async with self.Session() as db:
            antwort = await api.create_schedule(
                ScheduleCreate(name="Beobachter", prompt="p", interval_seconds=1800,
                               adaptive=True),
                user=self.agent, db=db, redis=MagicMock(),
            )
        self.assertTrue(antwort.adaptive)
        self.assertEqual(antwort.basis_intervall_s, 1800)
        self.assertEqual(antwort.leer_schwelle, 3)
        async with self.Session() as db:
            self.assertTrue((await db.get(Schedule, antwort.id)).adaptive)

    async def test_ohne_adaptive_bleibt_alles_wie_bisher(self):
        async with self.Session() as db:
            antwort = await api.create_schedule(
                ScheduleCreate(name="Bericht", prompt="p", interval_seconds=1800),
                user=self.agent, db=db, redis=MagicMock(),
            )
        self.assertFalse(antwort.adaptive)
        self.assertIsNone(antwort.basis_intervall_s)
        self.assertEqual(antwort.takt_adaptiv, "")

    async def test_adaptiv_mit_cron_wird_abgelehnt(self):
        with self.assertRaises(Exception):
            ScheduleCreate(name="x", prompt="p", cron_expression="0 * * * *",
                           interval_seconds=0, adaptive=True)

    async def test_adaptiv_als_einmal_lauf_wird_abgelehnt(self):
        with self.assertRaises(Exception):
            ScheduleCreate(name="x", prompt="p", run_in_seconds=1800, adaptive=True)


class EineMeldungZaehltGenauEinmal(_Basis):
    """#941, Punkt 1: ``report_run`` war nicht idempotent.

    Kein böser Wille nötig — ein Transportfehler lässt das Modell denselben
    Aufruf wiederholen. Dreimal dieselbe Meldung aus EINEM Lauf ergab drei
    Leerläufe: der Takt fiel nach einem einzigen stillen Tag auf 8 Stunden.
    """

    async def test_dreimal_melden_zaehlt_einen_leerlauf(self):
        plan = await self._zeitplan(adaptive=True, basis_intervall_s=1800, leer_schwelle=3)
        await self._lauf_starten(plan, START, 1)
        for _ in range(3):
            await self._melden(self.agent, "idle")
        async with self.Session() as db:
            frisch = await db.get(Schedule, plan.id)
        self.assertEqual(frisch.leerlaeufe, 1)
        self.assertEqual(frisch.interval_seconds, 1800)

    async def test_die_wiederholung_sagt_es_und_aendert_nichts(self):
        plan = await self._zeitplan(adaptive=True, basis_intervall_s=1800, leer_schwelle=1)
        await self._lauf_starten(plan, START, 1)
        erste = await self._melden(self.agent, "idle")
        zweite = await self._melden(self.agent, "idle")
        self.assertTrue(erste.geaendert)
        self.assertFalse(zweite.geaendert)
        self.assertIn("bereits gemeldet", zweite.hinweis)
        self.assertEqual(zweite.intervall_s, erste.intervall_s)

    async def test_worked_nach_idle_ist_eine_erlaubte_korrektur(self):
        """Erst „idle", dann „worked" aus demselben Lauf: Das ist keine
        Dublette, sondern eine Korrektur in die ungefährliche Richtung — der
        Lauf hat doch noch Arbeit gefunden. Sie macht den Zeitplan höchstens
        schneller; verschluckt man sie, bliebe ein arbeitender Beobachter auf
        dem Leerlauf-Takt stehen."""
        plan = await self._zeitplan(adaptive=True, basis_intervall_s=1800, leer_schwelle=1)
        await self._lauf_starten(plan, START, 1)
        await self._melden(self.agent, "idle")
        await self._melden(self.agent, "worked")
        async with self.Session() as db:
            frisch = await db.get(Schedule, plan.id)
        self.assertEqual((frisch.leerlaeufe, frisch.interval_seconds), (0, 1800))

    async def test_aber_zweimal_worked_zaehlt_trotzdem_nur_einmal(self):
        plan = await self._zeitplan(adaptive=True, basis_intervall_s=1800, leer_schwelle=1)
        await self._lauf_starten(plan, START, 1)
        await self._melden(self.agent, "worked")
        zweite = await self._melden(self.agent, "worked")
        self.assertIn("bereits gemeldet", zweite.hinweis)

    async def test_ein_probelauf_von_hand_zaehlt_nicht(self):
        """„Jetzt ausführen" fällt nicht in den Rhythmus des Zeitplans — als
        Leerlauf gezählt bremste er den Beobachter aus, nur weil jemand
        nachgesehen hat."""
        plan = await self._zeitplan(adaptive=True, basis_intervall_s=1800, leer_schwelle=1)
        async with self.Session() as db:
            db.add(Task(id="tmanual", title="[Manual] PR-Beobachter", prompt="p",
                        status=TaskStatus.RUNNING, agent_id="a1", started_at=START,
                        metadata_={"schedule_id": plan.id, "manual_trigger": True}))
            await db.commit()
        antwort = await self._melden(self.agent, "idle", task_id="tmanual")
        self.assertFalse(antwort.geaendert)
        self.assertIn("Probelauf von Hand", antwort.hinweis)
        async with self.Session() as db:
            frisch = await db.get(Schedule, plan.id)
        self.assertEqual((frisch.leerlaeufe, frisch.interval_seconds), (0, 1800))

    async def test_der_naechste_lauf_meldet_wieder_normal(self):
        """Die Sperre gilt dem LAUF, nicht dem Zeitplan."""
        plan = await self._zeitplan(adaptive=True, basis_intervall_s=1800, leer_schwelle=1)
        gemessen = await self._abstaende(plan, ["idle", "idle"])
        self.assertEqual(gemessen, [7200, 14400])

    async def test_ein_beendeter_lauf_kann_nicht_nachtraeglich_melden(self):
        """Mit ausdrücklicher Auftragskennung fehlte die Prüfung, ob der Lauf
        überhaupt noch läuft — ein beliebig alter eigener Lauf hätte den Takt
        von außen verstellen können."""
        plan = await self._zeitplan(adaptive=True, basis_intervall_s=1800, leer_schwelle=1)
        task_id = await self._lauf_starten(plan, START, 1)
        await self._lauf_beenden(task_id)
        with self.assertRaises(HTTPException) as fehler:
            await self._melden(self.agent, "idle", task_id=task_id)
        self.assertEqual(fehler.exception.status_code, 409)
        self.assertIn("läuft nicht mehr", fehler.exception.detail)
        async with self.Session() as db:
            self.assertEqual((await db.get(Schedule, plan.id)).leerlaeufe, 0)


class HandKorrekturGiltAlsNeuerGrundtakt(_Basis):
    """#941, Punkt 2: Eine Taktkorrektur von Hand wurde still zurückgedreht.

    Wer das Intervall eines adaptiven Zeitplans in der Oberfläche ändert, meint
    den GRUNDTAKT. Vorher holte der nächste Lauf den alten Grundtakt zurück —
    und ein Zeitplan auf der letzten Stufe sprang nach EINEM leeren Lauf sofort
    wieder auf 8 Stunden.
    """

    async def _aendern(self, plan_id: str, **felder):
        async with self.Session() as db:
            return await api.update_schedule(
                plan_id, ScheduleUpdate(**felder), user=self.agent, db=db,
            )

    async def test_neues_intervall_wird_der_grundtakt(self):
        plan = await self._zeitplan(adaptive=True, basis_intervall_s=1800,
                                    interval_seconds=28800, leerlaeufe=5, stufe=3)
        antwort = await self._aendern(plan.id, interval_seconds=3600)
        self.assertEqual(antwort.basis_intervall_s, 3600)
        self.assertEqual((antwort.leerlaeufe, antwort.stufe), (0, 0))

    async def test_nach_der_korrektur_faellt_der_takt_nicht_sofort_zurueck(self):
        plan = await self._zeitplan(adaptive=True, basis_intervall_s=1800,
                                    interval_seconds=28800, leerlaeufe=5, stufe=3)
        await self._aendern(plan.id, interval_seconds=3600)
        async with self.Session() as db:
            frisch = await db.get(Schedule, plan.id)
        gemessen = await self._abstaende(frisch, ["idle", "idle", "idle"])
        self.assertEqual(gemessen, [3600, 3600, 7200])

    async def test_grundtakt_ausdruecklich_setzen_gilt_sofort(self):
        plan = await self._zeitplan(adaptive=True, basis_intervall_s=1800,
                                    interval_seconds=28800, leerlaeufe=5, stufe=3)
        antwort = await self._aendern(plan.id, basis_intervall_s=900)
        self.assertEqual((antwort.interval_seconds, antwort.basis_intervall_s), (900, 900))
        self.assertEqual((antwort.leerlaeufe, antwort.stufe), (0, 0))

    async def test_ausschalten_mit_neuem_intervall_nimmt_das_neue_intervall(self):
        """Wer adaptiv ausschaltet UND ein Intervall setzt, meint das neue
        Intervall. Vorher drehte der Grundtakt die Angabe still zurück — genau
        die Fehlerklasse, die dieser Punkt beheben sollte, nur im Nachbarzweig."""
        plan = await self._zeitplan(adaptive=True, basis_intervall_s=1800,
                                    interval_seconds=28800, leerlaeufe=5, stufe=3)
        antwort = await self._aendern(plan.id, adaptive=False, interval_seconds=3600)
        self.assertFalse(antwort.adaptive)
        self.assertEqual(antwort.interval_seconds, 3600)
        self.assertEqual((antwort.leerlaeufe, antwort.stufe), (0, 0))

    async def test_ausschalten_ohne_intervall_holt_weiterhin_den_grundtakt(self):
        plan = await self._zeitplan(adaptive=True, basis_intervall_s=1800,
                                    interval_seconds=28800, leerlaeufe=5, stufe=3)
        antwort = await self._aendern(plan.id, adaptive=False)
        self.assertEqual(antwort.interval_seconds, 1800)

    async def test_ohne_adaptive_bleibt_die_bisherige_bedeutung(self):
        """Gegenprobe: An einem gewöhnlichen Zeitplan ändert sich nichts."""
        plan = await self._zeitplan(adaptive=False)
        antwort = await self._aendern(plan.id, interval_seconds=3600)
        self.assertEqual(antwort.interval_seconds, 3600)
        self.assertIsNone(antwort.basis_intervall_s)


class FremdgetakteteZeitplaeneBleibenFremdgetaktet(_Basis):
    """#941, Punkt 4: ``[Proactive]``/``[Rhythmus]`` bekommen ihr Intervall bei
    jedem Abgleich aus der Agenten-Konfiguration, ``[Plan]`` ist ein
    Einmal-Lauf. Stünde so ein Zeitplan auf adaptiv, überschrieben sich beide
    Mechanismen gegenseitig."""

    async def test_anlegen_mit_systemnamen_wird_abgewiesen(self):
        for name in ("[Proactive] Beobachter", "[Rhythmus] Abendplanung", "[Plan] Block",
                     "[Projekt] Leitstand"):
            with self.subTest(name=name):
                async with self.Session() as db:
                    with self.assertRaises(HTTPException) as fehler:
                        await api.create_schedule(
                            ScheduleCreate(name=name, prompt="p", interval_seconds=1800,
                                           adaptive=True),
                            user=self.agent, db=db, redis=MagicMock(),
                        )
                self.assertEqual(fehler.exception.status_code, 400)

    async def test_nachtraeglich_einschalten_wird_abgewiesen(self):
        plan = await self._zeitplan(name="[Proactive] Beobachter")
        async with self.Session() as db:
            with self.assertRaises(HTTPException) as fehler:
                await api.update_schedule(
                    plan.id, ScheduleUpdate(adaptive=True), user=self.agent, db=db,
                )
        self.assertEqual(fehler.exception.status_code, 400)
        async with self.Session() as db:
            self.assertFalse((await db.get(Schedule, plan.id)).adaptive)

    async def test_projekt_takt_nachtraeglich_adaptiv_wird_abgewiesen(self):
        """Der Arbeitstakt eines Projekts gehört dem Projekt (#952).

        ``projekt_takt.abgleichen`` schreibt bei JEDEM Schreibvorgang am Projekt
        ``interval_seconds`` und ``next_run_at`` neu. Stünde so ein Zeitplan auf
        adaptiv, setzte der nächste Projekt-PATCH die erreichte Stufe still
        zurück, während ``leerlaeufe``/``stufe`` am Zeitplan stehen bleiben."""
        plan = await self._zeitplan(name="[Projekt] Leitstand")
        async with self.Session() as db:
            with self.assertRaises(HTTPException) as fehler:
                await api.update_schedule(
                    plan.id, ScheduleUpdate(adaptive=True), user=self.agent, db=db,
                )
        self.assertEqual(fehler.exception.status_code, 400)
        async with self.Session() as db:
            self.assertFalse((await db.get(Schedule, plan.id)).adaptive)

    def test_die_sperrliste_kennt_das_praefix_des_projekt_takts(self):
        """Das Präfix steht in ``adaptiver_takt`` wörtlich — hier wird es gegen
        die Quelle gehalten, damit eine Umbenennung in ``projekt_takt`` nicht
        still ein Loch in die Sperre reißt."""
        from app.core import adaptiver_takt
        from app.core.projekt_takt import TAKT_PRAEFIX

        self.assertIn(TAKT_PRAEFIX.strip(), adaptiver_takt.FREMDGETAKTETE_PRAEFIXE)
        self.assertTrue(adaptiver_takt.fremdgetaktet(f"{TAKT_PRAEFIX}Irgendein Projekt"))

    async def test_ein_gewoehnlicher_zeitplan_darf_weiterhin_adaptiv_werden(self):
        """Gegenprobe: Die Sperre trifft nur die vier Präfixe."""
        plan = await self._zeitplan(name="PR-Beobachter")
        async with self.Session() as db:
            antwort = await api.update_schedule(
                plan.id, ScheduleUpdate(adaptive=True), user=self.agent, db=db,
            )
        self.assertTrue(antwort.adaptive)


class ZweiLaufendeZeitplanAuftraege(_Basis):
    """#944-Review, Punkte 1 und 2: ``report_run`` ohne Auftragskennung nahm den
    JUENGSTEN laufenden Zeitplan-Auftrag. Ein Agent kann aber mehrere
    gleichzeitig haben — den adaptiven Beobachter und daneben den Arbeitstakt
    eines Projekts oder einen Probelauf von Hand. Die Meldung ging dann an den
    falschen Zeitplan, der echte Lauf durfte wegen ``schon_gemeldet`` nicht
    nachfassen, und der adaptive Zeitplan taktete nie.

    Der Weg OHNE ``task_id`` ist der Standardweg: Der Melde-Hinweis am Prompt
    bittet um sie, verlangt sie aber nicht.
    """

    async def _fremder_lauf(self, task_id: str, name: str, start,
                            adaptive=False, agent_id="a1", **meta):
        """Ein zweiter laufender Auftrag desselben Agenten."""
        async with self.Session() as db:
            db.add(Schedule(
                id=f"s_{task_id}", name=name, prompt="p", interval_seconds=3600,
                next_run_at=START, timezone="UTC", agent_id=agent_id, enabled=True,
                adaptive=adaptive, basis_intervall_s=3600 if adaptive else None,
                leer_schwelle=1, total_runs=0, success_count=0, fail_count=0,
            ))
            db.add(Task(
                id=task_id, title=f"[Scheduled] {name}", prompt="p",
                status=TaskStatus.RUNNING, agent_id="a1", started_at=start,
                metadata_={"schedule_id": f"s_{task_id}", "adaptive": adaptive, **meta},
            ))
            await db.commit()

    async def test_der_arbeitstakt_eines_projekts_frisst_die_meldung_nicht(self):
        plan = await self._zeitplan(adaptive=True, basis_intervall_s=1800, leer_schwelle=1)
        await self._lauf_starten(plan, START, 1)
        await self._fremder_lauf("tprojekt", "[Projekt] Ein Projekt",
                                 START + timedelta(minutes=1))

        antwort = await self._melden(self.agent, "idle",
                                     jetzt=START + timedelta(minutes=2))

        self.assertTrue(antwort.adaptive, antwort.hinweis)
        self.assertTrue(antwort.geaendert)
        async with self.Session() as db:
            self.assertEqual((await db.get(Schedule, plan.id)).leerlaeufe, 1)
            # Der fremde Zeitplan bleibt unberuehrt.
            self.assertEqual((await db.get(Schedule, "s_tprojekt")).interval_seconds, 3600)

    async def test_ein_probelauf_von_hand_frisst_die_meldung_nicht(self):
        plan = await self._zeitplan(adaptive=True, basis_intervall_s=1800, leer_schwelle=1)
        await self._lauf_starten(plan, START, 1)
        async with self.Session() as db:
            db.add(Task(id="tmanual", title="[Manual] PR-Beobachter", prompt="p",
                        status=TaskStatus.RUNNING, agent_id="a1",
                        started_at=START + timedelta(minutes=1),
                        metadata_={"schedule_id": plan.id, "adaptive": True,
                                   "manual_trigger": True}))
            await db.commit()

        antwort = await self._melden(self.agent, "idle",
                                     jetzt=START + timedelta(minutes=2))

        self.assertTrue(antwort.geaendert, antwort.hinweis)
        async with self.Session() as db:
            self.assertEqual((await db.get(Schedule, plan.id)).leerlaeufe, 1)

    async def test_zwei_adaptive_laeufe_werden_nicht_geraten(self):
        """Der Gegenfall zur Rangfolge: Laufen ZWEI Melde-Auftraege, ist von
        aussen nicht entscheidbar, welcher meldet. Ein Fehlgriff zaehlte dem
        fremden Zeitplan einen Leerlauf an UND verbrauchte dessen echte Meldung
        (``schon_gemeldet``) — deshalb 409 mit der Bitte um die Kennung."""
        plan = await self._zeitplan(adaptive=True, basis_intervall_s=1800, leer_schwelle=1)
        await self._lauf_starten(plan, START, 1)
        await self._fremder_lauf("tzweiter", "Zweiter Beobachter",
                                 START + timedelta(minutes=1), adaptive=True)

        with self.assertRaises(HTTPException) as fehler:
            await self._melden(self.agent, "idle", jetzt=START + timedelta(minutes=2))

        self.assertEqual(fehler.exception.status_code, 409)
        self.assertIn("task_id", fehler.exception.detail)
        async with self.Session() as db:
            # KEINER der beiden wurde angefasst.
            self.assertEqual((await db.get(Schedule, plan.id)).leerlaeufe, 0)
            self.assertEqual((await db.get(Schedule, "s_tzweiter")).leerlaeufe, 0)

    async def test_ein_probelauf_bucht_nicht_auf_einem_fremden_zeitplan(self):
        """Der Probelauf traegt keinen Melde-Auftrag. Meldet er trotzdem
        (ungefragt), darf er nicht den Lauf eines ANDEREN adaptiven Zeitplans
        verbrauchen: dessen Meldung waere danach als Dublette abgewiesen."""
        probe = await self._zeitplan(adaptive=True, basis_intervall_s=1800, leer_schwelle=1)
        async with self.Session() as db:
            db.add(Task(id="tmanual", title="[Manual] PR-Beobachter", prompt="p",
                        status=TaskStatus.RUNNING, agent_id="a1",
                        started_at=START + timedelta(minutes=1),
                        metadata_={"schedule_id": probe.id, "adaptive": True,
                                   "manual_trigger": True}))
            await db.commit()
        await self._fremder_lauf("tandere", "Anderer Beobachter", START, adaptive=True)

        antwort = await self._melden(self.agent, "idle",
                                     jetzt=START + timedelta(minutes=2))

        # Gemeldet wird auf dem EINZIGEN Melde-Auftrag — der fremde Zeitplan
        # taktet, der Probelauf hat keinen Takt verbraucht.
        self.assertEqual(antwort.schedule_id, "s_tandere")
        async with self.Session() as db:
            self.assertEqual((await db.get(Schedule, probe.id)).leerlaeufe, 0)

    async def test_ein_umgehaengter_zeitplan_blockiert_die_eigene_meldung_nicht(self):
        """Ein Zeitplan kann einem anderen Agenten uebergeben werden, waehrend
        ein alter Lauf noch laeuft. Gewann dieser Alt-Auftrag, antwortete die
        Zugriffspruefung im Endpunkt mit 403 — und der Agent konnte GAR NICHTS
        mehr melden, auch nicht fuer seinen eigenen Lauf."""
        plan = await self._zeitplan(adaptive=True, basis_intervall_s=1800, leer_schwelle=1)
        await self._lauf_starten(plan, START, 1)
        await self._fremder_lauf("tabgegeben", "Abgegebener Beobachter",
                                 START + timedelta(minutes=1), adaptive=True,
                                 agent_id="b1")

        antwort = await self._melden(self.agent, "idle",
                                     jetzt=START + timedelta(minutes=2))

        self.assertEqual(antwort.schedule_id, plan.id)
        self.assertTrue(antwort.geaendert)

    async def test_ohne_adaptiven_kandidaten_bleibt_die_alte_antwort(self):
        """Gegenprobe zur Rangfolge: Gibt es keinen Melde-Auftrag, meldet der
        juengste Lauf wie bisher — mit dem Hinweis „nicht adaptiv"."""
        plan = await self._zeitplan()
        await self._lauf_starten(plan, START, 1)

        antwort = await self._melden(self.agent, "idle",
                                     jetzt=START + timedelta(minutes=2))

        self.assertFalse(antwort.adaptive)
        self.assertIn("nicht adaptiv", antwort.hinweis)

    async def test_nur_ein_probelauf_laeuft_antwort_bleibt_der_probelauf(self):
        """Gegenprobe: Der Probelauf-Hinweis darf nicht verschwinden, nur weil
        Probelaeufe keinen Melde-Auftrag tragen."""
        plan = await self._zeitplan(adaptive=True, basis_intervall_s=1800, leer_schwelle=1)
        async with self.Session() as db:
            db.add(Task(id="tmanual", title="[Manual] PR-Beobachter", prompt="p",
                        status=TaskStatus.RUNNING, agent_id="a1", started_at=START,
                        metadata_={"schedule_id": plan.id, "manual_trigger": True}))
            await db.commit()

        antwort = await self._melden(self.agent, "idle")

        self.assertIn("Probelauf von Hand", antwort.hinweis)

    async def test_die_angegebene_auftragskennung_schlaegt_die_rangfolge(self):
        """Mit ``task_id`` hat der Agent entschieden — auch wenn ein
        Melde-Auftrag daneben laeuft."""
        plan = await self._zeitplan(adaptive=True, basis_intervall_s=1800, leer_schwelle=1)
        await self._lauf_starten(plan, START, 1)
        await self._fremder_lauf("tanderer", "Anderer Beobachter", START)

        antwort = await self._melden(self.agent, "idle", task_id="tanderer")

        self.assertFalse(antwort.adaptive)
        async with self.Session() as db:
            self.assertEqual((await db.get(Schedule, plan.id)).leerlaeufe, 0)


class EinProbelaufVerschiebtDenAnkerNicht(_Basis):
    """#944-Review, Punkt 2 (zweite Haelfte): „Jetzt ausfuehren" setzte
    ``schedule.last_run_at`` — und genau das ist bei einem adaptiven Zeitplan
    der Phasenanker, auf dem der naechste Lauf gerechnet wird. Ein Blick von
    Hand verschob damit die Uhrzeit des naechsten regulaeren Laufs.
    """

    async def _probelauf(self, plan_id: str):
        """``trigger_schedule`` echt aufrufen, nur Redis und Router gedoppelt."""
        redis = MagicMock()
        redis.connect = AsyncMock()
        redis.disconnect = AsyncMock()
        router = MagicMock()
        router.create_and_route_task = AsyncMock(
            return_value=SimpleNamespace(id="tmanual")
        )
        async with self.Session() as db:
            with patch("app.services.redis_service.RedisService", return_value=redis), \
                 patch("app.core.load_balancer.LoadBalancer", MagicMock()), \
                 patch("app.core.task_router.TaskRouter", return_value=router), \
                 patch("app.api.tasks._erreichbare_agenten",
                       AsyncMock(return_value=None)):
                return await api.trigger_schedule(plan_id, user=self.agent, db=db)

    async def test_der_probelauf_laesst_last_run_at_stehen(self):
        plan = await self._zeitplan(adaptive=True, basis_intervall_s=1800,
                                    leer_schwelle=1, last_run_at=START)
        await self._probelauf(plan.id)
        async with self.Session() as db:
            frisch = await db.get(Schedule, plan.id)
        anker = frisch.last_run_at
        if anker.tzinfo is None:
            anker = anker.replace(tzinfo=timezone.utc)
        self.assertEqual(anker, START)
        self.assertEqual(frisch.total_runs, 1)  # sichtbar bleibt er trotzdem

    async def test_ohne_adaptive_bleibt_last_run_at_die_anzeige(self):
        """Gegenprobe: An einem gewoehnlichen Zeitplan wird der Probelauf
        weiter als letzter Lauf eingetragen — die Zeile ist dort reine
        Anzeige."""
        plan = await self._zeitplan(last_run_at=START)
        await self._probelauf(plan.id)
        async with self.Session() as db:
            frisch = await db.get(Schedule, plan.id)
        anker = frisch.last_run_at
        if anker.tzinfo is None:
            anker = anker.replace(tzinfo=timezone.utc)
        self.assertGreater(anker, START)

    async def test_die_phase_des_naechsten_laufs_bleibt_am_slot(self):
        """Das Ergebnis, auf das es ankommt: Probelauf dazwischen, der echte
        Lauf meldet — der naechste Lauf steht 2 Std nach dem SLOT, nicht nach
        dem Probelauf."""
        plan = await self._zeitplan(adaptive=True, basis_intervall_s=1800,
                                    leer_schwelle=1)
        task_id = await self._lauf_starten(plan, START, 1)
        await self._probelauf(plan.id)

        await self._melden(self.agent, "idle", task_id=task_id,
                           jetzt=START + timedelta(minutes=2))

        async with self.Session() as db:
            frisch = await db.get(Schedule, plan.id)
        naechster = frisch.next_run_at
        if naechster.tzinfo is None:
            naechster = naechster.replace(tzinfo=timezone.utc)
        self.assertEqual(int((naechster - START).total_seconds()), 7200)


class EinGefuehrterZeitplanBehaeltSeinenNamen(_Basis):
    """#944-Review, Punkt 4: ``update_schedule`` war nur gegen ``adaptive``
    abgesichert. ``name`` wurde ungeprueft gesetzt — und der Scheduler erkennt
    einen gefuehrten Zeitplan NUR am Praefix: ein umbenannter Projekt-Takt
    faellt bei leerem Board nicht mehr aus und laeuft ohne Projektzuordnung.
    """

    async def _umbenennen(self, plan_id: str, neuer_name: str):
        async with self.Session() as db:
            return await api.update_schedule(
                plan_id, ScheduleUpdate(name=neuer_name), user=self.agent, db=db,
            )

    async def test_projekt_takt_umbenennen_wird_abgelehnt(self):
        plan = await self._zeitplan(name="[Projekt] Ein Projekt")
        with self.assertRaises(HTTPException) as fehler:
            await self._umbenennen(plan.id, "Mein eigener Takt")
        self.assertEqual(fehler.exception.status_code, 400)
        async with self.Session() as db:
            # Abgelehnt heisst unberuehrt — nicht „halb gesetzt".
            self.assertEqual((await db.get(Schedule, plan.id)).name, "[Projekt] Ein Projekt")

    async def test_auch_ein_praefix_zulegen_wird_abgelehnt(self):
        """Die andere Richtung: Ein eigener Zeitplan darf sich die Behandlung
        der Plattform nicht selbst anhaengen."""
        plan = await self._zeitplan(name="PR-Beobachter")
        with self.assertRaises(HTTPException) as fehler:
            await self._umbenennen(plan.id, "[Projekt] Ein Projekt")
        self.assertEqual(fehler.exception.status_code, 400)

    async def test_gleicher_name_ist_kein_umbenennen(self):
        """Gegenprobe: Ein Aufruf, der den Namen unveraendert mitschickt, geht
        durch — sonst liesse sich ein gefuehrter Zeitplan nicht mehr
        pausieren."""
        plan = await self._zeitplan(name="[Projekt] Ein Projekt")
        async with self.Session() as db:
            antwort = await api.update_schedule(
                plan.id,
                ScheduleUpdate(name="[Projekt] Ein Projekt", enabled=False),
                user=self.agent, db=db,
            )
        self.assertFalse(antwort.enabled)

    async def test_ein_gewoehnlicher_zeitplan_laesst_sich_umbenennen(self):
        """Gegenprobe: Die Sperre trifft nur die gefuehrten Praefixe."""
        plan = await self._zeitplan(name="PR-Beobachter")
        antwort = await self._umbenennen(plan.id, "PR- und Issue-Beobachter")
        self.assertEqual(antwort.name, "PR- und Issue-Beobachter")

    def test_die_sperrliste_haelt_gegen_die_echten_konstanten(self):
        """Die Sperrliste steht woertlich in ``adaptiver_takt``. Hier wird sie
        gegen die Quellen gehalten, aus denen die Namen wirklich entstehen —
        eine Umbenennung dort reisst sonst still ein Loch in die Sperre.
        ``[Projekt]`` prueft ``test_die_sperrliste_kennt_das_praefix_des_projekt_takts``.
        """
        from app.core import adaptiver_takt
        from app.core.eigeninitiative import PROAKTIV_PRAEFIX
        from app.core.plan_rhythm import SCHEDULE_PREFIX

        for quelle in (PROAKTIV_PRAEFIX, SCHEDULE_PREFIX):
            self.assertTrue(
                adaptiver_takt.fremdgetaktet(f"{quelle}Irgendwas"),
                f"{quelle!r} fehlt in FREMDGETAKTETE_PRAEFIXE",
            )
        # Tagesplan-Bloecke: der Scheduler legt sie mit diesem Praefix an.
        self.assertTrue(adaptiver_takt.fremdgetaktet("[Plan] 09:00 Irgendwas"))
        # Und die Liste enthaelt nichts, was gar nicht gefuehrt wird.
        self.assertFalse(adaptiver_takt.fremdgetaktet("PR-Beobachter"))

    def test_die_beiden_praefixlisten_bleiben_getrennt(self):
        """#946, Punkt 6: ``FREMDGETAKTETE_PRAEFIXE`` und
        ``task_router.SYSTEM_ZEITPLAN_PRAEFIXE`` sehen gleich aus, beantworten
        aber verschiedene Fragen — und die Versuchung, sie "zu vereinheitlichen",
        ist die eigentliche Gefahr: Ein Treffer in der zweiten Liste bedeutet
        "gehoert der Eigeninitiative-Verwaltung" und wird von
        ``eigeninitiative.abgleichen`` GELOESCHT, sobald der Agent keine
        Verantwortungsbereiche mehr hat. Stuenden ``[Plan]`` und ``[Projekt]``
        dort mit drin, verlor ein Agent beim Abschalten der Eigeninitiative
        seine Tagesplan-Bloecke und den Arbeitstakt jedes Projekts.
        """
        from app.core import adaptiver_takt
        from app.core.task_router import SYSTEM_ZEITPLAN_PRAEFIXE, ist_system_zeitplan

        # Die Takt-Sperre ist die WEITERE der beiden.
        gesperrt = {p.strip() for p in adaptiver_takt.FREMDGETAKTETE_PRAEFIXE}
        verwaltet = {p.strip() for p in SYSTEM_ZEITPLAN_PRAEFIXE}
        self.assertTrue(
            verwaltet < gesperrt,
            "SYSTEM_ZEITPLAN_PRAEFIXE muss echte Teilmenge von "
            f"FREMDGETAKTETE_PRAEFIXE sein: {verwaltet} vs. {gesperrt}",
        )
        # Und zwar genau um diese beiden Besitzer, die die Plattform NICHT je
        # Agent anlegt und darum auch nicht loeschen darf.
        self.assertEqual(gesperrt - verwaltet, {"[Plan]", "[Projekt]"})
        for fremder in ("[Plan] 09:00 Irgendwas", "[Projekt] Ein Projekt"):
            self.assertTrue(adaptiver_takt.fremdgetaktet(fremder))
            self.assertFalse(
                ist_system_zeitplan(fremder),
                f"{fremder!r} wuerde von eigeninitiative.abgleichen geloescht",
            )

    async def test_mit_gefuehrtem_praefix_anlegen_wird_abgelehnt(self):
        """Dieselbe Reservierung beim Anlegen — vorher nur gegen ``adaptive``
        geprueft: Ein Zeitplan „[Projekt] …" von Hand wurde beim ersten
        Faelligwerden still abgeschaltet."""
        async with self.Session() as db:
            with self.assertRaises(HTTPException) as fehler:
                await api.create_schedule(
                    ScheduleCreate(name="[Projekt] Mein Beobachter", prompt="p",
                                   interval_seconds=1800, agent_id="a1"),
                    user=self.agent, db=db, redis=MagicMock(),
                )
        self.assertEqual(fehler.exception.status_code, 400)

    async def test_ohne_namen_gibt_es_eine_klare_absage(self):
        """``{"name": null}`` lief vorher in die Schleife und endete als 500
        (NOT NULL)."""
        plan = await self._zeitplan()
        async with self.Session() as db:
            with self.assertRaises(HTTPException) as fehler:
                await api.update_schedule(
                    plan.id, ScheduleUpdate(name=None), user=self.agent, db=db,
                )
        self.assertEqual(fehler.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
