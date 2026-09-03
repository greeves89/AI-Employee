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
from app.models.task import Task, TaskStatus
from app.models.team import Team
from app.services import duty_service


# Die Team-Tabelle nutzt JSONB; fuer SQLite reicht JSON. Betrifft nur den Test.
@compiles(JSONB, "sqlite")
def _jsonb_as_json(type_, compiler, **kw):  # noqa: ANN001
    return "JSON"


UTC = timezone.utc


class _FakeRedisClient:
    """Nur so viel Redis, wie Drossel + Telegram-Publish brauchen."""

    def __init__(self):
        self.keys: set[str] = set()
        self.published: list[tuple[str, str]] = []

    async def set(self, key, value, nx=False, ex=None):  # noqa: ANN001
        if nx and key in self.keys:
            return False
        self.keys.add(key)
        return True

    async def publish(self, channel, message):  # noqa: ANN001
        self.published.append((channel, message))


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
            # #610: priority="high" allein reicht nicht — braucht den expliziten Publish,
            # weil dieser Pfad nie durch die agent-facing Notifications-API laeuft.
            self.assertEqual(len(self.redis.client.published), 1)
            self.assertEqual(self.redis.client.published[0][0], "telegram:notification")

    async def test_no_open_work_does_not_page_telegram(self):
        """priority="normal" (Vertreter gefunden, aber nichts zu uebergeben) soll nicht
        denselben Alarm ausloesen wie ein echter Ausfall mit Uebergabe — sonst verwaessert
        jede Meldung die naechste."""
        async with self.Session() as db:
            db.add_all([
                self._agent("dead", "Ausfaller", AgentState.STOPPED, deputy_agent_id="vertretung"),
                self._agent("vertretung", "Vertretung"),
            ])
            await db.commit()
            agent = (await db.execute(select(Agent).where(Agent.id == "dead"))).scalar_one()
            await duty_service.escalate_failure(db, self.redis, agent, {"reason": "tot"})
            await db.commit()

            note = (await db.execute(select(Notification))).scalars().one()
            self.assertEqual(note.priority, "normal")
            self.assertEqual(self.redis.client.published, [])

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


class OverloadEscalationTests(DutyChainBase):
    """#605: Ueberlast wurde bisher stillschweigend uebersprungen — kein Vertreter
    noetig (der Agent lebt), aber ohne Meldung faellt ein taeglicher Job spurlos aus."""

    async def test_notification_is_written(self):
        async with self.Session() as db:
            db.add(self._agent("voll", "Podcast-Agent"))
            await db.commit()
            agent = (await db.execute(select(Agent).where(Agent.id == "voll"))).scalar_one()
            duty = {"state": duty_core.OVERLOADED, "reason": "7 Aufgaben warten in der Schlange"}
            result = await duty_service.escalate_overload(
                db, self.redis, agent, duty, "Taeglicher KI-News-Podcast",
            )
            await db.commit()

            self.assertTrue(result)
            note = (await db.execute(select(Notification))).scalars().one()
            # War "normal" -> landete nur im Web-UI, nie in Telegram (#610).
            self.assertEqual(note.priority, "high")
            self.assertIn("Taeglicher KI-News-Podcast", note.message)
            self.assertIn("7 Aufgaben warten", note.message)
            self.assertEqual(note.meta["reason"], "duty_overload")

    async def test_reaches_telegram_not_just_the_web_ui(self):
        """#610: die Notification allein reichte nicht — ohne expliziten Publish auf
        den Telegram-Kanal erfuhr der Nutzer nie vom uebersprungenen Zeitplan."""
        async with self.Session() as db:
            db.add(self._agent("voll", "Podcast-Agent"))
            await db.commit()
            agent = (await db.execute(select(Agent).where(Agent.id == "voll"))).scalar_one()
            duty = {"state": duty_core.OVERLOADED, "reason": "ueberlastet"}
            await duty_service.escalate_overload(db, self.redis, agent, duty, "Podcast")
            await db.commit()

            self.assertEqual(len(self.redis.client.published), 1)
            channel, payload = self.redis.client.published[0]
            self.assertEqual(channel, "telegram:notification")
            self.assertIn("Podcast-Agent", payload)
            self.assertIn("Podcast", payload)

    async def test_second_run_within_the_hour_is_throttled(self):
        async with self.Session() as db:
            db.add(self._agent("voll", "Podcast-Agent"))
            await db.commit()
            agent = (await db.execute(select(Agent).where(Agent.id == "voll"))).scalar_one()
            duty = {"state": duty_core.OVERLOADED, "reason": "ueberlastet"}
            first = await duty_service.escalate_overload(db, self.redis, agent, duty, "A")
            second = await duty_service.escalate_overload(db, self.redis, agent, duty, "B")
            await db.commit()

            self.assertTrue(first)
            self.assertFalse(second)
            self.assertEqual(len((await db.execute(select(Notification))).scalars().all()), 1)

    async def test_no_deputy_search_happens(self):
        """Anders als bei escalate_failure: der Agent ist arbeitsfaehig, es gibt also
        nichts zu uebergeben — nur die Meldung, sonst nichts."""
        async with self.Session() as db:
            db.add_all([
                self._agent("voll", "Podcast-Agent"),
                self._todo("voll", "Bleibt beim Agenten"),
            ])
            await db.commit()
            agent = (await db.execute(select(Agent).where(Agent.id == "voll"))).scalar_one()
            duty = {"state": duty_core.OVERLOADED, "reason": "ueberlastet"}
            await duty_service.escalate_overload(db, self.redis, agent, duty, "A")
            await db.commit()

            still_there = (await db.execute(
                select(AgentTodo).where(AgentTodo.agent_id == "voll")
            )).scalars().all()
            self.assertEqual(len(still_there), 1)


