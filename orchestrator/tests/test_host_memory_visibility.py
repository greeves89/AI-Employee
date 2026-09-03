"""Ein vom Kernel abgeschossener Lauf darf nicht spurlos verschwinden.

Befund #653: Auf einem Host mit ``cgroup_disable=memory`` fehlt der
Speicher-Controller. Docker kann dann kein Limit je Container durchsetzen
(``mem_limit`` wird still ignoriert), es gibt keine Buchfuehrung je Container,
und bei Knappheit beendet der Kernel den groessten Prozess — meist einen
laufenden Agenten.

Der Orchestrator sieht davon nur ``Connection closed by server``. Zehn von
neunzehn fehlgeschlagenen Aufgaben einer Woche gingen darauf zurueck, und die
Suche lief jedes Mal ins Leere: die Fehlerklasse wurde erst dem pids-Limit,
dann einem Sentinel-Stopp zugeschrieben — beides falsch.

Gemessen: ein Lauf belegt rund 1,04 GB, davon 691 MB die MCP-Server. Vier
gleichzeitige Laeufe sind 4,2 GB auf einem Host mit 7,95 GB.

Dieses Modul stellt nur Sichtbarkeit her. Der grosse Hebel ist #638 (MCP-Server
container-weit statt je Lauf), die Host-Aenderung selbst entscheidet der
Betreiber.
"""

import unittest
import unittest.mock
from pathlib import Path

from app.core import host_memory
from app.core.run_outcome import erklaerung_fuer_abriss

_ROUTER = (Path(__file__).resolve().parents[1] / "app" / "core" / "task_router.py").read_text()
_MAIN = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text()


class DieHostPruefungTests(unittest.TestCase):
    def test_ohne_lesbares_dateisystem_wird_nichts_behauptet(self):
        """Auf einem Rechner ohne cgroup2 (macOS, BSD) gibt es nichts zu melden —
        eine Warnung dort waere Rauschen."""
        with unittest.mock.patch.object(host_memory, "_CONTROLLER",
                                        Path("/nicht/vorhanden")):
            self.assertIsNone(host_memory.speicher_controller_da())
            self.assertIsNone(host_memory.hinweis())

    def test_vorhandener_controller_meldet_nichts(self):
        with unittest.mock.patch.object(host_memory, "speicher_controller_da",
                                        return_value=True):
            self.assertIsNone(host_memory.hinweis())

    def test_fehlender_controller_wird_gemeldet(self):
        with unittest.mock.patch.object(host_memory, "speicher_controller_da",
                                        return_value=False), \
             unittest.mock.patch.object(host_memory, "abgeschaltet_per_kernelzeile",
                                        return_value=False):
            text = host_memory.hinweis()
        self.assertIsNotNone(text)
        self.assertIn("wirkungslos", text)
        self.assertIn("Connection closed by server", text)

    def test_der_behebbare_fall_nennt_die_ursache(self):
        """`cgroup_disable=memory` ist entfernbar — das gehoert in die Meldung,
        sonst weiss niemand, dass sich daran etwas aendern laesst."""
        with unittest.mock.patch.object(host_memory, "speicher_controller_da",
                                        return_value=False), \
             unittest.mock.patch.object(host_memory, "abgeschaltet_per_kernelzeile",
                                        return_value=True):
            text = host_memory.hinweis()
        self.assertIn("cgroup_disable=memory", text)
        self.assertIn("Neustart", text)

    def test_die_pruefung_laeuft_beim_start(self):
        self.assertIn("beim_start_melden()", _MAIN)

    def test_ein_fehler_dabei_haelt_den_start_nicht_auf(self):
        block = _MAIN.split("beim_start_melden()", 1)[1][:300]
        self.assertIn("except Exception", block)


class DerAbrissBekommtSeineErklaerungTests(unittest.TestCase):
    def test_nur_bei_fehlendem_controller(self):
        """Auf einem gesunden Host ist der Abriss etwas anderes — dort waere die
        Erklaerung eine Irrefuehrung."""
        with unittest.mock.patch.object(host_memory, "speicher_controller_da",
                                        return_value=True):
            self.assertIsNone(erklaerung_fuer_abriss("Connection closed by server"))

    def test_bei_fehlendem_controller_wird_erklaert(self):
        with unittest.mock.patch.object(host_memory, "speicher_controller_da",
                                        return_value=False):
            text = erklaerung_fuer_abriss("Consumer error: Connection closed by server.")
        self.assertIsNotNone(text)
        self.assertIn("Kernel", text)
        self.assertIn("#653", text)

    def test_andere_fehler_bekommen_keine_erklaerung(self):
        """Sonst stuende bei jedem Fehlschlag derselbe Absatz — und niemand
        laese ihn mehr."""
        with unittest.mock.patch.object(host_memory, "speicher_controller_da",
                                        return_value=False):
            for anderer in ("401 Unauthorized", "FileNotFoundError: /tmp/x",
                            "You've hit your limit", ""):
                self.assertIsNone(erklaerung_fuer_abriss(anderer), anderer)

    def test_sie_haengt_am_ergebnispfad(self):
        self.assertIn("erklaerung_fuer_abriss(task.error)", _ROUTER)

    def test_der_urspruengliche_text_bleibt_erhalten(self):
        """Die Erklaerung tritt hinzu, sie ersetzt nicht — der Wortlaut des
        Fehlers ist die Grundlage jeder weiteren Suche."""
        block = _ROUTER.split("erklaerung_fuer_abriss(task.error)", 1)[1][:300]
        self.assertIn('f"{task.error} — {zusatz}"', block)


if __name__ == "__main__":
    unittest.main()
