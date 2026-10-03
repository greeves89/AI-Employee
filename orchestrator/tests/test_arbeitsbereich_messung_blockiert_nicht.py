"""Die Agenten-Ansicht darf nie auf ``du`` warten.

Am 03.10.2026 stand die App auf dem Pi: ``GET /agents/{id}`` mass bei jedem Aufruf
den Arbeitsbereich live mit ``du``. Bei 11 GB und vollem Swap dauerte das Minuten,
die Seite fragte alle paar Sekunden nach, und jede Abfrage hielt einen Thread und
eine Datenbankverbindung fest — der Pool (30) war leer, alles andere endete nach
20 s mit 500.

Jetzt liest die Abfrage nur die letzte Messung; fehlt sie oder ist sie alt, startet
hoechstens EINE Messung je Container im Hintergrund.
"""

import threading
import time
import unittest

from app.services.docker_service import DockerService


def _dienst(messdauer: float = 0.0):
    svc = DockerService.__new__(DockerService)  # ohne Docker-Verbindung
    svc._platz_gemessen = {}
    svc._platz_laeuft = set()
    svc._platz_sperre = threading.Lock()
    svc.aufrufe = 0
    svc.freigabe = threading.Event()

    def messen(container_id, limit_gb):
        svc.aufrufe += 1
        svc.freigabe.wait(5)
        time.sleep(messdauer)
        svc._platz_gemessen[container_id] = (time.monotonic(), 2048.0)
        return None

    svc.get_workspace_disk_usage = messen
    return svc


def _warte_bis(bedingung, frist=3.0):
    ende = time.monotonic() + frist
    while time.monotonic() < ende:
        if bedingung():
            return True
        time.sleep(0.01)
    return False


class AbfrageWartetNieTests(unittest.TestCase):
    def test_erster_aufruf_antwortet_sofort_ohne_wert(self):
        svc = _dienst()
        start = time.monotonic()
        self.assertIsNone(svc.workspace_disk_usage_cached("c1", 10))
        self.assertLess(time.monotonic() - start, 0.2, "die Abfrage hat auf du gewartet")
        svc.freigabe.set()

    def test_gleichzeitige_abfragen_starten_nur_eine_messung(self):
        svc = _dienst()
        for _ in range(20):
            svc.workspace_disk_usage_cached("c1", 10)
        svc.freigabe.set()
        self.assertTrue(_warte_bis(lambda: not svc._platz_laeuft))
        self.assertEqual(svc.aufrufe, 1)

    def test_nach_der_messung_kommt_der_wert_gegen_die_quote(self):
        svc = _dienst()
        svc.freigabe.set()
        svc.workspace_disk_usage_cached("c1", 10)
        self.assertTrue(_warte_bis(lambda: "c1" in svc._platz_gemessen))
        wert = svc.workspace_disk_usage_cached("c1", 4)
        self.assertEqual(wert["disk_usage_mb"], 2048.0)
        self.assertEqual(wert["disk_limit_mb"], 4096.0)
        self.assertEqual(wert["disk_percent"], 50.0)

    def test_frische_messung_startet_keine_neue(self):
        svc = _dienst()
        svc._platz_gemessen["c1"] = (time.monotonic(), 100.0)
        svc.workspace_disk_usage_cached("c1", 10)
        self.assertEqual(svc.aufrufe, 0)

    def test_alte_messung_liefert_alten_wert_und_misst_neu(self):
        svc = _dienst()
        svc.freigabe.set()
        svc._platz_gemessen["c1"] = (time.monotonic() - 3600, 100.0)
        self.assertEqual(svc.workspace_disk_usage_cached("c1", 10)["disk_usage_mb"], 100.0)
        self.assertTrue(_warte_bis(lambda: svc.aufrufe == 1))


class AnfragePfadMisstNichtLiveTests(unittest.TestCase):
    def test_agent_manager_nutzt_den_zwischenspeicher(self):
        import pathlib
        quelle = (pathlib.Path(__file__).resolve().parents[1] / "app" / "core" / "agent_manager.py").read_text(encoding="utf-8")
        self.assertIn("workspace_disk_usage_cached", quelle)
        self.assertNotIn("self.docker.get_workspace_disk_usage,", quelle)


if __name__ == "__main__":
    unittest.main()