class SkippedRunTests(DutyChainBase):
    """#632: ein wegen Ausfall uebersprungener Zeitplan-Lauf muss auffindbar bleiben.

    Der DOWN-Zweig des Schedulers kehrte zurueck, bevor ein Task entstand — der Lauf
    hinterliess nichts, in keiner Liste. Ein taeglicher Job fiel so an einem Drittel der
    Tage aus, ohne dass es irgendwo auftauchte.
    """

    SLOT = datetime(2026, 8, 21, 5, 0, tzinfo=UTC)

    async def asyncSetUp(self):
        await super().asyncSetUp()
        async with self.engine.begin() as conn:
            await conn.run_sync(Task.metadata.create_all, tables=[Task.__table__])

    async def _skip(self, db, agent, *, slot=None, schedule_id="s1"):
        return await duty_service.escalate_skipped_run(
            db, self.redis, agent, {"state": duty_core.DOWN, "reason": "Agent ist stopped"},
            schedule_id=schedule_id, schedule_name="Taeglicher Podcast",
            slot=slot or self.SLOT,
        )

    async def test_the_lost_run_becomes_a_findable_task(self):
        async with self.Session() as db:
            db.add(self._agent("tot", "Podcast-Agent", AgentState.STOPPED))
            await db.commit()
            agent = (await db.execute(select(Agent).where(Agent.id == "tot"))).scalar_one()
            self.assertTrue(await self._skip(db, agent))
            await db.commit()

            task = (await db.execute(select(Task))).scalars().one()
            self.assertEqual(task.status, TaskStatus.FAILED)
            self.assertEqual(task.agent_id, "tot")
            self.assertIn("Taeglicher Podcast", task.title)
            self.assertEqual(task.metadata_["reason"], "schedule_skipped")
            self.assertEqual(task.metadata_["schedule_id"], "s1")
            self.assertEqual(task.metadata_["duty_state"], duty_core.DOWN)
            self.assertTrue(task.error)

    async def test_the_same_slot_is_booked_only_once(self):
        """Der Zweig rueckt next_run_at nicht vor und wird jeden Tick erneut erreicht —
        ohne Merker entstuenden hunderte Eintraege fuer denselben verpassten Lauf."""
        async with self.Session() as db:
            db.add(self._agent("tot", "Podcast-Agent", AgentState.STOPPED))
            await db.commit()
            agent = (await db.execute(select(Agent).where(Agent.id == "tot"))).scalar_one()
            for _ in range(5):
                await self._skip(db, agent)
            await db.commit()
            self.assertEqual(len((await db.execute(select(Task))).scalars().all()), 1)

    async def test_the_next_days_slot_is_booked_again(self):
        """Gedrosselt wird pro Slot, nicht pro Zeitplan — sonst faellt der zweite
        Ausfalltag wieder unter den Tisch."""
        async with self.Session() as db:
            db.add(self._agent("tot", "Podcast-Agent", AgentState.STOPPED))
            await db.commit()
            agent = (await db.execute(select(Agent).where(Agent.id == "tot"))).scalar_one()
            await self._skip(db, agent)
            await self._skip(db, agent, slot=self.SLOT + timedelta(days=1))
            await db.commit()
            self.assertEqual(len((await db.execute(select(Task))).scalars().all()), 2)

    async def test_the_operator_is_paged(self):
        """#610: die Notification allein erreicht Telegram nie."""
        async with self.Session() as db:
            db.add(self._agent("tot", "Podcast-Agent", AgentState.STOPPED))
            await db.commit()
            agent = (await db.execute(select(Agent).where(Agent.id == "tot"))).scalar_one()
            await self._skip(db, agent)
            await db.commit()

            note = (await db.execute(select(Notification))).scalars().one()
            self.assertEqual(note.priority, "high")
            self.assertIn("Taeglicher Podcast", note.title)
            self.assertEqual(note.meta["reason"], "duty_skipped_run")
            self.assertEqual(len(self.redis.client.published), 1)
            self.assertEqual(self.redis.client.published[0][0], "telegram:notification")

    async def test_the_alert_does_not_use_the_agent_wide_handover_throttle(self):
        """Kern von Punkt 3: die 12h-Drossel zaehlt pro AGENT. Hat derselbe Agent aus
        einem anderen Grund schon gemeldet, verschluckt sie den Ausfall des taeglichen
        Jobs komplett — der eigene Merker haengt deshalb am Zeitplan."""
        async with self.Session() as db:
            db.add(self._agent("tot", "Podcast-Agent", AgentState.STOPPED))
            await db.commit()
            agent = (await db.execute(select(Agent).where(Agent.id == "tot"))).scalar_one()
            await duty_service.escalate_failure(db, self.redis, agent, {"reason": "tot"})
            await self._skip(db, agent)
            await db.commit()

            self.assertEqual(len((await db.execute(select(Task))).scalars().all()), 1)
            reasons = {n.meta.get("reason")
                       for n in (await db.execute(select(Notification))).scalars().all()}
            self.assertIn("duty_skipped_run", reasons)


class LostRunWordingTests(DutyChainBase):
    """#632 Punkt 2: 'es geht also nichts verloren' war die falsche Aussage genau dann,
    wenn gerade ein faelliger Lauf verloren ging."""

    async def test_a_lost_run_is_named_and_pages(self):
        async with self.Session() as db:
            db.add_all([
                self._agent("tot", "Podcast-Agent", AgentState.STOPPED,
                            deputy_agent_id="vertretung"),
                self._agent("vertretung", "Vertretung"),
            ])
            await db.commit()
            agent = (await db.execute(select(Agent).where(Agent.id == "tot"))).scalar_one()
            await duty_service.escalate_failure(
                db, self.redis, agent, {"reason": "Container gestoppt"},
                lost_run="Taeglicher Podcast",
            )
            await db.commit()

            note = (await db.execute(select(Notification))).scalars().one()
            self.assertIn("Taeglicher Podcast", note.title)
            self.assertNotIn("nichts verloren", note.message)
            self.assertEqual(note.priority, "high")
            self.assertEqual(len(self.redis.client.published), 1)


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
