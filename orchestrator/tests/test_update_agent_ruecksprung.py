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

_TOKEN = None


def setUpModule():
    """Token-Erneuerung (vor dem Anhalten) ist hier nicht Gegenstand — keine Netzaufrufe."""
    global _TOKEN
    from unittest.mock import patch
    _TOKEN = patch("app.services.claude_token_service.ClaudeTokenService",
                   return_value=SimpleNamespace(refresh_access_token=AsyncMock()))
    _TOKEN.start()


def tearDownModule():
    _TOKEN.stop()


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
    # Modulweiter Zustand je Test frisch (jeder Test hat seine eigene Ereignisschleife;
    # ein Lock aus einem früheren Test wäre an dessen Schleife gebunden).
    am._neubau_locks.clear()
    am._letzter_stopp.clear()
    am._festgeschrieben.clear()
    mgr = AgentManager.__new__(AgentManager)
    mgr.docker = docker
    mgr.db = MagicMock(rollback=AsyncMock(), commit=AsyncMock(), refresh=AsyncMock())
    mgr.agent = SimpleNamespace(id="a1b2c3d4", name="Test", container_id=container_id,
                                state=AgentState.RUNNING, config={})
    mgr._get_agent = AsyncMock(return_value=mgr.agent)
    mgr._cancel_open_chats = AsyncMock()

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
            am._festgeschrieben[aid] = "neu1"
        if danach_fehler:
            raise danach_fehler
        return mgr_ref[0].agent
    return neu


class Ruecksprung(unittest.IsolatedAsyncioTestCase):
    async def test_token_und_chats_vor_dem_anhalten(self):
        """Reihenfolge wie vor 1.434.1: Token erneuern, offene Chats beenden, DANN anhalten."""
        from unittest.mock import patch
        docker = FakeDocker(FakeBehaelter("alt1", NAME))
        reihenfolge = []
        docker.stop_container = lambda cid: (reihenfolge.append("anhalten"),
                                             setattr(docker._get(cid), "status", "exited"))
        ref = [None]
        mgr = ref[0] = _manager(docker, _neu(docker, ref))
        mgr._cancel_open_chats = AsyncMock(side_effect=lambda *a: reihenfolge.append("chats"))
        token = SimpleNamespace(refresh_access_token=AsyncMock(
            side_effect=lambda: reihenfolge.append("token")))
        with patch("app.services.claude_token_service.ClaudeTokenService", return_value=token):
            await mgr.update_agent("a1b2c3d4")
        self.assertEqual(reihenfolge, ["token", "chats", "anhalten"])

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

    async def test_fehlschlag_ohne_wartung_alter_entfernt_agent_angehalten(self):
        """Neuerstellen entzieht auch Zugänge: der alte Behälter darf nicht der des Agenten
        bleiben — sonst brächte ihn der nächste Start mit den entzogenen Zugängen hoch."""
        docker = FakeDocker(FakeBehaelter("alt1", NAME))

        async def neu(aid):
            docker.behaelter["halb"] = FakeBehaelter("halb", NAME, status="created")
            raise RuntimeError("Abbild fehlt")
        mgr = _manager(docker, neu)
        with self.assertRaises(RuntimeError):
            await mgr.update_agent("a1b2c3d4")
        self.assertIn("halb", docker.entfernt)
        self.assertIn("alt1", docker.entfernt)
        self.assertEqual(docker.gestartet, [])
        self.assertIsNone(mgr.agent.container_id, "nächster Start baut neu")
        self.assertEqual(mgr.agent.state, AgentState.STOPPED)
        self.assertEqual(mgr.agent.config["stop_reason"]["type"], "aktualisieren_gescheitert")

    async def test_fehlschlag_bei_wartung_laeuft_der_alte_weiter(self):
        docker = FakeDocker(FakeBehaelter("alt1", NAME))
        mgr = _manager(docker, AsyncMock(side_effect=RuntimeError("Abbild fehlt")))
        with self.assertRaises(RuntimeError):
            await mgr.update_agent("a1b2c3d4", wartung=True)
        self.assertEqual(docker.gestartet, ["alt1"])
        self.assertEqual(docker.behaelter["alt1"].name, NAME)
        self.assertEqual(mgr.agent.container_id, "alt1")
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


