"""Befunde des Sicherheitsreviews zu 1.461: weitere Startwege und Kiosk-Zugang.

* ``AgentManager.start_agent`` (darüber laufen Zustellung, Agenten-Räume und
  Nachrichten zwischen Agenten) startet einen vom Nutzer angehaltenen Agenten nur,
  wenn ein Mensch es will (``durch_mensch``).
* Einstellungen, die bisher ``restart_agent`` riefen, bauen einen gestoppten Agenten
  nur neu und lassen ihn gestoppt.
* Der Kiosk antwortet nur Geräten mit Zugang (``core/kiosk_zugang``), auch für
  Agenten, die den Orchestrator direkt erreichen.
"""

import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.core import kiosk_zugang
from app.core.agent_duty import ANGEHALTEN_VOM_NUTZER
from app.core.agent_manager import AgentManager
from app.models.agent import AgentState


def _manager(angehalten: bool):
    mgr = AgentManager.__new__(AgentManager)
    mgr.agent = SimpleNamespace(id="a1", name="A", container_id="c1", state=AgentState.STOPPED,
                                config={ANGEHALTEN_VOM_NUTZER: True} if angehalten else {})
    mgr._get_agent = AsyncMock(return_value=mgr.agent)
    mgr._publish_event = AsyncMock()
    mgr.docker = SimpleNamespace(start_container=lambda cid: (_ for _ in ()).throw(AssertionError("gestartet")))
    return mgr


class StartAgentHalt(unittest.IsolatedAsyncioTestCase):
    async def test_angehalten_automatisch_startet_nicht(self):
        mgr = _manager(True)
        self.assertIs(await mgr.start_agent("a1"), mgr.agent)
        mgr._publish_event.assert_not_awaited()

    async def test_zustellung_meldet_nicht_laufend(self):
        """ensure_agent_running ohne Mensch: der angehaltene Agent bleibt aus, die
        Zustellung wartet (Rueckgabe False)."""
        from app.core import agent_wakeup

        agent = SimpleNamespace(id="a1", container_id="c1", config={ANGEHALTEN_VOM_NUTZER: True},
                                state=AgentState.STOPPED)

        class _Db:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def scalar(self, _s):
                return agent

        docker = SimpleNamespace(get_container_status=lambda cid: "exited",
                                 start_container=lambda cid: (_ for _ in ()).throw(AssertionError("gestartet")))
        with patch("app.db.session.async_session_factory", lambda: _Db()):
            self.assertFalse(await agent_wakeup.ensure_agent_running("a1", docker, None))

    def test_menschliche_startwege_sagen_es(self):
        from app.api import agents, concierge, ws

        self.assertEqual(inspect.getsource(agents.start_agent).count("durch_mensch=True"), 1)
        self.assertEqual(inspect.getsource(agents.sentinel_fortsetzen).count("durch_mensch=True"), 1)
        self.assertIn("durch_mensch=True", inspect.getsource(concierge))
        self.assertIn("ensure_agent_running(agent_id, _docker, _redis, durch_mensch=True)", inspect.getsource(ws))

    def test_zustellwege_ohne_mensch(self):
        from app.api import meeting_rooms
        from app.core import agent_wakeup

        self.assertNotIn("durch_mensch=True", inspect.getsource(meeting_rooms))
        self.assertIn("durch_mensch: bool = False", inspect.getsource(agent_wakeup.ensure_agent_running))


class EinstellungenStartenNicht(unittest.TestCase):
    def test_anonymisierer_und_zielliste_bauen_gestoppte_nur_neu(self):
        from app.api import agents

        quelle = inspect.getsource(agents)
        muster = ("            if agent.state in (AgentState.RUNNING, AgentState.IDLE, AgentState.WORKING):\n"
                  "                await manager.restart_agent(agent_id)\n"
                  "            else:\n"
                  "                await manager.update_agent(agent_id)\n")
        self.assertEqual(quelle.count(muster), 2)

    def test_app_bericht_weckt_ueber_wake_agent(self):
        from app.api import apps_overview

        quelle = inspect.getsource(apps_overview)
        self.assertNotIn("docker.start_container(agent.container_id)", quelle)
        self.assertIn("await wake_agent(db, docker, agent.id)", quelle)


class KioskZugang(unittest.IsolatedAsyncioTestCase):
    def test_passt(self):
        wert = kiosk_zugang._wert("geheim")
        self.assertTrue(kiosk_zugang.passt("geheim", wert))
        for falsch in (None, "", "geheim2", "x" * 300):
            self.assertFalse(kiosk_zugang.passt(falsch, wert))
        self.assertFalse(kiosk_zugang.passt("geheim", ""), "ohne Einrichtung nie")

    def _app(self, gespeichert: str):
        from app.api import kiosk

        app = FastAPI()
        app.include_router(kiosk.router, prefix="/api/v1")
        app.include_router(kiosk.anmelde_router, prefix="/api/v1")
        return app, patch.object(kiosk_zugang, "_gespeichert", AsyncMock(return_value=gespeichert))

    def test_ohne_zugang_404_mit_cookie_durch(self):
        app, p = self._app(kiosk_zugang._wert("richtig"))
        with p, TestClient(app) as c:
            self.assertEqual(c.get("/api/v1/kiosk/settings").status_code, 404)
            self.assertEqual(c.get("/api/v1/kiosk/settings", headers={"X-Kiosk-Zugang": "falsch"}).status_code, 404)
            self.assertEqual(c.post("/api/v1/kiosk/anmelden", json={"zugang": "falsch"}).status_code, 404)
            r = c.post("/api/v1/kiosk/anmelden", json={"zugang": "richtig"})
            self.assertEqual(r.status_code, 200)
            kopf = r.headers["set-cookie"].lower()
            for teil in ("httponly", "samesite=strict", "path=/api/v1/kiosk"):
                self.assertIn(teil, kopf)
            self.assertEqual(c.get("/api/v1/kiosk/settings").status_code, 200)

    def test_ohne_einrichtung_alles_zu(self):
        app, p = self._app("")
        with p, TestClient(app) as c:
            self.assertEqual(c.post("/api/v1/kiosk/anmelden", json={"zugang": ""}).status_code, 404)
            self.assertEqual(c.get("/api/v1/kiosk/overview").status_code, 404)

    def test_jeder_kiosk_endpunkt_haengt_am_zugang(self):
        from app.api import kiosk

        self.assertIn(kiosk_zugang.pruefen, [d.dependency for d in kiosk.router.dependencies])
        offen = {r.path for r in kiosk.anmelde_router.routes}
        self.assertEqual(offen, {"/kiosk/anmelden"})

    async def test_verwaltung_nur_admin(self):
        from app.api import kiosk
        from app.dependencies import require_admin

        for route in kiosk.verwaltung_router.routes:
            abh = [p.call for p in route.dependant.dependencies]
            self.assertIn(require_admin, abh, route.path)


if __name__ == "__main__":
    unittest.main()
