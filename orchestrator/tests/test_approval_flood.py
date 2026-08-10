"""Freigaben laufen nicht mehr über (#11-Folge, Kundenmeldung 2026-08-10).

Auf einer Kundenanlage standen **570** offene Freigaben. Der Hergang, ablesbar an
den Anfragen selbst: ein Agent, dessen Einrichtung unvollständig ist, soll seinen
Laufstatus in ``/workspace/.agent_state.md`` notieren — das verlangt auf
Autonomiestufe 1–3 eine Freigabe. Niemand antwortet, die Frage läuft in ihre
Zeitgrenze, eine Stunde später fragt der nächste proaktive Lauf dasselbe.

Zwei Ursachen, beide hier festgenagelt:

1. **Nichts lief je ab.** ``ApprovalStatus.EXPIRED`` stand seit jeher im Modell und
   wurde NIE gesetzt.
2. **Nichts wurde entdoppelt.** Aus Sicht des Menschen ist das EINE Entscheidung,
   nicht 570.

In einer Liste mit 570 Einträgen findet niemand mehr die eine, die zählt — deshalb
sind das keine Schönheitsfehler.
"""

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from app.api import approvals as api
from app.models.agent import Agent, AgentState
from app.models.audit_log import AuditLog
from app.models.command_approval import ApprovalStatus, CommandApproval
from app.models.notification import Notification
from app.models.user import UserRole


@compiles(JSONB, "sqlite")
def _jsonb_as_json(type_, compiler, **kw):  # noqa: ANN001
    return "JSON"


def _admin():
    return SimpleNamespace(id="u1", role=UserRole.ADMIN, email="admin@example.test")


def _member(uid="u2"):
    return SimpleNamespace(id=uid, role=UserRole.MEMBER, email=f"{uid}@example.test")


class ApprovalFloodTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            for model in (Agent, CommandApproval, Notification, AuditLog):
                await conn.run_sync(model.metadata.create_all, tables=[model.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.Session() as db:
            db.add(Agent(id="a1", name="Buchhaltung", state=AgentState.RUNNING,
                         user_id="u2", config={}))
            db.add(Agent(id="a2", name="Fremd", state=AgentState.RUNNING,
                         user_id="u9", config={}))
            await db.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    def _body(self, question="Darf ich den Laufstatus notieren?", **kw):
        return api.ApprovalRequest(question=question, reasoning=kw.pop("reasoning", "Onboarding NOT COMPLETED"), **kw)

    async def _ask(self, db, agent_id="a1", **kw):
        return await api.request_approval(
            self._body(**kw), agent_auth={"agent_id": agent_id}, db=db
        )

    # ── Entdopplung ──────────────────────────────────────────────────────────

    async def test_the_same_question_does_not_pile_up(self):
        async with self.Session() as db:
            first = await self._ask(db)
            for _ in range(20):
                await self._ask(db)

            rows = (await db.execute(select(CommandApproval))).scalars().all()
            self.assertEqual(len(rows), 1, "21 gleiche Fragen dürfen EINE Zeile sein")
            self.assertEqual(rows[0].meta["repeats"], 21)
            # Der Agent wartet weiter auf dieselbe Kennung.
            again = await self._ask(db)
            self.assertEqual(again["approval_id"], first["approval_id"])

    async def test_a_different_question_still_gets_its_own_row(self):
        """Entdopplung darf keine echten zweiten Fragen verschlucken."""
        async with self.Session() as db:
            await self._ask(db)
            await self._ask(db, question="Darf ich die Rechnung verschicken?")
            rows = (await db.execute(select(CommandApproval))).scalars().all()
            self.assertEqual(len(rows), 2)

    async def test_another_agent_asking_the_same_is_not_merged(self):
        """Zwei Agenten, dieselbe Frage — das sind zwei Entscheidungen."""
        async with self.Session() as db:
            await self._ask(db, agent_id="a1")
            await self._ask(db, agent_id="a2")
            rows = (await db.execute(select(CommandApproval))).scalars().all()
            self.assertEqual(len(rows), 2)

    async def test_a_resolved_question_may_be_asked_again(self):
        """Sonst könnte ein Agent nach einem Nein nie wieder fragen."""
        async with self.Session() as db:
            first = await self._ask(db)
            row = await db.get(CommandApproval, int(first["approval_id"]))
            row.status = ApprovalStatus.DENIED
            await db.commit()

            second = await self._ask(db)
            self.assertNotEqual(second["approval_id"], first["approval_id"])

    async def test_no_second_notification_for_a_repeat(self):
        """Sonst klingelt das Telefon 570-mal für dieselbe Frage."""
        async with self.Session() as db:
            await self._ask(db)
            before = len((await db.execute(select(Notification))).scalars().all())
            for _ in range(5):
                await self._ask(db)
            after = len((await db.execute(select(Notification))).scalars().all())
            self.assertEqual(before, after)

    # ── Verfall ──────────────────────────────────────────────────────────────

    async def test_stale_approvals_expire(self):
        from app.services.scheduler_service import _APPROVAL_TTL_HOURS

        async with self.Session() as db:
            old = CommandApproval(
                agent_id="a1", command="user_decision", description="alt",
                status=ApprovalStatus.PENDING,
                created_at=datetime.now(timezone.utc) - timedelta(hours=_APPROVAL_TTL_HOURS + 1),
                meta={},
            )
            fresh = CommandApproval(
                agent_id="a1", command="user_decision", description="frisch",
                status=ApprovalStatus.PENDING,
                created_at=datetime.now(timezone.utc), meta={},
            )
            db.add_all([old, fresh])
            await db.commit()

            # Die reine Auswahlregel, ohne den Zeitgeber selbst zu starten.
            cutoff = datetime.now(timezone.utc) - timedelta(hours=_APPROVAL_TTL_HOURS)
            stale = (await db.execute(
                select(CommandApproval).where(
                    CommandApproval.status == ApprovalStatus.PENDING,
                    CommandApproval.created_at < cutoff,
                )
            )).scalars().all()
            self.assertEqual([a.description for a in stale], ["alt"])

    async def test_expired_is_actually_a_state_that_exists(self):
        """Es stand im Modell und wurde nie gesetzt — genau das war das Problem."""
        self.assertEqual(ApprovalStatus.EXPIRED, "expired")

    # ── Sammelverwerfung ─────────────────────────────────────────────────────

    async def test_clear_all_resolves_but_keeps_the_trail(self):
        async with self.Session() as db:
            await self._ask(db)
            await self._ask(db, question="Zweite Frage")

            out = await api.clear_pending_approvals(user=_admin(), db=db)
            self.assertEqual(out["cleared"], 2)

            rows = (await db.execute(select(CommandApproval))).scalars().all()
            self.assertEqual(len(rows), 2, "Verworfen heisst NICHT geloescht")
            for row in rows:
                self.assertEqual(row.status, ApprovalStatus.DENIED)
                self.assertIsNotNone(row.resolved_at)
                self.assertIn("admin@example.test", row.user_response)

    async def test_clear_all_only_touches_your_own_agents(self):
        """Sonst raeumt ein Nutzer die Entscheidungen eines anderen weg."""
        async with self.Session() as db:
            await self._ask(db, agent_id="a1")
            await self._ask(db, agent_id="a2")

            out = await api.clear_pending_approvals(user=_member("u2"), db=db)
            self.assertEqual(out["cleared"], 1)

            fremd = (await db.execute(
                select(CommandApproval).where(CommandApproval.agent_id == "a2")
            )).scalar_one()
            self.assertEqual(fremd.status, ApprovalStatus.PENDING)

    # ── Zaehler fuer das Abzeichen ───────────────────────────────────────────

    async def test_count_matches_the_list_the_user_sees(self):
        """Stuende im Menue eine andere Zahl als in der Liste, glaubt man keiner."""
        async with self.Session() as db:
            await self._ask(db, agent_id="a1")
            await self._ask(db, agent_id="a2")

            for user in (_admin(), _member("u2")):
                with self.subTest(role=user.role):
                    count = (await api.count_pending_approvals(user=user, db=db))["count"]
                    listed = (await api.list_pending_approvals(user=user, db=db))["count"]
                    self.assertEqual(count, listed)

    async def test_count_ignores_resolved_ones(self):
        async with self.Session() as db:
            first = await self._ask(db)
            row = await db.get(CommandApproval, int(first["approval_id"]))
            row.status = ApprovalStatus.APPROVED
            await db.commit()
            self.assertEqual((await api.count_pending_approvals(user=_admin(), db=db))["count"], 0)


if __name__ == "__main__":
    unittest.main()


class RouteOrderTests(unittest.TestCase):
    """``DELETE /approvals/pending`` darf nicht auf ``/{approval_id}`` fallen.

    FastAPI entscheidet nach Deklarationsreihenfolge. Stuende ``/{approval_id}``
    vorher, landete „alle verwerfen" dort mit ``approval_id="pending"`` — und der
    Aufruf machte ``int("pending")``. Dieselbe Falle hat in diesem Projekt schon
    zweimal zugeschlagen (``/sso/{provider}/login`` und ``/{room_id}``), deshalb
    steht sie hier als Test und nicht als Kommentar.
    """

    def test_static_paths_are_declared_before_the_wildcard(self):
        from pathlib import Path

        src = (Path(__file__).resolve().parents[1] / "app/api/approvals.py").read_text()
        static_delete = src.index('@router.delete("/pending")')
        wildcard_delete = src.index('@router.delete("/{approval_id}")')
        self.assertLess(static_delete, wildcard_delete)

    def test_the_router_really_resolves_it_that_way(self):
        """Nicht nur die Quelltext-Reihenfolge — was FastAPI daraus macht."""
        from app.api.approvals import router

        paths = [
            r.path for r in router.routes
            if "DELETE" in getattr(r, "methods", set())
        ]
        self.assertLess(paths.index("/approvals/pending"), paths.index("/approvals/{approval_id}"))