class StoppWaehrendNeubau(unittest.IsolatedAsyncioTestCase):
    async def test_stopp_waehrend_neubau_haelt_den_neuen_an(self):
        """Nutzer oder Sentinel stoppen, während gebaut wird: Der neue Behälter darf danach
        nicht einfach loslaufen."""
        docker = FakeDocker(FakeBehaelter("alt1", NAME))
        docker.angehalten = []
        echt_stop = docker.stop_container
        docker.stop_container = lambda cid: (docker.angehalten.append(cid), echt_stop(cid))
        ref = [None]

        async def neu(aid):
            # Mitten im Bau kommt der Stopp — er darf nicht auf den Neubau warten.
            await asyncio.wait_for(ref[0].stop_agent(aid), timeout=1)
            docker.behaelter["neu1"] = FakeBehaelter("neu1", NAME)
            ref[0].agent.container_id = "neu1"
            am._festgeschrieben[aid] = "neu1"
            return ref[0].agent
        mgr = ref[0] = _manager(docker, neu)
        mgr._publish_event = AsyncMock()
        await mgr.update_agent("a1b2c3d4", wartung=True)
        self.assertIn("neu1", docker.angehalten)
        self.assertEqual(mgr.agent.state, AgentState.STOPPED)

    async def test_stopp_waehrend_neubau_bei_fehlschlag_startet_den_alten_nicht(self):
        docker = FakeDocker(FakeBehaelter("alt1", NAME))
        ref = [None]

        async def neu(aid):
            await ref[0].stop_agent(aid)
            raise RuntimeError("Abbild fehlt")
        mgr = ref[0] = _manager(docker, neu)
        mgr._publish_event = AsyncMock()
        with self.assertRaises(RuntimeError):
            await mgr.update_agent("a1b2c3d4", wartung=True)
        self.assertEqual(docker.gestartet, [], "gestoppt bleibt gestoppt, auch bei Wartung")
        self.assertEqual(docker.behaelter["alt1"].name, NAME, "bei Wartung bleibt der alte erhalten (angehalten)")
        self.assertNotIn("stop_reason", mgr.agent.config, "gewollter Stopp ist kein Fehlergrund")
        self.assertEqual(mgr.agent.state, AgentState.STOPPED)

    async def test_alter_stopp_haelt_keinen_spaeteren_neubau_an(self):
        """Nichts darf „liegen bleiben“: ein Stopp von früher wirkt nicht auf einen neuen Neubau."""
        docker = FakeDocker(FakeBehaelter("alt1", NAME))
        ref = [None]
        mgr = ref[0] = _manager(docker, _neu(docker, ref))
        mgr._publish_event = AsyncMock()
        await mgr.stop_agent("a1b2c3d4")              # Stopp VOR der Anfrage
        await asyncio.sleep(0.001)
        await mgr.update_agent("a1b2c3d4")
        self.assertEqual(mgr.agent.container_id, "neu1")
        self.assertNotIn("neu1", getattr(docker, "angehalten", []))

    async def test_wartender_start_ueberstimmt_keinen_stopp(self):
        """Start wartet auf einen laufenden Neubau, DANN kommt der Sentinel-Stopp: der Agent
        darf danach nicht wieder hochfahren (Prüfung: wartender Start überstimmte den Stopp)."""
        docker = FakeDocker(FakeBehaelter("alt1", NAME))
        ref, gebaut = [None], []
        darf_weiter = asyncio.Event()

        async def neu(aid):
            gebaut.append(aid)
            await darf_weiter.wait()
            docker.behaelter.setdefault("neu1", FakeBehaelter("neu1", NAME))
            ref[0].agent.container_id = "neu1"
            am._festgeschrieben[aid] = "neu1"
            return ref[0].agent
        mgr = ref[0] = _manager(docker, neu)
        mgr._publish_event = AsyncMock()
        erster = asyncio.create_task(mgr.update_agent("a1b2c3d4", wartung=True))
        await asyncio.sleep(0.01)
        wartender = asyncio.create_task(mgr.update_agent("a1b2c3d4", nur_wenn_noetig=True))
        await asyncio.sleep(0.01)
        await mgr.stop_agent("a1b2c3d4")              # Sentinel
        darf_weiter.set()
        await erster
        await wartender
        self.assertEqual(len(gebaut), 1, "der wartende Start baut nach dem Stopp nicht neu")
        self.assertEqual(mgr.agent.state, AgentState.STOPPED)

    async def test_entzug_nach_stopp_baut_nichts_und_entfernt_den_alten(self):
        """Neubau zum Entziehen eines Zugangs (wartung=False), Stopp kommt, während er auf den
        Lock wartet: kein neuer Behälter, der auch nur kurz läuft (Sentinel-Stopp gilt), und
        der alte mit dem entzogenen Zugang verschwindet — der nächste Start baut frisch."""
        docker = FakeDocker(FakeBehaelter("alt1", NAME))
        ref, gebaut = [None], []
        darf_weiter = asyncio.Event()

        async def neu(aid):
            gebaut.append(aid)
            await darf_weiter.wait()
            nid = f"neu{len(gebaut)}"
            docker.behaelter[nid] = FakeBehaelter(nid, NAME)
            ref[0].agent.container_id = nid
            am._festgeschrieben[aid] = nid
            return ref[0].agent
        mgr = ref[0] = _manager(docker, neu)
        mgr._publish_event = AsyncMock()
        erster = asyncio.create_task(mgr.update_agent("a1b2c3d4", wartung=True))
        await asyncio.sleep(0.01)
        entzug = asyncio.create_task(mgr.update_agent("a1b2c3d4"))       # Schlüssel entfernt
        await asyncio.sleep(0.01)
        await mgr.stop_agent("a1b2c3d4")
        darf_weiter.set()
        await erster
        await entzug
        self.assertEqual(len(gebaut), 1, "nach dem Stopp entsteht kein weiterer Behälter")
        self.assertEqual(docker.behaelter, {}, "kein Behälter mit dem entzogenen Zugang bleibt")
        self.assertIsNone(mgr.agent.container_id)
        self.assertEqual(mgr.agent.state, AgentState.STOPPED)

    async def test_stopp_waehrend_des_baus_vor_dem_erstellen_kein_start(self):
        """Stopp kommt mitten im Bau, aber vor dem Erstellen: es entsteht kein Behälter, kein
        Fehler für den Aufrufer (sonst hielte die Automatik an), Wartung behält den alten
        angehalten."""
        docker = FakeDocker(FakeBehaelter("alt1", NAME))
        ref = [None]

        async def neu(aid):
            await ref[0].stop_agent(aid)                 # Sentinel, mitten im Bau
            await asyncio.sleep(0.001)
            # so prüft _update_agent_neu_erstellen vor dem Erstellen:
            if am._gestoppt_seit(aid, am._neubau_angefragt.get(aid, float("inf"))):
                raise am.NeubauGestoppt(aid)
            raise AssertionError("hätte nicht erstellt werden dürfen")
        mgr = ref[0] = _manager(docker, neu)
        mgr._publish_event = AsyncMock()
        ergebnis = await mgr.update_agent("a1b2c3d4", wartung=True)
        self.assertIs(ergebnis, mgr.agent)
        self.assertEqual(docker.gestartet, [])
        self.assertEqual(docker.behaelter["alt1"].name, NAME)
        self.assertEqual(mgr.agent.state, AgentState.STOPPED)
        self.assertEqual(am._neubau_angefragt, {}, "nichts bleibt liegen")

    def test_pruefung_steht_vor_dem_erstellen_im_bauweg(self):
        """Verdrahtung im Syntaxbaum: im Bauweg steht die Stopp-Prüfung vor jedem Erstellen."""
        import ast
        from pathlib import Path
        baum = ast.parse((Path(__file__).resolve().parents[1] / "app/core/agent_manager.py").read_text())
        fn = next(n for n in ast.walk(baum)
                  if isinstance(n, ast.AsyncFunctionDef) and n.name == "_update_agent_neu_erstellen")
        def erstellt(knoten):
            return any(isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_create_agent_container"
                       for n in ast.walk(knoten))

        def prueft(knoten):
            return any(isinstance(n, ast.Raise) and "NeubauGestoppt" in ast.dump(n) for n in ast.walk(knoten))
        # Die Anweisung, in der erstellt wird (der Zweig Codex/sonst) — davor, auf derselben
        # Ebene, eine eigene Prüfung; die im Codex-Zweig allein deckt den anderen Zweig nicht.
        i = next(i for i, n in enumerate(fn.body) if erstellt(n))
        self.assertTrue(any(prueft(n) for n in fn.body[:i]), "Prüfung vor dem Erstellen fehlt")

    async def test_stopp_ohne_neubau_wie_bisher(self):
        docker = FakeDocker(FakeBehaelter("alt1", NAME))
        mgr = _manager(docker, AsyncMock())
        mgr._publish_event = AsyncMock()
        await mgr.stop_agent("a1b2c3d4")
        self.assertEqual(mgr.agent.state, AgentState.STOPPED)

    async def test_stopp_fuer_unbekannten_agenten_hinterlaesst_nichts(self):
        mgr = _manager(FakeDocker(), AsyncMock())
        mgr._publish_event = AsyncMock()
        mgr._get_agent = AsyncMock(side_effect=ValueError("nicht gefunden"))
        with self.assertRaises(ValueError):
            await mgr.stop_agent("ffffffff")
        self.assertNotIn("ffffffff", am._letzter_stopp)


