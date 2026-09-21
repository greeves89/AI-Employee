"""Das automatische Nachbauen eines verschwundenen Agenten-Containers.

Am 21.09.2026 stand im Protokoll der Anlage:

    [UserLifecycle] Container gone for 5ff1d0cd, recreating via AgentManager
    [UserLifecycle] Could not recreate agent 5ff1d0cd: Redis not connected

``wake_agent()`` legt sich fuer den Neuaufbau einen eigenen ``RedisService``
an — und **verbindet ihn nicht**. Solange ``redis_acl_enabled`` aus ist,
faellt das nicht auf: Dann fasst der Agenten-Verwalter Redis auf diesem Weg
gar nicht an. Ist die Einstellung an (auf der betroffenen Anlage: an), holt
er sich fuer jeden Container einen eigenen Redis-Zugang — und scheitert an
der unverbundenen Verbindung. Ergebnis: Ein Agent, dessen Container
verschwunden ist, wird **nie** wieder aufgebaut; er bleibt dauerhaft weg.

Alle anderen Stellen, die sich einen eigenen ``RedisService`` anlegen
(``api/ratings.py``, ``api/schedules.py``), rufen unmittelbar danach
``connect()``. Nur diese eine nicht.

Der Test baut den Ablauf nach: ein Agent, dessen Container nicht mehr
existiert, ein Agenten-Verwalter, der — wie der echte — einen Redis-Zugang
anfordert. Ohne ``connect()`` scheitert der Neuaufbau, mit ihm gelingt er.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.models.agent import Agent, AgentState
from app.services import user_lifecycle


class _FakeDb:
    def __init__(self, agent):
        self.agent = agent
        self.commits = 0

    async def scalar(self, _stmt):
        return self.agent

    async def commit(self):
        self.commits += 1


class _FakeDocker:
    """Der Container ist weg — genau der Zustand, der den Neuaufbau ausloest."""

    def get_container_status(self, _ref):
        return ""


class _FakeRedisService:
    """So viel vom echten Dienst, wie dieser Pfad beruehrt."""

    #: Letzte erzeugte Instanz — der Test schaut sich danach an, ob sie
    #: verbunden und hinterher wieder geschlossen wurde.
    letzte = None

    def __init__(self, url=None):
        self.url = url
        self.client = None
        self.verbunden = False
        self.geschlossen = False
        type(self).letzte = self

    async def connect(self):
        self.client = object()
        self.verbunden = True

    async def disconnect(self):
        self.client = None
        self.geschlossen = True

    async def ensure_agent_acl_user(self, agent_id):
        # Dieselbe Bedingung wie im echten Dienst (redis_service.py).
        if not self.client:
            raise RuntimeError("Redis not connected")
        return f"redis://agent-{agent_id}@redis:6379"


class _FakeAgentManager:
    """Verhaelt sich beim Neuaufbau wie der echte: fordert einen Redis-Zugang an.

    Im Original passiert das in ``restart_agent`` ueber ``_agent_redis_url``,
    das bei eingeschaltetem ``redis_acl_enabled`` auf
    ``redis.ensure_agent_acl_user`` durchreicht.
    """

    def __init__(self, db, docker, redis):
        self.db = db
        self.docker = docker
        self.redis = redis

    async def restart_agent(self, agent_id):
        await self.redis.ensure_agent_acl_user(agent_id)
        return SimpleNamespace(id=agent_id)


class WakeAgentBautContainerNeu(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.agent = Agent(
            id="5ff1d0cd",
            name="Testagent",
            state=AgentState.STOPPED,
            config={},
            container_id="ai-agent-testagent-5ff1d0cd",
        )
        self.db = _FakeDb(self.agent)
        _FakeRedisService.letzte = None

    async def _wecken(self):
        with patch("app.core.agent_manager.AgentManager", _FakeAgentManager), \
             patch("app.services.redis_service.RedisService", _FakeRedisService):
            return await user_lifecycle.wake_agent(self.db, _FakeDocker(), self.agent.id)

    async def test_neuaufbau_gelingt(self):
        self.assertTrue(
            await self._wecken(),
            "Der Neuaufbau ist fehlgeschlagen — ein Agent ohne Container bleibt "
            "damit dauerhaft weg, statt automatisch wiederzukommen.",
        )

    async def test_der_redis_dienst_wird_verbunden(self):
        await self._wecken()
        dienst = _FakeRedisService.letzte
        self.assertIsNotNone(dienst, "Es wurde gar kein Redis-Dienst angelegt.")
        self.assertTrue(
            dienst.verbunden,
            "Der eigens angelegte Redis-Dienst wurde nie verbunden — genau der "
            "Fehler, der den Neuaufbau auf der Anlage unmoeglich machte.",
        )

    async def test_die_verbindung_bleibt_nicht_offen(self):
        """Jede Minute ein Weckversuch, jedes Mal eine Verbindung mehr —
        die Verbindung muss wieder zu, auch wenn der Neuaufbau scheitert."""
        await self._wecken()
        self.assertTrue(
            _FakeRedisService.letzte.geschlossen,
            "Die Verbindung blieb offen; der Weckpfad laeuft im Minutentakt.",
        )

    async def test_auch_bei_fehler_wird_geschlossen(self):
        class _Knallt(_FakeAgentManager):
            async def restart_agent(self, agent_id):
                raise RuntimeError("Docker weg")

        with patch("app.core.agent_manager.AgentManager", _Knallt), \
             patch("app.services.redis_service.RedisService", _FakeRedisService):
            ergebnis = await user_lifecycle.wake_agent(self.db, _FakeDocker(), self.agent.id)

        self.assertFalse(ergebnis)
        self.assertTrue(
            _FakeRedisService.letzte.geschlossen,
            "Nach einem gescheiterten Neuaufbau blieb die Verbindung offen.",
        )


if __name__ == "__main__":
    unittest.main()
