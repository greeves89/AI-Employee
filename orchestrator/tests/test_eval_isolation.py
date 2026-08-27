"""Golden-Test-Läufe sind mandantengetrennt (#391).

Harte Vorgabe: die App ist userbased. Der Fall, den dieser Test festnagelt, ist der
gefaehrlichste an der ganzen Sache — ``GET /evals/runs`` OHNE ``agent_id``. Der
Besitzcheck hing zuerst im ``if agent_id:``-Zweig; wer den Parameter weglaesst,
bekam damit die Laeufe aller Nutzer, samt Auftragstexten und Antwortauszuegen.

Geprueft wird die echte Endpunktfunktion gegen echtes SQL.
"""

import unittest
from types import SimpleNamespace

from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from app.api.evals import list_runs
from app.models.agent import Agent, AgentState
from app.models.eval_set import EvalRun, EvalSet


@compiles(JSONB, "sqlite")
def _jsonb_as_json(type_, compiler, **kw):  # noqa: ANN001
    return "JSON"


def _user(uid: str, role: str = "user"):
    return SimpleNamespace(id=uid, role=role, email=f"{uid}@example.test")


class EvalIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            for model in (Agent, EvalSet, EvalRun):
                await conn.run_sync(model.metadata.create_all, tables=[model.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

        async with self.Session() as db:
            db.add(Agent(id="a-anna", name="Annas Agent", state=AgentState.RUNNING,
                         user_id="anna", config={}))
            db.add(Agent(id="a-bob", name="Bobs Agent", state=AgentState.RUNNING,
                         user_id="bob", config={}))
            db.add(Agent(id="a-platform", name="Plattform", state=AgentState.RUNNING,
                         user_id=None, config={}))
            db.add(Agent(id="a-flagged", name="Deliberate Plattform-Agent", state=AgentState.RUNNING,
                         user_id=None, is_platform_agent=True, config={}))
            db.add(EvalSet(id="es-anna", name="Annas Tests", items=[], user_id="anna"))
            db.add(EvalSet(id="es-bob", name="Bobs Tests", items=[], user_id="bob"))
            for run_id, agent_id, set_id in (
                ("ev-anna", "a-anna", "es-anna"),
                ("ev-bob", "a-bob", "es-bob"),
                ("ev-platform", "a-platform", "es-anna"),
                ("ev-flagged", "a-flagged", "es-anna"),
            ):
                db.add(EvalRun(id=run_id, set_id=set_id, agent_id=agent_id,
                               status="completed", score=90.0, total=1, passed=1,
                               results=[]))
            await db.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _runs(self, user, **kw):
        async with self.Session() as db:
            out = await list_runs(user=user, db=db, limit=50, **{
                "agent_id": kw.get("agent_id"), "set_id": kw.get("set_id"),
            })
        return {r["id"] for r in out["runs"]}

    async def test_unfiltered_listing_never_leaks_other_users(self):
        """Der Fall, in dem der bedingte Besitzcheck versagte."""
        self.assertNotIn("ev-bob", await self._runs(_user("anna")))

    async def test_own_runs_are_visible(self):
        self.assertIn("ev-anna", await self._runs(_user("anna")))

    async def test_ownerless_agents_are_hidden_from_regular_users(self):
        """Umgekehrt seit 2026-08-27 (gemeldet bei einem Mehr-Abteilungen-Kunden):
        ein Agent ohne zugewiesenen Besitzer ist NICHT mehr automatisch
        "fuer alle" — sonst sieht jede neue Abteilung jede andere, bis jemand
        die Zuweisung nachtraegt. Admins sehen ihn weiterhin (naechster Test),
        so kann er zugewiesen werden."""
        self.assertNotIn("ev-platform", await self._runs(_user("anna")))
        self.assertNotIn("ev-platform", await self._runs(_user("bob")))

    async def test_explicitly_flagged_platform_agents_stay_visible(self):
        """Der Ersatz fuer den alten Automatismus: is_platform_agent ist eine
        bewusste Admin-Markierung, kein NULL-Nebeneffekt — die bleibt fuer
        alle sichtbar, genau wie frueher gewollt war."""
        self.assertIn("ev-flagged", await self._runs(_user("anna")))
        self.assertIn("ev-flagged", await self._runs(_user("bob")))

    async def test_admin_sees_everything(self):
        self.assertEqual(
            await self._runs(_user("root", role="admin")),
            {"ev-anna", "ev-bob", "ev-platform", "ev-flagged"},
        )

    async def test_asking_for_a_foreign_agent_is_refused(self):
        from fastapi import HTTPException

        with self.assertRaises(HTTPException) as ctx:
            await self._runs(_user("anna"), agent_id="a-bob")
        self.assertEqual(ctx.exception.status_code, 403)

    async def test_asking_for_a_foreign_set_is_refused(self):
        """Ohne diesen Check waere die Sammlung ein Umweg um den Agentencheck."""
        from fastapi import HTTPException

        with self.assertRaises(HTTPException) as ctx:
            await self._runs(_user("anna"), set_id="es-bob")
        self.assertEqual(ctx.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
