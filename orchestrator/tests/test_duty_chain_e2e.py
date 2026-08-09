"""Vertretungskette von Ende zu Ende — gegen echtes SQL, nicht gegen Attrappen.

``test_agent_duty`` prueft die Entscheidungslogik mit ``SimpleNamespace``-Objekten. Das
ist schnell, beruehrt aber keine einzige Datenbankabfrage — und genau dort lag der Fehler:
``team_lead_for`` verband sich auf eine Tabelle ``team_members``, die es nie gab. Der
ImportError wurde vom ``except`` verschluckt, die Funktion gab stumm "" zurueck. Die
Team-Lead-Stufe der Kette hat dadurch nie ausgeloest, ohne dass ein Test das gemerkt haette.

Hier laufen deshalb die echten Dienstfunktionen gegen eine echte (In-Memory-)Datenbank
mit den echten Modellen: Agent faellt aus -> Vertreter uebernimmt nachweislich die Todos,
Mensch schweigt -> es geht nachweislich an den Team-Lead.
"""

import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy import select

from app.core import agent_duty as duty_core
from app.models.agent import Agent, AgentState
from app.models.agent_todo import AgentTodo, TodoStatus
from app.models.notification import Notification
from app.models.team import Team
from app.services import duty_service


# Die Team-Tabelle nutzt JSONB; fuer SQLite reicht JSON. Betrifft nur den Test.
@compiles(JSONB, "sqlite")
def _jsonb_as_json(type_, compiler, **kw):  # noqa: ANN001
    return "JSON"


UTC = timezone.utc


class _FakeRedisClient:
    """Nur so viel Redis, wie die Drossel braucht: SET mit nx/ex."""

    def __init__(self):
        self.keys: set[str] = set()

    async def set(self, key, value, nx=False, ex=None):  # noqa: ANN001
        if nx and key in self.keys:
            return False
        self.keys.add(key)
        return True


class _FakeRedis:
    def __init__(self):
        self.client = _FakeRedisClient()