class KeinZweiterNeubau(unittest.IsolatedAsyncioTestCase):
    async def test_wartender_start_findet_fertigen_neubau(self):
        """Wecken während eines Updates: nicht den gerade fertigen Agenten noch einmal bauen."""
        from unittest.mock import patch
        docker = FakeDocker(FakeBehaelter("neu1", NAME))
        docker.get_container_status = lambda cid: "running"
        neu = AsyncMock()
        mgr = _manager(docker, neu, container_id="neu1")
        with patch("app.core.agenten_aktualisieren.veraltet", return_value=False):
            ergebnis = await mgr.update_agent("a1b2c3d4", nur_wenn_noetig=True)
        neu.assert_not_awaited()
        self.assertIs(ergebnis, mgr.agent)
        mgr.db.refresh.assert_awaited()

    async def test_ohne_flag_wird_gebaut(self):
        docker = FakeDocker()
        neu = AsyncMock(return_value=SimpleNamespace())
        mgr = _manager(docker, neu, container_id=None)
        await mgr.update_agent("a1b2c3d4", nur_wenn_noetig=True)
        neu.assert_awaited_once()

    def test_muster_nur_fuer_beiseitegestellte(self):
        self.assertTrue(am._ALT_MUSTER.search("ai-agent-x-a1b2c3d4-alt-1794000409"))
        self.assertFalse(am._ALT_MUSTER.search("ai-agent-alt-support-a1b2c3d4"), "Agent heißt „Alt-Support“")
        self.assertFalse(am._ALT_MUSTER.search("ai-agent-x-a1b2c3d4-alt-12"))


class AufraeumenBeimStart(unittest.IsolatedAsyncioTestCase):
    async def test_reste_entfernt_verwendeter_bekommt_namen_zurueck(self):
        from unittest.mock import patch
        weg = FakeBehaelter("w1", NAME + am.ALT_MARKE + "1700000000", status="exited")
        gebraucht = FakeBehaelter("g1", "ai-agent-zwei-e5f6a7b8" + am.ALT_MARKE + "1700000000", status="exited")
        laufend = FakeBehaelter("l1", "ai-agent-drei-11112222" + am.ALT_MARKE + "1700000000", status="running")
        normal = FakeBehaelter("n1", "ai-agent-vier-33334444", status="running")
        alt_support = FakeBehaelter("s1", "ai-agent-alt-support-55556666", status="exited")
        docker = SimpleNamespace(client=SimpleNamespace(containers=SimpleNamespace(
            list=lambda **k: [weg, gebraucht, laufend, normal, alt_support])))
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
        self.assertFalse(getattr(alt_support, "entfernt", False), "Agent „Alt-Support“ ist kein Rest")


if __name__ == "__main__":
    unittest.main()
