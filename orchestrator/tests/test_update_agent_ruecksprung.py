"""Neuerstellen mit Rücksprung (``AgentManager.update_agent``).

Früher: alten Behälter entfernen, dann neuen erstellen. Scheiterte das Erstellen, war
der Agent weg. Jetzt: alten anhalten und beiseitestellen, neuen erstellen, erst dann den
alten entfernen. Scheitert es vor dem Festschreiben, kommt der alte unter seinem Namen
zurück — GESTARTET aber nur bei reiner Wartung: Neuerstellen entzieht auch Zugänge
(Schlüssel entfernt, Rechte gekürzt), der alte Behälter trüge sie noch.
"""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from docker.errors import APIError, NotFound

from app.core import agent_manager as am
from app.core.agent_manager import AgentManager
from app.models.agent import AgentState

NAME = "ai-agent-test-a1b2c3d4"


class FakeBehaelter:
    def __init__(self, cid, name, status="running", rename_fehler=False, haelt_nicht=False):
        self.id, self.name, self.status = cid, name, status
        self.rename_fehler, self.haelt_nicht = rename_fehler, haelt_nicht

    def rename(self, neu):
        if self.rename_fehler:
            raise APIError("rename gesperrt")
        self.name = neu

    def reload(self):
        pass

    def remove(self, force=True):
        self.entfernt = True


class FakeDocker:
    def __init__(self, *behaelter):
        self.behaelter = {b.id: b for b in behaelter}
        self.gestartet, self.entfernt = [], []
        self.client = SimpleNamespace(containers=SimpleNamespace(get=self._get))

    def _get(self, ref):
        for b in self.behaelter.values():
            if ref in (b.id, b.name):
                return b
        raise NotFound("weg")

    def stop_container(self, cid):
        b = self._get(cid)
        if not b.haelt_nicht:
            b.status = "exited"

    def start_container(self, cid):
        self.gestartet.append(cid)
        self._get(cid).status = "running"

    def remove_container(self, cid, force=True):
        self._get(cid)
        self.entfernt.append(cid)
        del self.behaelter[cid]


def _manager(docker, neu_erstellen, container_id="alt1"):
    mgr = AgentManager.__new__(AgentManager)
    mgr.docker = docker
    mgr.db = MagicMock(rollback=AsyncMock(), commit=AsyncMock())
    mgr.agent = SimpleNamespace(id="a1b2c3d4", name="Test", container_id=container_id,
                                state=AgentState.RUNNING, config={})
    mgr._get_agent = AsyncMock(return_value=mgr.agent)

    async def bauen(aid):
        ergebnis = await neu_erstellen(aid)
        return ergebnis
    mgr._update_agent_neu_erstellen = bauen
    return mgr


def _neu(docker, mgr_ref, festschreiben=True, danach_fehler=None):
    async def neu(aid):
        docker.behaelter["neu1"] = FakeBehaelter("neu1", NAME)
        if festschreiben:
            mgr_ref[0].agent.container_id = "neu1"
            mgr_ref[0]._neubau_festgeschrieben = "neu1"
        if danach_fehler:
            raise danach_fehler
        return mgr_ref[0].agent
    return neu


