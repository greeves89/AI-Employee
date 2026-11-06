"""Neuerstellen mit Rücksprung (``AgentManager.update_agent``).

Früher: alten Behälter entfernen, dann neuen erstellen. Scheiterte das Erstellen, war
der Agent weg — beim automatischen Aktualisieren (1.433.0) hätte das ohne Aufsicht
passieren können. Jetzt: alten anhalten und beiseitestellen, neuen erstellen, erst dann
den alten entfernen; scheitert es, kommt der alte zurück und läuft wie vorher.
"""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from docker.errors import APIError, NotFound

from app.core.agent_manager import AgentManager

NAME = "ai-agent-test-a1b2c3d4"


class FakeBehaelter:
    def __init__(self, cid, name, status="running", rename_fehler=False):
        self.id, self.name, self.status, self.rename_fehler = cid, name, status, rename_fehler

    def rename(self, neu):
        if self.rename_fehler:
            raise APIError("rename gesperrt")
        self.name = neu


class FakeDocker:
    def __init__(self, *behaelter):
        self.behaelter = {b.id: b for b in behaelter}
        self.gestartet, self.entfernt, self.angehalten = [], [], []
        self.client = SimpleNamespace(containers=SimpleNamespace(get=self._get))

    def _get(self, ref):
        for b in self.behaelter.values():
            if ref in (b.id, b.name):
                return b
        raise NotFound("weg")

    def stop_container(self, cid):
        self.angehalten.append(cid)
        self._get(cid).status = "exited"

    def start_container(self, cid):
        self.gestartet.append(cid)
        self._get(cid).status = "running"

    def remove_container(self, cid, force=True):
        self._get(cid)
        self.entfernt.append(cid)
        del self.behaelter[cid]


def _manager(docker, neu_erstellen):
    mgr = AgentManager.__new__(AgentManager)
    mgr.docker = docker
    mgr.db = MagicMock(rollback=AsyncMock())
    mgr._get_agent = AsyncMock(return_value=SimpleNamespace(id="a1b2c3d4", name="Test", container_id="alt1"))
    mgr._update_agent_neu_erstellen = neu_erstellen
    return mgr


class Ruecksprung(unittest.IsolatedAsyncioTestCase):
    async def test_erfolg_alter_erst_danach_entfernt(self):
        docker = FakeDocker(FakeBehaelter("alt1", NAME))
        reihenfolge = []

        async def neu(aid):
            alt = docker.behaelter["alt1"]
            reihenfolge.append(("beim_erstellen", alt.name, alt.status))
            docker.behaelter["neu1"] = FakeBehaelter("neu1", NAME)
            return SimpleNamespace(container_id="neu1")
        mgr = _manager(docker, neu)
        await mgr.update_agent("a1b2c3d4")
        _, name, status = reihenfolge[0]
        self.assertTrue(name.startswith(NAME + "-alt-"), "Name frei für den neuen Behälter")
        self.assertEqual(status, "exited", "alter steht, bevor der neue startet (Codex-Anmeldung)")
        self.assertEqual(docker.entfernt, ["alt1"])
        self.assertEqual(set(docker.behaelter), {"neu1"})

    async def test_fehlschlag_alter_kommt_zurueck_und_laeuft(self):
        docker = FakeDocker(FakeBehaelter("alt1", NAME))

        async def neu(aid):
            docker.behaelter["halb"] = FakeBehaelter("halb", NAME, status="created")
            raise RuntimeError("Abbild fehlt")
        mgr = _manager(docker, neu)
        with self.assertRaises(RuntimeError):
            await mgr.update_agent("a1b2c3d4")
        self.assertIn("halb", docker.entfernt, "halb erstellter neuer Behälter weg")
        self.assertEqual(docker.behaelter["alt1"].name, NAME, "alter wieder unter seinem Namen")
        self.assertEqual(docker.gestartet, ["alt1"])
        self.assertNotIn("alt1", docker.entfernt)
        mgr.db.rollback.assert_awaited()

    async def test_fehlschlag_gestoppter_alter_bleibt_gestoppt(self):
        docker = FakeDocker(FakeBehaelter("alt1", NAME, status="exited"))
        mgr = _manager(docker, AsyncMock(side_effect=RuntimeError("Wächter lehnt ab")))
        with self.assertRaises(RuntimeError):
            await mgr.update_agent("a1b2c3d4")
        self.assertEqual(docker.behaelter["alt1"].name, NAME)
        self.assertEqual(docker.gestartet, [])

    async def test_ohne_alten_behaelter(self):
        docker = FakeDocker()

        async def neu(aid):
            docker.behaelter["neu1"] = FakeBehaelter("neu1", NAME)
            return SimpleNamespace(container_id="neu1")
        mgr = _manager(docker, neu)
        await mgr.update_agent("a1b2c3d4")
        self.assertEqual(docker.entfernt, [])

    async def test_umbenennen_gesperrt_wie_frueher_entfernen(self):
        docker = FakeDocker(FakeBehaelter("alt1", NAME, rename_fehler=True))
        neu = AsyncMock(return_value=SimpleNamespace(container_id="neu1"))
        mgr = _manager(docker, neu)
        await mgr.update_agent("a1b2c3d4")
        self.assertEqual(docker.entfernt, ["alt1"], "Name muss frei werden")
        neu.assert_awaited_once()

    async def test_abbruch_waehrend_des_erstellens_auch_zurueck(self):
        import asyncio
        docker = FakeDocker(FakeBehaelter("alt1", NAME))
        mgr = _manager(docker, AsyncMock(side_effect=asyncio.CancelledError()))
        with self.assertRaises(asyncio.CancelledError):
            await mgr.update_agent("a1b2c3d4")
        self.assertEqual(docker.behaelter["alt1"].name, NAME)
        self.assertEqual(docker.gestartet, ["alt1"])


if __name__ == "__main__":
    unittest.main()
