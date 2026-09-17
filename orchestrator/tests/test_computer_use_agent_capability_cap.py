"""Issue #787 Punkt 1: Computer-Use bekommt einen dauerhaften Pro-Agent-Default.

Vorher gab es dafuer gar keinen dauerhaften Wert -- jede Session startete immer
mit demselben Plattform-Default (``DEFAULT_ALLOWED_CAPABILITIES``), unabhaengig
davon, ob der zugewiesene Agent laut Autonomie-Matrix ueberhaupt Shell-/System-
Aktionen ausfuehren darf. Diese Tests pruefen die neue Invariante: eine Session
darf nie mehr Faehigkeiten haben als der Agent, dem sie zugewiesen ist.
"""
import time
import unittest
from unittest.mock import AsyncMock

from app.api import computer_use as cu


def _session(user_id="u1", **over):
    s = {
        "user_id": user_id,
        "created_at": time.time(),
        "last_activity_at": time.time(),
        "bridge_connected": False,
        "bridge_ws": None,
        "action_count": 0,
        "audit_log": [],
        "pending_results": {},
        "allowed_capabilities": set(cu.DEFAULT_ALLOWED_CAPABILITIES),
        "allowed_apps": None,
        "allowed_domains": None,
        "last_disconnected_at": None,
        "bridge_last_seen_at": None,
        "bridge_host": None,
        "agent_id": None,
        "recording": False,
        "recording_steps": [],
        "capture_human": False,
    }
    s.update(over)
    return s


def _agent(access_policy=None, user_id="u1"):
    return type("FakeAgent", (), {"access_policy": access_policy or {}, "user_id": user_id})()


class AgentDefaultCapabilitiesTests(unittest.IsolatedAsyncioTestCase):
    async def test_agent_without_override_falls_back_to_platform_default(self):
        db = AsyncMock()
        db.get = AsyncMock(return_value=_agent(access_policy={}))
        got = await cu._agent_default_capabilities("a1", db)
        self.assertEqual(got, set(cu.DEFAULT_ALLOWED_CAPABILITIES))

    async def test_agent_with_override_uses_its_own_list(self):
        db = AsyncMock()
        db.get = AsyncMock(return_value=_agent(
            access_policy={"computer_use_default_capabilities": ["screenshots"]}
        ))
        got = await cu._agent_default_capabilities("a1", db)
        self.assertEqual(got, {"screenshots"})

    async def test_missing_agent_falls_back_to_platform_default(self):
        db = AsyncMock()
        db.get = AsyncMock(return_value=None)
        got = await cu._agent_default_capabilities("gone", db)
        self.assertEqual(got, set(cu.DEFAULT_ALLOWED_CAPABILITIES))


class AssignAgentCapsSessionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        cu._sessions.clear()

    async def test_assigning_a_restrictive_agent_shrinks_the_session(self):
        cu._sessions["s1"] = _session(
            allowed_capabilities={"screenshots", "mouse", "keyboard"}
        )
        db = AsyncMock()
        db.scalar = AsyncMock(return_value=_agent(
            access_policy={"computer_use_default_capabilities": ["screenshots"]}
        ))
        user = type("U", (), {"id": "u1"})()
        req = cu.AgentAssignment(agent_id="a1")

        await cu.assign_agent("s1", req, user=user, db=db)

        self.assertEqual(cu._sessions["s1"]["allowed_capabilities"], {"screenshots"})
        self.assertEqual(cu._sessions["s1"]["agent_id"], "a1")

    async def test_unassigning_does_not_touch_capabilities(self):
        cu._sessions["s1"] = _session(
            allowed_capabilities={"screenshots", "mouse"}, agent_id="a1"
        )
        db = AsyncMock()
        user = type("U", (), {"id": "u1"})()
        req = cu.AgentAssignment(agent_id=None)

        await cu.assign_agent("s1", req, user=user, db=db)

        self.assertEqual(cu._sessions["s1"]["allowed_capabilities"], {"screenshots", "mouse"})
        self.assertIsNone(cu._sessions["s1"]["agent_id"])


class UpdateCapabilitiesCapTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        cu._sessions.clear()

    async def test_requesting_beyond_the_agents_default_is_rejected(self):
        cu._sessions["s1"] = _session(agent_id="a1", allowed_capabilities={"screenshots"})
        db = AsyncMock()
        db.get = AsyncMock(return_value=_agent(
            access_policy={"computer_use_default_capabilities": ["screenshots"]}
        ))
        user = type("U", (), {"id": "u1"})()
        req = cu.CapabilityUpdate(allowed_capabilities=["screenshots", "mouse"])

        with self.assertRaises(cu.HTTPException) as ctx:
            await cu.update_capabilities("s1", req, user=user, db=db)
        self.assertEqual(ctx.exception.status_code, 422)
        # Unveraendert -- eine abgelehnte Anfrage darf den Zustand nicht anfassen.
        self.assertEqual(cu._sessions["s1"]["allowed_capabilities"], {"screenshots"})

    async def test_requesting_within_the_agents_default_succeeds(self):
        cu._sessions["s1"] = _session(agent_id="a1", allowed_capabilities={"screenshots"})
        db = AsyncMock()
        db.get = AsyncMock(return_value=_agent(
            access_policy={"computer_use_default_capabilities": ["screenshots", "mouse"]}
        ))
        user = type("U", (), {"id": "u1"})()
        req = cu.CapabilityUpdate(allowed_capabilities=["mouse"])

        await cu.update_capabilities("s1", req, user=user, db=db)

        self.assertEqual(cu._sessions["s1"]["allowed_capabilities"], {"mouse"})

    async def test_a_session_without_an_assigned_agent_is_unrestricted_as_before(self):
        """Regressionsschutz: kein zugewiesener Agent -> unveraendertes
        Verhalten von vor dieser Aenderung."""
        cu._sessions["s1"] = _session(agent_id=None, allowed_capabilities={"screenshots"})
        db = AsyncMock()
        user = type("U", (), {"id": "u1"})()
        req = cu.CapabilityUpdate(allowed_capabilities=["shell"])

        await cu.update_capabilities("s1", req, user=user, db=db)

        self.assertEqual(cu._sessions["s1"]["allowed_capabilities"], {"shell"})


class CreateSessionSeedingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        cu._sessions.clear()

    async def test_seed_is_the_intersection_of_platform_and_agent_default(self):
        db = AsyncMock()
        db.scalar = AsyncMock(return_value=_agent(
            access_policy={"computer_use_default_capabilities": ["screenshots"]}, user_id="u1"
        ))
        user = type("U", (), {"id": "u1"})()

        result = await cu.create_session(user=user, reuse=False, agent_id="a1", db=db)

        self.assertEqual(result["allowed_capabilities"], ["screenshots"])

    async def test_without_an_agent_id_behavior_is_unchanged(self):
        db = AsyncMock()
        user = type("U", (), {"id": "u1"})()

        result = await cu.create_session(user=user, reuse=False, agent_id=None, db=db)

        self.assertEqual(set(result["allowed_capabilities"]), set(cu.DEFAULT_ALLOWED_CAPABILITIES))

    async def test_someone_elses_agent_id_is_rejected_not_silently_used(self):
        """Security-Fund im Review: `create_session` prueft die Eigentuemer-
        schaft eines uebergebenen agent_id NICHT, waehrend das gleichzeitig
        hinzugefuegte `assign_agent` das schon tut -- ein Nutzer konnte damit
        ein fremdes Agenten-Profil (dessen Computer-Use-Default) ueber die
        Session-Erstellung abfragen. Muss wie `assign_agent` einheitlich
        ablehnen, nicht nur bei der spaeteren Zuweisung."""
        db = AsyncMock()
        db.scalar = AsyncMock(return_value=_agent(
            access_policy={"computer_use_default_capabilities": ["screenshots"]}, user_id="someone-else"
        ))
        user = type("U", (), {"id": "u1"})()

        with self.assertRaises(cu.HTTPException) as ctx:
            await cu.create_session(user=user, reuse=False, agent_id="not-mine", db=db)
        self.assertEqual(ctx.exception.status_code, 404)

    async def test_a_nonexistent_agent_id_is_rejected_too(self):
        db = AsyncMock()
        db.scalar = AsyncMock(return_value=None)
        user = type("U", (), {"id": "u1"})()

        with self.assertRaises(cu.HTTPException) as ctx:
            await cu.create_session(user=user, reuse=False, agent_id="gone", db=db)
        self.assertEqual(ctx.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