class Ruecksprung(unittest.IsolatedAsyncioTestCase):
    async def test_erfolg_alter_erst_danach_entfernt_und_waehrenddessen_kein_verweis(self):
        docker = FakeDocker(FakeBehaelter("alt1", NAME))
        ref, beobachtet = [None], []

        async def neu(aid):
            alt = docker.behaelter["alt1"]
            beobachtet.append((alt.name, alt.status, ref[0].agent.container_id))
            return await _neu(docker, ref)(aid)
        mgr = ref[0] = _manager(docker, neu)
        await mgr.update_agent("a1b2c3d4")
        name, status, verweis = beobachtet[0]
        self.assertTrue(name.startswith(NAME + am.ALT_MARKE))
        self.assertEqual(status, "exited", "alter steht, bevor der neue startet")
        self.assertIsNone(verweis, "während des Neubaus zeigt der Agent auf keinen Behälter")
        self.assertEqual(docker.entfernt, ["alt1"])
        self.assertEqual(mgr.agent.container_id, "neu1")

    async def test_fehlschlag_alter_zurueck_aber_angehalten(self):
        """Kein automatischer Neustart: ein entzogener Zugang darf nicht weiter gelten."""
        docker = FakeDocker(FakeBehaelter("alt1", NAME))

        async def neu(aid):
            docker.behaelter["halb"] = FakeBehaelter("halb", NAME, status="created")
            raise RuntimeError("Abbild fehlt")
        mgr = _manager(docker, neu)
        with self.assertRaises(RuntimeError):
            await mgr.update_agent("a1b2c3d4")
        self.assertIn("halb", docker.entfernt)
        self.assertEqual(docker.behaelter["alt1"].name, NAME)
        self.assertEqual(docker.gestartet, [])
        self.assertEqual(mgr.agent.container_id, "alt1")
        self.assertEqual(mgr.agent.state, AgentState.STOPPED)
        self.assertEqual(mgr.agent.config["stop_reason"]["type"], "aktualisieren_gescheitert")

    async def test_fehlschlag_bei_wartung_laeuft_der_alte_weiter(self):
        docker = FakeDocker(FakeBehaelter("alt1", NAME))
        mgr = _manager(docker, AsyncMock(side_effect=RuntimeError("Abbild fehlt")))
        with self.assertRaises(RuntimeError):
            await mgr.update_agent("a1b2c3d4", wartung=True)
        self.assertEqual(docker.gestartet, ["alt1"])
        self.assertNotEqual(mgr.agent.state, AgentState.STOPPED)

    async def test_gestoppter_alter_bleibt_gestoppt_ohne_stoppgrund(self):
        docker = FakeDocker(FakeBehaelter("alt1", NAME, status="exited"))
        mgr = _manager(docker, AsyncMock(side_effect=RuntimeError("Wächter lehnt ab")))
        mgr.agent.state = AgentState.STOPPED
        with self.assertRaises(RuntimeError):
            await mgr.update_agent("a1b2c3d4", wartung=True)
        self.assertEqual(docker.gestartet, [])
        self.assertNotIn("stop_reason", mgr.agent.config)

    async def test_fehler_nach_dem_festschreiben_kein_ruecksprung(self):
        """Abbruch nach dem Commit (Anfrage abgebrochen): der neue Behälter gilt."""
        docker = FakeDocker(FakeBehaelter("alt1", NAME))
        ref = [None]
        mgr = ref[0] = _manager(docker, _neu(docker, ref, danach_fehler=asyncio.CancelledError()))
        with self.assertRaises(asyncio.CancelledError):
            await mgr.update_agent("a1b2c3d4")
        self.assertIn("neu1", docker.behaelter, "gesunder neuer Behälter bleibt")
        self.assertEqual(docker.entfernt, ["alt1"])
        self.assertEqual(mgr.agent.container_id, "neu1")

    async def test_alter_haelt_nicht_an_wird_entfernt(self):
        """Nie einen laufenden alten neben den neuen stellen (zwei Agenten, dieselben Volumes)."""
        docker = FakeDocker(FakeBehaelter("alt1", NAME, haelt_nicht=True))
        ref = [None]
        mgr = ref[0] = _manager(docker, _neu(docker, ref))
        await mgr.update_agent("a1b2c3d4")
        self.assertEqual(docker.entfernt, ["alt1"])

    async def test_umbenennen_gesperrt_wie_frueher_entfernen(self):
        docker = FakeDocker(FakeBehaelter("alt1", NAME, rename_fehler=True))
        ref = [None]
        mgr = ref[0] = _manager(docker, _neu(docker, ref))
        await mgr.update_agent("a1b2c3d4")
        self.assertEqual(docker.entfernt, ["alt1"])

    async def test_abbruch_vor_dem_festschreiben_auch_zurueck(self):
        docker = FakeDocker(FakeBehaelter("alt1", NAME))
        mgr = _manager(docker, AsyncMock(side_effect=asyncio.CancelledError()))
        with self.assertRaises(asyncio.CancelledError):
            await mgr.update_agent("a1b2c3d4", wartung=True)
        self.assertEqual(docker.behaelter["alt1"].name, NAME)
        self.assertEqual(mgr.agent.container_id, "alt1")

    async def test_zwei_gleichzeitige_neubauten_nacheinander(self):
        docker = FakeDocker()
        laeuft, maximal = [0], [0]

        async def neu(aid):
            laeuft[0] += 1
            maximal[0] = max(maximal[0], laeuft[0])
            await asyncio.sleep(0.01)
            laeuft[0] -= 1
            return SimpleNamespace()
        a, b = _manager(docker, neu, container_id=None), _manager(docker, neu, container_id=None)
        await asyncio.gather(a.update_agent("a1b2c3d4"), b.update_agent("a1b2c3d4"))
        self.assertEqual(maximal[0], 1)


class AufraeumenBeimStart(unittest.IsolatedAsyncioTestCase):
    async def test_reste_entfernt_verwendeter_bekommt_namen_zurueck(self):
        from unittest.mock import patch
        weg = FakeBehaelter("w1", NAME + am.ALT_MARKE + "1700000000", status="exited")
        gebraucht = FakeBehaelter("g1", "ai-agent-zwei-e5f6a7b8" + am.ALT_MARKE + "1700000000", status="exited")
        laufend = FakeBehaelter("l1", "ai-agent-drei-11112222" + am.ALT_MARKE + "1700000000", status="running")
        normal = FakeBehaelter("n1", "ai-agent-vier-33334444", status="running")
        docker = SimpleNamespace(client=SimpleNamespace(containers=SimpleNamespace(
            list=lambda **k: [weg, gebraucht, laufend, normal])))
        db = MagicMock()
        db.execute = AsyncMock(return_value=MagicMock(all=lambda: [("g1",), ("n1",)]))
        fabrik = MagicMock()
        fabrik.return_value.__aenter__ = AsyncMock(return_value=db)
        fabrik.return_value.__aexit__ = AsyncMock(return_value=False)
        with patch("app.db.session.async_session_factory", fabrik):
            ergebnis = await am.alte_behaelter_aufraeumen(docker)
        self.assertEqual(ergebnis, {"entfernt": 1, "zurueck": 1})
        self.assertTrue(getattr(weg, "entfernt", False))
        self.assertEqual(gebraucht.name, "ai-agent-zwei-e5f6a7b8")
        self.assertFalse(getattr(laufend, "entfernt", False), "laufende nie anfassen")


if __name__ == "__main__":
    unittest.main()
