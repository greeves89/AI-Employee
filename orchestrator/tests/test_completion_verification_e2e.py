"""Fertig-Meldung, Falsch-Fertig-Erkennung, Delegationsketten — gegen echtes SQL.

Kundenwunsch: "das delegieren und mitbekommmen ob fertig loopdetection ist sehr
wichtig" — proaktive Fertig-Meldung mit echtem Inhalt statt Platzhaltertext,
eine Warnung wenn die Selbstpruefung den Auftrag fuer nicht erfuellt haelt, und
dieselbe Warnung zuverlaessig ueber BEIDE Delegationswege (delegate_and_wait
UND sub_task/parent_task_id).

Wie test_self_healing_e2e: echter TaskRouter, echte In-Memory-DB, nur Redis und
der claude-CLI-Reflection-Aufruf sind Doubles — Ersteres weil es kein echtes
Redis im Testlauf gibt, Letzteres weil der eigentliche Prozessaufruf (Netzwerk,
API-Key) nicht Gegenstand dieses Tests ist (das deckt test_reflection_parse.py
bereits fuer sich ab).
"""

import json
import unittest
from datetime import timezone
from unittest.mock import AsyncMock, patch

from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy import select

from app.core.task_router import TaskRouter
from app.models.agent import Agent, AgentState
from app.models.approval_rule import ApprovalRule
from app.models.command_approval import CommandApproval
from app.models.job_state import JobState
from app.models.notification import Notification
from app.models.task import Task, TaskStatus
from app.models.task_rating import TaskRating
from app.models.task_step import TaskStep


@compiles(JSONB, "sqlite")
def _jsonb_as_json(type_, compiler, **kw):  # noqa: ANN001
    return "JSON"


UTC = timezone.utc


class _FakeRedisClient:
    """Wie test_self_healing_e2e, aber lpush/setex/publish werden mitgeschnitten —
    genau das brauchen diese Tests, um die Nachrichtentexte zu pruefen."""

    def __init__(self):
        self.lpushed: list[tuple[str, str]] = []
        self.published: list[tuple[str, str]] = []
        self.setex_calls: list[tuple[str, int, str]] = []

    async def publish(self, channel, payload):  # noqa: ANN001
        self.published.append((channel, payload))

    async def lpush(self, key, payload):  # noqa: ANN001
        self.lpushed.append((key, payload))
        return 1

    async def setex(self, key, ttl, value):  # noqa: ANN001
        self.setex_calls.append((key, ttl, value))
        return True

    async def delete(self, *a, **kw):  # noqa: ANN001, D102
        return 1

    async def lrange(self, *a, **kw):  # noqa: ANN001, D102
        return []

    def messages_to(self, agent_id: str, queue: str) -> list[dict]:
        prefix = f"agent:{agent_id}:{queue}"
        return [json.loads(p) for k, p in self.lpushed if k == prefix]


class _FakeRedis:
    def __init__(self):
        self.client = _FakeRedisClient()
        self.pushed: list[tuple[str, str]] = []

    async def push_task(self, agent_id, payload):  # noqa: ANN001
        self.pushed.append((agent_id, payload))


class _FakeLoadBalancer:
    async def select_agent(self, priority=1):  # noqa: ANN001
        return None


