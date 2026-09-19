"""Der Agent muss den Speicherquote-Vorfall selbst sehen, nicht nur der Betreiber
(#830). Vorher war ``/workspace/.disk_warning`` reine Bring-Schuld — nach einem
automatischen Neustart hatte niemand einen Grund, dem Agenten davon zu erzaehlen.
``get_disk_incident_context`` macht daraus eine Hol-Schuld bei jedem Sitzungsstart.
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.runner_hooks import get_disk_incident_context


class DiskIncidentContextTest(unittest.TestCase):
    def test_no_file_means_no_block(self):
        with TemporaryDirectory() as d:
            self.assertEqual(get_disk_incident_context(str(Path(d) / ".disk_warning")), "")

    def test_missing_directory_does_not_raise(self):
        self.assertEqual(get_disk_incident_context("/does/not/exist/.disk_warning"), "")

    def test_present_file_becomes_a_prominent_block(self):
        with TemporaryDirectory() as d:
            path = Path(d) / ".disk_warning"
            path.write_text("DISK WARNING: 96.0% of workspace quota used\nUsed: 9830 MB / 10240 MB\n")
            block = get_disk_incident_context(str(path))
            self.assertIn("=== SPEICHERPLATZ KNAPP ===", block)
            self.assertIn("=== ENDE ===", block)
            self.assertIn("96.0% of workspace quota used", block)

    def test_empty_file_means_no_block(self):
        """_clear_warning loescht die Datei bei Erholung; ein leerer Rest (z.B.
        durch eine Race beim Schreiben) darf trotzdem keinen leeren Block zeigen."""
        with TemporaryDirectory() as d:
            path = Path(d) / ".disk_warning"
            path.write_text("   \n")
            self.assertEqual(get_disk_incident_context(str(path)), "")


if __name__ == "__main__":
    unittest.main()
