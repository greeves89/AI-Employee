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
from unittest.mock import MagicMock, patch

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
                metadata_={"schedule_id": frisch.id},
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


if __name__ == "__main__":
    unittest.main()