class DutyChainBase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            for model in (Agent, AgentTodo, Notification, Team):
                await conn.run_sync(model.metadata.create_all, tables=[model.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        self.redis = _FakeRedis()

    async def asyncTearDown(self):
        await self.engine.dispose()

    def _agent(self, aid, name, state=AgentState.RUNNING, **config):
        return Agent(id=aid, name=name, state=state, user_id="u1", config=config)

    def _todo(self, agent_id, title, status=TodoStatus.PENDING, description=""):
        return AgentTodo(agent_id=agent_id, title=title, status=status, description=description)


class HandoverTests(DutyChainBase):
    async def test_deputy_really_receives_the_open_work(self):
        """Der Kern des offenen Punktes: Agent stoppen -> Arbeit landet beim Vertreter."""
        async with self.Session() as db:
            db.add_all([
                self._agent("dead", "Ausfaller", AgentState.STOPPED, deputy_agent_id="vertretung"),
                self._agent("vertretung", "Vertretung"),
                self._todo("dead", "Rechnung pruefen"),
                self._todo("dead", "Bericht schreiben", TodoStatus.IN_PROGRESS),
                self._todo("dead", "Schon erledigt", TodoStatus.COMPLETED),
            ])
            await db.commit()

            agent = (await db.execute(select(Agent).where(Agent.id == "dead"))).scalar_one()
            result = await duty_service.escalate_failure(
                db, self.redis, agent, {"reason": "Container gestoppt"}
            )
            await db.commit()

            self.assertTrue(result["handled"])
            self.assertEqual(result["deputy"], "vertretung")
            self.assertEqual(result["todos"], 2)

            moved = (await db.execute(
                select(AgentTodo).where(AgentTodo.agent_id == "vertretung")
            )).scalars().all()
            self.assertEqual({t.title for t in moved}, {"Rechnung pruefen", "Bericht schreiben"})
            # Laufende Arbeit faengt beim Vertreter neu an, sonst gilt sie als in Arbeit
            # bei jemandem, der nie damit angefangen hat.
            self.assertTrue(all(t.status == TodoStatus.PENDING for t in moved))
            # Herkunft bleibt nachvollziehbar.
            self.assertTrue(all("Ausfaller" in (t.description or "") for t in moved))

            # Die erledigte Aufgabe bleibt, wo sie war.
            left = (await db.execute(
                select(AgentTodo).where(AgentTodo.agent_id == "dead")
            )).scalars().all()
            self.assertEqual([t.title for t in left], ["Schon erledigt"])

    async def test_the_owner_is_told_once(self):
        async with self.Session() as db:
            db.add_all([
                self._agent("dead", "Ausfaller", AgentState.STOPPED, deputy_agent_id="vertretung"),
                self._agent("vertretung", "Vertretung"),
                self._todo("dead", "Rechnung pruefen"),
            ])
            await db.commit()
            agent = (await db.execute(select(Agent).where(Agent.id == "dead"))).scalar_one()
            await duty_service.escalate_failure(db, self.redis, agent, {"reason": "tot"})
            await db.commit()

            notes = (await db.execute(select(Notification))).scalars().all()
            self.assertEqual(len(notes), 1)
            self.assertIn("Vertretung", notes[0].title)
            self.assertEqual(notes[0].meta["deputy"], "vertretung")
            self.assertEqual(notes[0].meta["todos_moved"], 1)

    async def test_second_run_is_throttled(self):
        """Ein tagelang toter Agent darf nicht jeden Tick erneut uebergeben."""
        async with self.Session() as db:
            db.add_all([
                self._agent("dead", "Ausfaller", AgentState.STOPPED, deputy_agent_id="vertretung"),
                self._agent("vertretung", "Vertretung"),
                self._todo("dead", "Rechnung pruefen"),
            ])
            await db.commit()
            agent = (await db.execute(select(Agent).where(Agent.id == "dead"))).scalar_one()
            await duty_service.escalate_failure(db, self.redis, agent, {"reason": "tot"})
            second = await duty_service.escalate_failure(db, self.redis, agent, {"reason": "tot"})
            await db.commit()
            self.assertTrue(second.get("throttled"))
            self.assertEqual(len((await db.execute(select(Notification))).scalars().all()), 1)

    async def test_dead_deputy_does_not_swallow_the_work(self):
        """Einen zweiten toten Agenten zu beauftragen sieht aus wie Erledigung."""
        async with self.Session() as db:
            db.add_all([
                self._agent("dead", "Ausfaller", AgentState.STOPPED, deputy_agent_id="auch_tot"),
                self._agent("auch_tot", "Auch tot", AgentState.STOPPED),
                self._todo("dead", "Rechnung pruefen"),
            ])
            await db.commit()
            agent = (await db.execute(select(Agent).where(Agent.id == "dead"))).scalar_one()
            result = await duty_service.escalate_failure(db, self.redis, agent, {"reason": "tot"})
            await db.commit()

            self.assertEqual(result["deputy"], "")
            still_there = (await db.execute(
                select(AgentTodo).where(AgentTodo.agent_id == "dead")
            )).scalars().all()
            self.assertEqual(len(still_there), 1)
            note = (await db.execute(select(Notification))).scalars().first()
            self.assertIn("niemand übernimmt", note.title)
            self.assertEqual(note.priority, "high")


class TeamLeadFallbackTests(DutyChainBase):
    """Die Stufe, die nie ausgeloest hat — ohne eingetragenen Vertreter."""

    async def test_team_lead_is_found_via_the_member_list(self):
        async with self.Session() as db:
            db.add_all([
                self._agent("dead", "Ausfaller", AgentState.STOPPED),   # KEIN Vertreter
                self._agent("lead", "Teamleitung"),
                Team(id="t1", name="Buchhaltung", member_agent_ids=["dead", "lead"],
                     lead_agent_id="lead", is_active=True),
            ])
            await db.commit()
            self.assertEqual(await duty_service.team_lead_for(db, "dead"), "lead")

    async def test_lead_takes_over_when_no_deputy_is_set(self):
        async with self.Session() as db:
            db.add_all([
                self._agent("dead", "Ausfaller", AgentState.STOPPED),
                self._agent("lead", "Teamleitung"),
                Team(id="t1", name="Buchhaltung", member_agent_ids=["dead", "lead"],
                     lead_agent_id="lead", is_active=True),
                self._todo("dead", "Monatsabschluss"),
            ])
            await db.commit()
            agent = (await db.execute(select(Agent).where(Agent.id == "dead"))).scalar_one()
            result = await duty_service.escalate_failure(db, self.redis, agent, {"reason": "tot"})
            await db.commit()

            self.assertEqual(result["deputy"], "lead")
            moved = (await db.execute(
                select(AgentTodo).where(AgentTodo.agent_id == "lead")
            )).scalars().all()
            self.assertEqual([t.title for t in moved], ["Monatsabschluss"])

    async def test_an_agent_is_not_its_own_lead(self):
        async with self.Session() as db:
            db.add_all([
                self._agent("solo", "Alleinkaempfer", AgentState.STOPPED),
                Team(id="t1", name="Solo", member_agent_ids=["solo"],
                     lead_agent_id="solo", is_active=True),
            ])
            await db.commit()
            self.assertEqual(await duty_service.team_lead_for(db, "solo"), "")

    async def test_inactive_team_does_not_count(self):
        async with self.Session() as db:
            db.add_all([
                self._agent("dead", "Ausfaller", AgentState.STOPPED),
                self._agent("lead", "Teamleitung"),
                Team(id="t1", name="Aufgeloest", member_agent_ids=["dead", "lead"],
                     lead_agent_id="lead", is_active=False),
            ])
            await db.commit()
            self.assertEqual(await duty_service.team_lead_for(db, "dead"), "")

    async def test_non_member_does_not_inherit_a_lead(self):
        async with self.Session() as db:
            db.add_all([
                self._agent("fremd", "Fremder", AgentState.STOPPED),
                self._agent("lead", "Teamleitung"),
                Team(id="t1", name="Buchhaltung", member_agent_ids=["jemand", "lead"],
                     lead_agent_id="lead", is_active=True),
            ])
            await db.commit()
            self.assertEqual(await duty_service.team_lead_for(db, "fremd"), "")


class SilenceEscalationTests(DutyChainBase):
    """Schweigt der Mensch, geht es eine Stufe hoeher — auch das lief ins Leere."""

    def _old_note(self, agent_id, title, hours=20):
        return Notification(
            agent_id=agent_id, type="approval", title=title, message="", read=False,
            created_at=datetime.now(UTC) - timedelta(hours=hours),
        )

    async def test_escalates_to_the_team_lead(self):
        async with self.Session() as db:
            db.add_all([
                self._agent("fragend", "Frager"),
                self._agent("lead", "Teamleitung"),
                Team(id="t1", name="Team", member_agent_ids=["fragend", "lead"],
                     lead_agent_id="lead", is_active=True),
            ])
            for i in range(duty_core.ESCALATE_AFTER_UNANSWERED):
                db.add(self._old_note("fragend", f"Rueckfrage {i}"))
            await db.commit()

            agent = (await db.execute(select(Agent).where(Agent.id == "fragend"))).scalar_one()
            self.assertTrue(await duty_service.escalate_silence(db, self.redis, agent))
            await db.commit()

            escalation = (await db.execute(
                select(Notification).where(Notification.agent_id == "lead")
            )).scalars().all()
            self.assertEqual(len(escalation), 1)
            self.assertEqual(escalation[0].meta["escalated_to"], "lead")
            self.assertIn("Team-Lead", escalation[0].message)

    async def test_without_a_team_it_goes_to_the_administration(self):
        async with self.Session() as db:
            db.add(self._agent("fragend", "Frager"))
            for i in range(duty_core.ESCALATE_AFTER_UNANSWERED):
                db.add(self._old_note("fragend", f"Rueckfrage {i}"))
            await db.commit()
            agent = (await db.execute(select(Agent).where(Agent.id == "fragend"))).scalar_one()
            self.assertTrue(await duty_service.escalate_silence(db, self.redis, agent))
            await db.commit()

            note = (await db.execute(
                select(Notification).where(Notification.type == "warning")
            )).scalars().one()
            self.assertEqual(note.meta["escalated_to"], "admin")
            self.assertIn("Administration", note.message)

    async def test_fresh_or_read_questions_do_not_escalate(self):
        async with self.Session() as db:
            db.add(self._agent("fragend", "Frager"))
            # zu frisch
            db.add(self._old_note("fragend", "Gerade erst gefragt", hours=1))
            # alt, aber gelesen
            gelesen = self._old_note("fragend", "Gelesen")
            gelesen.read = True
            db.add(gelesen)
            await db.commit()
            agent = (await db.execute(select(Agent).where(Agent.id == "fragend"))).scalar_one()
            self.assertFalse(await duty_service.escalate_silence(db, self.redis, agent))

    async def test_escalation_happens_once(self):
        async with self.Session() as db:
            db.add(self._agent("fragend", "Frager"))
            for i in range(duty_core.ESCALATE_AFTER_UNANSWERED):
                db.add(self._old_note("fragend", f"Rueckfrage {i}"))
            await db.commit()
            agent = (await db.execute(select(Agent).where(Agent.id == "fragend"))).scalar_one()
            self.assertTrue(await duty_service.escalate_silence(db, self.redis, agent))
            self.assertFalse(await duty_service.escalate_silence(db, self.redis, agent))


class NoBypassTests(unittest.TestCase):
    def test_no_join_on_a_table_that_does_not_exist(self):
        """Der urspruengliche Fehler: JOIN auf `team_members`, Tabelle existiert nicht."""
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / "app/services/duty_service.py").read_text()
        self.assertNotIn("TeamMember", src)
        self.assertIn("_teams_for_agent", src,
                      "Mitgliedschaft muss ueber den bestehenden Helfer laufen, "
                      "nicht ueber eine zweite eigene Abfrage.")


if __name__ == "__main__":
    unittest.main()
