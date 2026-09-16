"""Ein Agent muss sein eigenes veraltetes Wissen entschaerfen koennen, nicht
nur loeschen (Issue #704).

Gemessen am 05.09.2026: 129 ``PUT /api/v1/memory/{id}``-Aufrufe eines Agenten
(``{"importance": 3}``) endeten alle mit 401, waehrend GETs mit demselben
Token funktionierten. Genau dieselbe Bug-Klasse wie bei
``test_agent_can_forget.py`` (dort fuer DELETE behoben) — ``update_memory``
hing weiter an ``require_auth``, einem reinen NUTZER-Login, obwohl
``_assert_agent_access`` den Agenten-Fall laengst kennt.

Die einzige fuer den Agenten erreichbare Selbstpflege war damit die
unumkehrbare (Loeschen). Wer eine Erinnerung nur herabstufen/korrigieren
wollte, musste sie loeschen und neu anlegen — und fuellte damit unnoetig den
Eintrags-Deckel des Vorspanns.
"""

import unittest
from types import SimpleNamespace

from app.api import memory as api
from app.dependencies import AgentPrincipal
from app.models.agent import Agent, AgentState
from app.models.memory import AgentMemory, AgentMemoryLink, AgentMemoryTag
from app.models.user import UserRole
from fastapi import HTTPException
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles


@compiles(JSONB, "sqlite")
def _jsonb_as_json(type_, compiler, **kw):
    return "JSON"


class AnAgentDowngradingItsOwnMemoryTests(unittest.IsolatedAsyncioTestCase):
    VERALTET = "AI Dev Team: Reviewer (id: 6e42) = Senior Code Reviewer"

    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            for model in (Agent, AgentMemory, AgentMemoryTag, AgentMemoryLink):
                await conn.run_sync(model.metadata.create_all, tables=[model.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.Session() as db:
            db.add(Agent(id="a1", name="Erster", state=AgentState.RUNNING, user_id="u1", config={}))
            db.add(Agent(id="a2", name="Zweiter", state=AgentState.RUNNING, user_id="u2", config={}))
            db.add(AgentMemory(id=7, agent_id="a1", category="fact", key="team",
                               content=self.VERALTET, importance=5))
            db.add(AgentMemory(id=8, agent_id="a2", category="fact", key="team",
                               content="Notiz eines fremden Agenten", importance=5))
            await db.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    @staticmethod
    def _agent(agent_id):
        return AgentPrincipal(id=agent_id, username=f"agent-{agent_id}")

    async def test_the_agent_can_downgrade_its_own_memory(self):
        """Das war der gemeldete Fehler: hier kam 401 statt einer Aktualisierung."""
        async with self.Session() as db:
            antwort = await api.update_memory(
                7, api.MemoryUpdate(importance=3), user=self._agent("a1"), db=db,
            )
        self.assertEqual(antwort["importance"], 3)

    async def test_the_agent_cannot_downgrade_a_colleagues_memory(self):
        """Der geoeffnete Weg darf die Mandantentrennung nicht mitreissen."""
        async with self.Session() as db:
            with self.assertRaises(HTTPException) as fall:
                await api.update_memory(8, api.MemoryUpdate(importance=1), user=self._agent("a1"), db=db)
        self.assertEqual(fall.exception.status_code, 403)

    async def test_the_owner_can_still_update_from_the_web_ui(self):
        """Der bisherige Weg muss unveraendert bleiben."""
        nutzer = SimpleNamespace(id="u1", role=UserRole.MEMBER, email="u1@example.test")
        async with self.Session() as db:
            antwort = await api.update_memory(7, api.MemoryUpdate(importance=2), user=nutzer, db=db)
        self.assertEqual(antwort["importance"], 2)

    async def test_a_stranger_still_gets_nothing(self):
        fremder = SimpleNamespace(id="u9", role=UserRole.MEMBER, email="u9@example.test")
        async with self.Session() as db:
            with self.assertRaises(HTTPException) as fall:
                await api.update_memory(7, api.MemoryUpdate(importance=1), user=fremder, db=db)
        self.assertEqual(fall.exception.status_code, 403)


class TheRouteAcceptsBothKindsOfCallerTests(unittest.TestCase):
    """Die Abhaengigkeit selbst ist der Fehler gewesen — ein Test auf das
    Verhalten oben ruft die Funktion direkt auf und wuerde einen Rueckfall auf
    ``require_auth`` nicht bemerken."""

    def test_update_does_not_hang_on_a_user_only_login(self):
        from app.dependencies import require_auth, require_auth_or_agent
        route = next(r for r in api.router.routes
                     if getattr(r, "path", None) == "/memory/{memory_id}" and "PUT" in getattr(r, "methods", ()))
        abhaengigkeiten = [d.call for d in route.dependant.dependencies]
        self.assertIn(require_auth_or_agent, abhaengigkeiten)
        self.assertNotIn(require_auth, abhaengigkeiten)


if __name__ == "__main__":
    unittest.main()
