"""Build-Werkzeuge (.exe bauen) haengen an der Vorlage — in JEDEM Weg, der einen Container baut.

Agenten aus einer Vorlage mit ``build_tools`` (mitgeliefert: „Fullstack
Developer") bekommen ein eigenes Volume auf /opt/build-tools, die Freigabe
``BUILD_TOOLS=1`` fuer ``build-tools`` im Container und einen Abschnitt in ihrer
Anleitung. Alle anderen bekommen nichts davon.

Geprueft wird am echten Ablauf von Anlegen, Neustart und Aktualisieren — nicht
am Quelltext. Fehlt die Verdrahtung in einem der Wege, verliert ein Agent sein
Werkzeug-Volume beim naechsten Neustart, obwohl seine Anleitung es verspricht.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.agent_manager import AgentManager, build_tools_volume
from app.models.agent import Agent, AgentState
from app.models.agent_template import AgentTemplate

AGENT_ID = "abc12345"


def _vorlage(build_tools: bool) -> AgentTemplate:
    return AgentTemplate(id=7, name="vorlage", display_name="Vorlage", build_tools=build_tools)


def _docker():
    docker = MagicMock()
    docker.geschrieben = {}
    docker.create_container.return_value = SimpleNamespace(id="neuer-container")
    docker.write_file_in_container.side_effect = (
        lambda _cid, pfad, inhalt: docker.geschrieben.__setitem__(pfad, inhalt)
    )
    return docker


def _db(vorlage):
    db = MagicMock()
    db.get = AsyncMock(side_effect=lambda modell, _id: vorlage if modell is AgentTemplate else None)
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.flush = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock())
    db.scalar = AsyncMock(return_value=None)
    return db


def _manager(db, docker, agent=None):
    m = AgentManager(db, docker, MagicMock())
    for name in ("_publish_event", "_cancel_open_chats", "migrate_knowledge_file"):
        setattr(m, name, AsyncMock())
    m._get_agent = AsyncMock(return_value=agent)
    m._agent_redis_url = AsyncMock(return_value="redis://redis:6379")
    m._get_secrets_env = AsyncMock(return_value={})
    m._effective_llm_config = AsyncMock(return_value=None)
    m._get_custom_mcp_env = AsyncMock(return_value={})
    m._get_integration_env = AsyncMock(return_value={})
    m._owner_credential_env = AsyncMock(return_value={})
    m._apply_permissions = MagicMock()
    m._update_team_registry = MagicMock()
    return m


def _agent(template_id):
    return Agent(
        id=AGENT_ID, name="Ben", state=AgentState.RUNNING, model="claude-sonnet-4-6",
        mode="claude_code", volume_name=f"workspace-{AGENT_ID}", container_id="alt",
        template_id=template_id, autonomy_level="l3", access_policy={},
        config={"role": "Entwickler", "session_volume": f"claude-session-{AGENT_ID}"},
    )


_UMGEBUNG = (
    patch("app.core.mounts.get_effective_catalog", AsyncMock(return_value={})),
    patch("app.core.master_rules.load", AsyncMock(return_value="")),
    patch("app.services.claude_token_service.ClaudeTokenService",
          MagicMock(return_value=MagicMock(refresh_access_token=AsyncMock()))),
)


class _Basis(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        for p in _UMGEBUNG:
            p.start()
            self.addCleanup(p.stop)

    def _pruefe(self, docker, soll: bool):
        kw = docker.create_container.call_args.kwargs
        if soll:
            self.assertEqual(kw["build_tools_volume_name"], build_tools_volume(AGENT_ID))
            self.assertEqual(kw["environment"].get("BUILD_TOOLS"), "1")
            self.assertIn("Windows-Programme bauen", docker.geschrieben["/workspace/CLAUDE.md"])
        else:
            self.assertIsNone(kw["build_tools_volume_name"])
            self.assertNotIn("BUILD_TOOLS", kw["environment"])
            self.assertNotIn("Windows-Programme bauen", docker.geschrieben["/workspace/CLAUDE.md"])


class NeustartUndAktualisieren(_Basis):
    async def _lauf(self, methode, vorlage, template_id=7):
        docker = _docker()
        m = _manager(_db(vorlage), docker, _agent(template_id))
        await getattr(m, methode)(AGENT_ID)
        return docker

    async def test_neustart_mit_vorlage_mit_werkzeugen(self):
        self._pruefe(await self._lauf("restart_agent", _vorlage(True)), True)

    async def test_aktualisieren_mit_vorlage_mit_werkzeugen(self):
        self._pruefe(await self._lauf("update_agent", _vorlage(True)), True)

    async def test_vorlage_ohne_werkzeuge(self):
        self._pruefe(await self._lauf("update_agent", _vorlage(False)), False)

    async def test_agent_ohne_vorlage(self):
        self._pruefe(await self._lauf("restart_agent", None, template_id=None), False)

    async def test_geloeschte_vorlage(self):
        self._pruefe(await self._lauf("update_agent", None), False)


class Anlegen(_Basis):
    async def _anlegen(self, vorlage, template_id):
        docker = _docker()
        db = _db(vorlage)
        m = _manager(db, docker)
        with patch("app.core.agent_manager.uuid.uuid4", return_value=MagicMock(hex=AGENT_ID + "0" * 24)):
            agent = await m.create_agent(name="Ben", user_id="u1", template_id=template_id)
        return docker, agent

    async def test_anlegen_aus_vorlage_mit_werkzeugen(self):
        docker, agent = await self._anlegen(_vorlage(True), 7)
        self._pruefe(docker, True)
        self.assertEqual(agent.template_id, 7, "Die Herkunft muss gleich beim Anlegen stehen.")

    async def test_anlegen_ohne_vorlage(self):
        docker, agent = await self._anlegen(None, None)
        self._pruefe(docker, False)


class RefreshInstructions(_Basis):
    async def test_anleitung_im_laufenden_container(self):
        docker = _docker()
        m = _manager(_db(_vorlage(True)), docker)
        self.assertTrue(await m.refresh_instructions(_agent(7)))
        self.assertIn("build-tools install", docker.geschrieben["/workspace/CLAUDE.md"])


class Loeschen(_Basis):
    async def test_werkzeug_volume_geht_mit_den_daten(self):
        docker = _docker()
        agent = _agent(7)
        m = _manager(_db(_vorlage(True)), docker, agent)
        m.redis = MagicMock()
        with patch("app.core.agent_manager.settings.redis_acl_enabled", False):
            try:
                await m.remove_agent(AGENT_ID, remove_data=True)
            except Exception:  # noqa: BLE001 — der DB-Teil danach ist hier nicht nachgebaut
                pass
        entfernt = [c.args[0] for c in docker.remove_volume.call_args_list]
        self.assertIn(build_tools_volume(AGENT_ID), entfernt)


class DockerMount(unittest.TestCase):
    def test_volume_landet_auf_opt_build_tools(self):
        from app.services.docker_service import DockerService

        dienst = DockerService.__new__(DockerService)
        dienst.client = MagicMock()
        dienst.create_container(
            image="bild", name="n", environment={}, volume_name="workspace-x",
            network="netz", build_tools_volume_name="build-tools-x",
        )
        volumes = dienst.client.containers.run.call_args.kwargs["volumes"]
        self.assertEqual(volumes["build-tools-x"], {"bind": "/opt/build-tools", "mode": "rw"})

    def test_ohne_werkzeuge_kein_mount(self):
        from app.services.docker_service import DockerService

        dienst = DockerService.__new__(DockerService)
        dienst.client = MagicMock()
        dienst.create_container(image="bild", name="n", environment={}, volume_name="w", network="netz")
        volumes = dienst.client.containers.run.call_args.kwargs["volumes"]
        self.assertNotIn("/opt/build-tools", [v["bind"] for v in volumes.values()])


class MitgelieferteVorlagen(unittest.TestCase):
    def test_fullstack_developer_hat_werkzeuge(self):
        from app.core.agent_templates import BUILTIN_TEMPLATES

        mit = {t["name"] for t in BUILTIN_TEMPLATES if t.get("build_tools")}
        self.assertEqual(mit, {"fullstack-developer"})

    def test_jede_vorlage_passt_ins_modell(self):
        """Der Start-Abgleich legt Vorlagen mit AgentTemplate(**daten) an —
        ein Schluessel ohne Spalte liesse den ganzen Abgleich scheitern."""
        from app.core.agent_templates import BUILTIN_TEMPLATES

        for daten in BUILTIN_TEMPLATES:
            AgentTemplate(**daten)


if __name__ == "__main__":
    unittest.main()