class CompletionVerificationE2E(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            for model in (Agent, Task, Notification, CommandApproval, ApprovalRule,
                          TaskRating, TaskStep, JobState):
                await conn.run_sync(model.metadata.create_all, tables=[model.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        self.redis = _FakeRedis()

    async def asyncTearDown(self):
        await self.engine.dispose()

    def _router(self, db):
        return TaskRouter(db, self.redis, _FakeLoadBalancer())

    async def _seed_agent(self, db, agent_id="a1", **kw):
        agent = Agent(
            id=agent_id, name="Mitarbeiter", state=AgentState.RUNNING,
            user_id="u1", config=kw.pop("config", {}), **kw,
        )
        db.add(agent)
        await db.commit()
        return agent

    def _reflect_mock(self, fulfilled, gap="", rating=4, reflection="ok"):
        return AsyncMock(return_value=(rating, reflection, fulfilled, gap))

    # ── 1. Proaktive Meldung traegt den echten Inhalt (Befund 1) ───────────

    async def test_rating_notification_carries_the_result_not_a_placeholder(self):
        async with self.Session() as db:
            await self._seed_agent(db)
            task = Task(
                id="t1", title="Monatsbericht", prompt="Erstelle den Bericht.",
                status=TaskStatus.RUNNING, agent_id="a1",
            )
            db.add(task)
            await db.commit()

            with patch("app.core.task_router._llm_reflect_on_task", new=self._reflect_mock(True)):
                await self._router(db).handle_task_completion({
                    "task_id": "t1", "agent_id": "a1", "status": "completed",
                    "result": "Bericht ist unter /shared/x.pdf",
                })

            notifs = (await db.execute(select(Notification))).scalars().all()
            rating_notifs = [n for n in notifs if (n.meta or {}).get("type") == "rating_request"]
            self.assertEqual(len(rating_notifs), 1)
            self.assertIn("Bericht ist unter /shared/x.pdf", rating_notifs[0].message)
            self.assertNotIn("Wie war das Ergebnis?", rating_notifs[0].message)

    async def test_fulfilled_false_prefixes_the_notification_with_a_warning(self):
        async with self.Session() as db:
            await self._seed_agent(db)
            task = Task(
                id="t1", title="Kuendigungsschreiben", prompt="Schreibe die Kuendigung.",
                status=TaskStatus.RUNNING, agent_id="a1",
            )
            db.add(task)
            await db.commit()

            mock = self._reflect_mock(False, gap="Nur angekuendigt, nie geliefert.")
            with patch("app.core.task_router._llm_reflect_on_task", new=mock):
                await self._router(db).handle_task_completion({
                    "task_id": "t1", "agent_id": "a1", "status": "completed",
                    "result": "Ich kuemmere mich gleich darum.",
                })

            notifs = (await db.execute(select(Notification))).scalars().all()
            rating_notifs = [n for n in notifs if (n.meta or {}).get("type") == "rating_request"]
            self.assertEqual(len(rating_notifs), 1)
            self.assertIn("Selbstpruefung unsicher", rating_notifs[0].message)
            self.assertIn("Nur angekuendigt, nie geliefert.", rating_notifs[0].message)
            self.assertEqual(rating_notifs[0].type, "warning")

    async def test_fulfilled_none_does_not_warn(self):
        """Kein Urteil (CLI-Fallback) darf NIE wie 'nicht erfuellt' aussehen."""
        async with self.Session() as db:
            await self._seed_agent(db)
            task = Task(
                id="t1", title="x", prompt="y", status=TaskStatus.RUNNING, agent_id="a1",
            )
            db.add(task)
            await db.commit()

            with patch("app.core.task_router._llm_reflect_on_task", new=self._reflect_mock(None)):
                await self._router(db).handle_task_completion({
                    "task_id": "t1", "agent_id": "a1", "status": "completed", "result": "fertig",
                })

            notifs = (await db.execute(select(Notification))).scalars().all()
            rating_notifs = [n for n in notifs if (n.meta or {}).get("type") == "rating_request"]
            self.assertNotIn("Selbstpruefung unsicher", rating_notifs[0].message)
            self.assertEqual(rating_notifs[0].type, "info")

    async def test_a_failed_auto_rate_commit_does_not_poison_the_rest_of_completion(self):
        """Live bei einer Kundenanlage beobachtet (2026-09-12, Schema-Drift-Fenster: Code lief
        knapp vor seiner eigenen Migration): der TaskRating-Commit in
        _auto_rate_task schlug fehl, und OHNE Rollback blieb die Session
        kaputt — die direkt danach laufende Rating-Notification (gleiche
        Session!) scheiterte reihum mit 'transaction has been rolled back due
        to a previous exception during flush'. Der Rollback im except-Zweig
        muss genau das verhindern.

        Ausgeloest hier ueber eine ECHTE Constraint-Verletzung (rating ist
        NOT NULL) statt eines gemockten Commits — damit durchlaeuft der Test
        denselben echten Flush/Rollback-Pfad wie die Produktion, statt
        SQLAlchemys Greenlet-Async-Bruecke mit einem kuenstlichen Fehler zu
        verwirren."""
        async with self.Session() as db:
            await self._seed_agent(db)
            task = Task(
                id="t1", title="x", prompt="y", status=TaskStatus.RUNNING, agent_id="a1",
            )
            db.add(task)
            await db.commit()

            broken_mock = self._reflect_mock(True)
            broken_mock.return_value = (None, "ok", True, "")  # rating=None verletzt NOT NULL

            with patch("app.core.task_router._llm_reflect_on_task", new=broken_mock):
                await self._router(db).handle_task_completion({
                    "task_id": "t1", "agent_id": "a1", "status": "completed", "result": "fertig",
                })

            # Kein TaskRating (der eine Commit, der fehlschlug) — aber die
            # Notification danach (ein SPAETERER Commit derselben Session)
            # muss trotzdem angekommen sein.
            ratings = (await db.execute(select(TaskRating))).scalars().all()
            self.assertEqual(ratings, [])
            notifs = (await db.execute(select(Notification))).scalars().all()
            rating_notifs = [n for n in notifs if (n.meta or {}).get("type") == "rating_request"]
            self.assertEqual(
                len(rating_notifs), 1,
                "Die Fertig-Notification muss den Session-Fehler ueberleben",
            )

    # ── 2. TaskRating persistiert fulfilled/gap (Befund 2) ──────────────────

    async def test_fulfilled_and_gap_are_persisted_on_the_rating(self):
        async with self.Session() as db:
            await self._seed_agent(db)
            task = Task(
                id="t1", title="x", prompt="y", status=TaskStatus.RUNNING, agent_id="a1",
            )
            db.add(task)
            await db.commit()

            mock = self._reflect_mock(False, gap="Nur die Haelfte geliefert.")
            with patch("app.core.task_router._llm_reflect_on_task", new=mock):
                await self._router(db).handle_task_completion({
                    "task_id": "t1", "agent_id": "a1", "status": "completed",
                    "result": "halb fertig",
                })

            rating = (await db.execute(
                select(TaskRating).where(TaskRating.task_id == "t1")
            )).scalar_one()
            self.assertFalse(rating.fulfilled)
            self.assertEqual(rating.gap, "Nur die Haelfte geliefert.")

    # ── 3. Delegationsweg delegate_and_wait / created_by_agent (Befund 3a) ──

    async def test_delegating_agent_gets_the_fulfilled_warning_in_chat(self):
        async with self.Session() as db:
            await self._seed_agent(db, agent_id="lead")
            await self._seed_agent(db, agent_id="worker")
            task = Task(
                id="t1", title="Recherche", prompt="Recherchiere X.",
                status=TaskStatus.RUNNING, agent_id="worker",
                metadata_={"created_by_agent": "lead", "chat_session_id": "s1"},
            )
            db.add(task)
            await db.commit()

            mock = self._reflect_mock(False, gap="Recherche wurde abgebrochen statt beendet.")
            with patch("app.core.task_router._llm_reflect_on_task", new=mock):
                await self._router(db).handle_task_completion({
                    "task_id": "t1", "agent_id": "worker", "status": "completed",
                    "result": "Nichts gefunden, aufgegeben.",
                })

            chat_msgs = self.redis.client.messages_to("lead", "chat")
            self.assertEqual(len(chat_msgs), 1)
            self.assertIn("ACHTUNG", chat_msgs[0]["text"])
            self.assertIn("Nichts gefunden, aufgegeben.", chat_msgs[0]["text"])
            self.assertIn("Recherche wurde abgebrochen statt beendet.", chat_msgs[0]["text"])
            self.assertEqual(chat_msgs[0]["chat_session_id"], "s1")

    async def test_delegating_agent_gets_no_warning_when_fulfilled(self):
        async with self.Session() as db:
            await self._seed_agent(db, agent_id="lead")
            await self._seed_agent(db, agent_id="worker")
            task = Task(
                id="t1", title="Recherche", prompt="Recherchiere X.",
                status=TaskStatus.RUNNING, agent_id="worker",
                metadata_={"created_by_agent": "lead", "chat_session_id": "s1"},
            )
            db.add(task)
            await db.commit()

            with patch("app.core.task_router._llm_reflect_on_task", new=self._reflect_mock(True)):
                await self._router(db).handle_task_completion({
                    "task_id": "t1", "agent_id": "worker", "status": "completed",
                    "result": "X gefunden: siehe Anhang.",
                })

            chat_msgs = self.redis.client.messages_to("lead", "chat")
            self.assertEqual(len(chat_msgs), 1)
            self.assertNotIn("ACHTUNG", chat_msgs[0]["text"])
            self.assertIn("X gefunden: siehe Anhang.", chat_msgs[0]["text"])

    # ── 4. Delegationsweg sub_task / parent_task_id (Befund 3b) ─────────────

    async def test_parent_agent_gets_a_chat_message_only_when_all_subtasks_are_done(self):
        """Vorher schrieb _notify_parent_agent NUR in :messages, nie in :chat —
        der Elternagent konnte die Fertigmeldung verpassen, wenn er sie im
        naechsten Zug nicht von sich aus aufgriff. Ausserdem: nur EINE
        Chat-Nachricht fuer den ganzen Stapel, kein Spam pro Subtask."""
        async with self.Session() as db:
            await self._seed_agent(db, agent_id="parent")
            await self._seed_agent(db, agent_id="worker")
            parent = Task(
                id="p1", title="Sammelauftrag", prompt="Erledige alles.",
                status=TaskStatus.RUNNING, agent_id="parent",
                metadata_={"chat_session_id": "s1"},
            )
            sub1 = Task(
                id="s1t", title="Teil 1", prompt="Teil 1.", status=TaskStatus.RUNNING,
                agent_id="worker", parent_task_id="p1",
            )
            sub2 = Task(
                id="s2t", title="Teil 2", prompt="Teil 2.", status=TaskStatus.COMPLETED,
                agent_id="worker", parent_task_id="p1", result="Teil 2 erledigt.",
            )
            db.add_all([parent, sub1, sub2])
            await db.commit()

            with patch("app.core.task_router._llm_reflect_on_task", new=self._reflect_mock(True)):
                await self._router(db).handle_task_completion({
                    "task_id": "s1t", "agent_id": "worker", "status": "completed",
                    "result": "Teil 1 erledigt.",
                })

            # Nach dem ERSTEN Subtask sind noch nicht alle Geschwister fertig
            # (sub2 wurde absichtlich schon vorher auf COMPLETED gesetzt, damit
            # dieser Aufruf den Batch abschliesst) — die Chat-Nachricht kommt
            # also GENAU jetzt, einmalig.
            chat_msgs = self.redis.client.messages_to("parent", "chat")
            self.assertEqual(len(chat_msgs), 1)
            self.assertIn("Berichte dem Menschen kurz", chat_msgs[0]["text"])
            self.assertEqual(chat_msgs[0]["chat_session_id"], "s1")

            queue_msgs = self.redis.client.messages_to("parent", "messages")
            all_done = [m for m in queue_msgs if m.get("type") == "all_subtasks_completed"]
            self.assertEqual(len(all_done), 1)

    async def test_parent_agent_warning_lists_the_unfulfilled_subtask(self):
        async with self.Session() as db:
            await self._seed_agent(db, agent_id="parent")
            await self._seed_agent(db, agent_id="worker")
            parent = Task(
                id="p1", title="Sammelauftrag", prompt="Erledige alles.",
                status=TaskStatus.RUNNING, agent_id="parent",
                metadata_={"chat_session_id": "s1"},
            )
            sub1 = Task(
                id="s1t", title="Teil A", prompt="Teil A.", status=TaskStatus.RUNNING,
                agent_id="worker", parent_task_id="p1",
            )
            sub2 = Task(
                id="s2t", title="Teil B", prompt="Teil B.", status=TaskStatus.COMPLETED,
                agent_id="worker", parent_task_id="p1", result="Teil B erledigt.",
            )
            db.add_all([parent, sub1, sub2])
            # Teil B wurde schon frueher fertig gemeldet und bewertet — genau
            # der Fall, den der Batch-Zweig aus TaskRating nachladen muss, weil
            # der aktuell abschliessende Aufruf nur das Urteil fuer Teil A kennt.
            db.add(TaskRating(
                task_id="s2t", agent_id="worker", rating=5, comment="ok",
                fulfilled=True, gap=None,
            ))
            await db.commit()

            mock = self._reflect_mock(False, gap="Nur begonnen, nicht beendet.")
            with patch("app.core.task_router._llm_reflect_on_task", new=mock):
                await self._router(db).handle_task_completion({
                    "task_id": "s1t", "agent_id": "worker", "status": "completed",
                    "result": "Teil A nur angefangen.",
                })

            chat_msgs = self.redis.client.messages_to("parent", "chat")
            self.assertEqual(len(chat_msgs), 1)
            self.assertIn("ACHTUNG", chat_msgs[0]["text"])
            self.assertIn("Teil A", chat_msgs[0]["text"])
            self.assertIn("Nur begonnen, nicht beendet.", chat_msgs[0]["text"])
            # Teil B war erfuellt — darf nicht in der Mangel-Liste auftauchen.
            self.assertNotIn("Teil B", chat_msgs[0]["text"].split("ACHTUNG")[1].split(".")[0])

    async def test_parent_agent_gets_no_chat_message_while_siblings_are_still_running(self):
        async with self.Session() as db:
            await self._seed_agent(db, agent_id="parent")
            await self._seed_agent(db, agent_id="worker")
            parent = Task(
                id="p1", title="Sammelauftrag", prompt="Erledige alles.",
                status=TaskStatus.RUNNING, agent_id="parent",
                metadata_={"chat_session_id": "s1"},
            )
            sub1 = Task(
                id="s1t", title="Teil 1", prompt="Teil 1.", status=TaskStatus.RUNNING,
                agent_id="worker", parent_task_id="p1",
            )
            sub2 = Task(
                id="s2t", title="Teil 2", prompt="Teil 2.", status=TaskStatus.RUNNING,
                agent_id="worker", parent_task_id="p1",
            )
            db.add_all([parent, sub1, sub2])
            await db.commit()

            with patch("app.core.task_router._llm_reflect_on_task", new=self._reflect_mock(True)):
                await self._router(db).handle_task_completion({
                    "task_id": "s1t", "agent_id": "worker", "status": "completed",
                    "result": "Teil 1 erledigt.",
                })

            self.assertEqual(self.redis.client.messages_to("parent", "chat"), [])


if __name__ == "__main__":
    unittest.main()
