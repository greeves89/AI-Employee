"""Ein Agent muss sein eigenes veraltetes Wissen loeschen koennen.

Nutzerbericht vom 18.08.2026, mit Bildschirmfoto: ein Agent stellte fest, dass
sein gespeicherter Team-Zettel einen Kollegen nennt, den es nicht mehr gibt,
wollte die vier Notizen wegraeumen — und bekam vier Mal
``401 Invalid or expired token``. Im Orchestrator-Log der Kundenkiste stand es
genauso:

    DELETE /api/v1/memory/7   401 Unauthorized
    DELETE /api/v1/memory/16  401 Unauthorized
    DELETE /api/v1/memory/22  401 Unauthorized
    DELETE /api/v1/memory/373 401 Unauthorized

Ursache: der Loeschweg hing an ``require_auth`` — einem reinen NUTZER-Login.
Speichern (``/memory/save``) und Auflisten (``/memory/agents/{id}``) liessen den
Agenten laengst durch, nur Loeschen nicht. Das Werkzeug ``memory_delete`` stand
also in allen vier Laufzeiten im Katalog und hat nie funktioniert.

Der Besitz-Schild ``_assert_agent_access`` kannte den Agenten-Fall die ganze
Zeit; er wurde nur nie erreicht. Deshalb pruefen die Tests hier BEIDES: dass der
Agent seine eigene Notiz loeschen kann, und dass er an fremde nicht herankommt.
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


class AnAgentCleaningUpAfterItselfTests(unittest.IsolatedAsyncioTestCase):
    #: Woertlich die veraltete Notiz aus dem Bericht, ohne die echten Kennungen.
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

    async def _noch_da(self, memory_id):
        async with self.Session() as db:
            return await db.get(AgentMemory, memory_id) is not None

    async def test_the_agent_can_delete_its_own_memory(self):
        """Das war der gemeldete Fehler: hier kam 401 statt einer Loeschung."""
        async with self.Session() as db:
            antwort = await api.delete_memory(7, user=self._agent("a1"), db=db)
        self.assertEqual(antwort, {"deleted": 7})
        self.assertFalse(await self._noch_da(7))

    async def test_the_agent_cannot_delete_a_colleagues_memory(self):
        """Erinnerungen tragen Gelerntes und manchmal Zugaenge — der geoeffnete
        Weg darf die Mandantentrennung nicht mitreissen."""
        async with self.Session() as db:
            with self.assertRaises(HTTPException) as fall:
                await api.delete_memory(8, user=self._agent("a1"), db=db)
        self.assertEqual(fall.exception.status_code, 403)
        self.assertTrue(await self._noch_da(8))

    async def test_the_owner_can_still_delete_from_the_web_ui(self):
        """Der bisherige Weg muss unveraendert bleiben."""
        nutzer = SimpleNamespace(id="u1", role=UserRole.MEMBER, email="u1@example.test")
        async with self.Session() as db:
            await api.delete_memory(7, user=nutzer, db=db)
        self.assertFalse(await self._noch_da(7))

    async def test_a_stranger_still_gets_nothing(self):
        fremder = SimpleNamespace(id="u9", role=UserRole.MEMBER, email="u9@example.test")
        async with self.Session() as db:
            with self.assertRaises(HTTPException) as fall:
                await api.delete_memory(7, user=fremder, db=db)
        self.assertEqual(fall.exception.status_code, 403)

    async def test_a_memory_that_is_gone_says_so(self):
        async with self.Session() as db:
            with self.assertRaises(HTTPException) as fall:
                await api.delete_memory(999, user=self._agent("a1"), db=db)
        self.assertEqual(fall.exception.status_code, 404)


class TheRouteAcceptsBothKindsOfCallerTests(unittest.TestCase):
    """Die Abhaengigkeit selbst ist der Fehler gewesen — ein Test auf das
    Verhalten oben ruft die Funktion direkt auf und wuerde einen Rueckfall auf
    ``require_auth`` nicht bemerken."""

    def test_delete_does_not_hang_on_a_user_only_login(self):
        from app.dependencies import require_auth, require_auth_or_agent
        route = next(r for r in api.router.routes
                     if getattr(r, "path", None) == "/memory/{memory_id}" and "DELETE" in getattr(r, "methods", ()))
        abhaengigkeiten = [d.call for d in route.dependant.dependencies]
        self.assertIn(require_auth_or_agent, abhaengigkeiten)
        self.assertNotIn(require_auth, abhaengigkeiten)


if __name__ == "__main__":
    unittest.main()
