"""Kleine Anlagen entlasten: docker stats zwischenspeichern, RAM-Grenze je Agent."""

import threading
import time
import unittest
from unittest import mock

from app.services.docker_service import DockerService


def _dienst():
    svc = DockerService.__new__(DockerService)  # ohne Docker-Verbindung
    svc._stats_gemessen = {}
    svc._stats_laeuft = set()
    svc._stats_sperre = threading.Lock()
    svc._platz_gemessen = {}
    svc._platz_laeuft = set()
    svc._platz_sperre = threading.Lock()
    return svc


def _warte_bis(bedingung, frist=3.0):
    ende = time.monotonic() + frist
    while time.monotonic() < ende:
        if bedingung():
            return True
        time.sleep(0.01)
    return False


class StatsZwischenspeicherTests(unittest.TestCase):
    def test_erster_aufruf_blockiert_nicht_und_liefert_none(self):
        svc = _dienst()
        freigabe = threading.Event()
        svc.get_container_stats = lambda cid: (freigabe.wait(5), {"cpu_percent": 1.0})[1]
        start = time.monotonic()
        self.assertIsNone(svc.container_stats_cached("c1"))
        self.assertLess(time.monotonic() - start, 0.2)
        freigabe.set()

    def test_zweiter_aufruf_nutzt_cache_ohne_neue_messung(self):
        svc = _dienst()
        aufrufe = []

        def messen(cid):
            aufrufe.append(cid)
            return {"cpu_percent": 3.0, "memory_usage_mb": 100.0}

        svc.get_container_stats = messen
        svc.container_stats_cached("c1")
        self.assertTrue(_warte_bis(lambda: "c1" in svc._stats_gemessen))
        for _ in range(5):
            self.assertEqual(svc.container_stats_cached("c1")["cpu_percent"], 3.0)
        time.sleep(0.05)
        self.assertEqual(len(aufrufe), 1)

    def test_alter_wert_startet_genau_eine_neue_messung(self):
        svc = _dienst()
        svc._stats_gemessen["c1"] = (time.monotonic() - 100, {"cpu_percent": 9.0})
        freigabe = threading.Event()
        aufrufe = []

        def messen(cid):
            aufrufe.append(cid)
            freigabe.wait(5)
            return {"cpu_percent": 1.0}

        svc.get_container_stats = messen
        for _ in range(10):
            # der alte Wert gilt, bis die neue Messung da ist
            self.assertEqual(svc.container_stats_cached("c1")["cpu_percent"], 9.0)
        self.assertTrue(_warte_bis(lambda: len(aufrufe) == 1))
        freigabe.set()
        self.assertTrue(_warte_bis(lambda: not svc._stats_laeuft))
        self.assertEqual(len(aufrufe), 1)

    def test_fehlgeschlagene_messung_gibt_freigabe(self):
        svc = _dienst()

        def kaputt(cid):
            raise RuntimeError("weg")

        svc.get_container_stats = kaputt
        self.assertIsNone(svc.container_stats_cached("c1"))
        self.assertTrue(_warte_bis(lambda: not svc._stats_laeuft))


class SpeichergrenzeTests(unittest.TestCase):
    def _erstelle(self, **kw):
        svc = DockerService.__new__(DockerService)
        client = mock.MagicMock()
        svc.client = client
        svc.create_container(
            image="img", name="n", environment={}, volume_name="v", network="net", **kw,
        )
        return client.containers.run.call_args.kwargs

    def test_grenze_setzt_mem_limit_und_gleiches_memswap(self):
        k = self._erstelle(memory_limit="3g")
        self.assertEqual(k["mem_limit"], "3g")
        self.assertEqual(k["memswap_limit"], "3g")

    def test_leer_setzt_keine_grenze(self):
        for leer in ("", None, "  "):
            k = self._erstelle(memory_limit=leer)
            self.assertNotIn("mem_limit", k)
            self.assertNotIn("memswap_limit", k)


if __name__ == "__main__":
    unittest.main()
