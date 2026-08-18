"""Textdateien im Arbeitsbereich lassen sich bearbeiten und speichern.

Aus dem Kundentermin vom 18.08.2026: ``.env``-Dateien liessen sich ansehen,
aber nicht aendern. Wer eine Zeile korrigieren wollte, musste herunterladen,
lokal bearbeiten und wieder hochladen. Die Dateiansicht war rein lesend, es gab
schlicht keinen Schreib-Endpunkt.

Der Schwerpunkt dieser Tests liegt auf dem, was beim Schreiben schiefgehen
kann: der Weg darf den Arbeitsbereich nicht verlassen. Genau deshalb sitzt die
Pruefung in ``FileManager`` — an derselben Stelle wie beim Lesen und Hochladen
— und nicht in der Schnittstelle.
"""

import unittest
from unittest.mock import MagicMock

from app.core.file_manager import MAX_EDIT_SIZE_BYTES, FileManager


class WritingAFileTests(unittest.TestCase):
    def setUp(self):
        self.docker = MagicMock()
        #: Weder Symlink noch Verzeichnis — der uebliche Fall.
        self.docker.exec_in_container.return_value = (0, "OK")
        self.fm = FileManager(self.docker)

    def test_a_normal_file_is_written(self):
        geschrieben = self.fm.write_file("c1", "/workspace/projekt/.env", "A=1\nB=2\n")
        self.docker.write_file_in_container.assert_called_once_with(
            "c1", "/workspace/projekt/.env", "A=1\nB=2\n"
        )
        self.assertEqual(geschrieben, len("A=1\nB=2\n"))

    def test_the_path_is_normalised_before_writing(self):
        """Sonst landet die Datei unter einem anderen Pfad, als geprueft wurde."""
        self.fm.write_file("c1", "/workspace/./a/../b.txt", "x")
        self.assertEqual(self.docker.write_file_in_container.call_args.args[1], "/workspace/b.txt")


class TheWorkspaceIsNotLeftTests(unittest.TestCase):
    def setUp(self):
        self.docker = MagicMock()
        self.docker.exec_in_container.return_value = (0, "OK")
        self.fm = FileManager(self.docker)

    def _verboten(self, pfad):
        with self.assertRaises(ValueError):
            self.fm.write_file("c1", pfad, "x")
        self.docker.write_file_in_container.assert_not_called()

    def test_climbing_out_with_dot_dot(self):
        self._verboten("/workspace/../etc/passwd")

    def test_a_path_outside_entirely(self):
        self._verboten("/etc/passwd")

    def test_a_relative_path(self):
        self._verboten("workspace/x")

    def test_a_null_byte(self):
        self._verboten("/workspace/x\x00.txt")

    def test_a_path_that_only_looks_like_the_workspace(self):
        """``/workspace-anders`` faengt zwar mit demselben Wort an, ist aber ein
        anderes Verzeichnis."""
        self._verboten("/workspace-anders/x.txt")


class DangerousTargetsAreRefusedTests(unittest.TestCase):
    def setUp(self):
        self.docker = MagicMock()
        self.fm = FileManager(self.docker)

    def test_a_symlink_is_not_followed(self):
        """Ueber einen Symlink liesse sich ausserhalb des Arbeitsbereichs
        schreiben, obwohl der Pfad selbst sauber aussieht. Lesen prueft
        dasselbe."""
        self.docker.exec_in_container.return_value = (0, "SYMLINK\n")
        with self.assertRaises(ValueError):
            self.fm.write_file("c1", "/workspace/link", "x")
        self.docker.write_file_in_container.assert_not_called()

    def test_a_directory_is_not_overwritten(self):
        self.docker.exec_in_container.return_value = (0, "DIR\n")
        with self.assertRaises(ValueError):
            self.fm.write_file("c1", "/workspace/ordner", "x")
        self.docker.write_file_in_container.assert_not_called()

    def test_a_blocked_extension_stays_blocked(self):
        """Was nicht hochgeladen werden darf, darf auch nicht per Bearbeiten
        entstehen — sonst waere die Sperre beim Hochladen umgehbar."""
        self.docker.exec_in_container.return_value = (0, "OK")
        with self.assertRaises(ValueError):
            self.fm.write_file("c1", "/workspace/boese.exe", "x")

    def test_something_far_too_big_is_refused(self):
        self.docker.exec_in_container.return_value = (0, "OK")
        with self.assertRaises(ValueError):
            self.fm.write_file("c1", "/workspace/gross.txt", "x" * (MAX_EDIT_SIZE_BYTES + 1))
        self.docker.write_file_in_container.assert_not_called()


class TheEndpointIsGuardedTests(unittest.TestCase):
    """Der Schreibweg muss dieselben Wachen haben wie das Herunterladen —
    sonst kann ein Fremder in einen fremden Arbeitsbereich schreiben."""

    def test_it_checks_the_owner_and_takes_a_login(self):
        from pathlib import Path
        quelle = (Path(__file__).resolve().parents[1] / "app/api/agents.py").read_text()
        block = quelle.split("async def save_file_content(", 1)
        self.assertEqual(len(block), 2, "Endpunkt fehlt")
        rumpf = block[1][:1400]
        self.assertIn("Depends(require_auth)", rumpf)
        self.assertIn("_check_owner(agent_id, user, db)", rumpf)

    def test_it_goes_through_the_file_manager_not_straight_to_docker(self):
        from pathlib import Path
        quelle = (Path(__file__).resolve().parents[1] / "app/api/agents.py").read_text()
        rumpf = quelle.split("async def save_file_content(", 1)[1][:1400]
        self.assertIn("file_mgr.write_file(", rumpf)
        self.assertNotIn("exec_run", rumpf)


if __name__ == "__main__":
    unittest.main()


class TheUiCanActuallyEditTests(unittest.TestCase):
    """Ein Schreib-Endpunkt allein nuetzt nichts, wenn die Ansicht weiter nur
    anzeigt — genau das war der gemeldete Zustand."""

    from pathlib import Path
    ROOT = Path(__file__).resolve().parents[2]
    VORSCHAU = (ROOT / "frontend/src/components/files/file-preview.tsx").read_text()
    API = (ROOT / "frontend/src/lib/api.ts").read_text()
    AGENT = (ROOT / "frontend/src/app/agents/[id]/page.tsx").read_text()
    DATEIEN = (ROOT / "frontend/src/app/files/page.tsx").read_text()

    def test_there_is_an_edit_button(self):
        self.assertIn("Bearbeiten", self.VORSCHAU)

    def test_editing_shows_a_text_field_instead_of_the_rendered_view(self):
        """Bei HTML saehe man sonst das Bild statt der Quelle."""
        self.assertIn("imBearbeiten ? bearbeitungsFlaeche : content", self.VORSCHAU)

    def test_saving_calls_the_endpoint(self):
        self.assertIn("api.saveFileContent(agentId, filePath, entwurf)", self.VORSCHAU)
        self.assertIn("/files/content", self.API)

    def test_switching_files_drops_an_unsaved_draft(self):
        """Sonst landete der Text der einen Datei in der naechsten."""
        block = self.VORSCHAU.split('setHtmlTab("rendered");', 1)[1][:200]
        self.assertIn("setEntwurf(null)", block)

    def test_saving_is_offered_in_both_file_trees(self):
        """Ohne `agentId` bleibt die Ansicht lesend — beide Baeume muessen ihn
        durchreichen, sonst kann man nur in einem bearbeiten."""
        self.assertIn("agentId={agentId}", self.AGENT)
        self.assertIn("agentId={selectedFile.agentId}", self.DATEIEN)

    def test_a_failed_save_is_shown_and_the_text_is_kept(self):
        """Der Entwurf darf beim Fehlschlag nicht verlorengehen."""
        block = self.VORSCHAU.split("setSpeicherFehler(e instanceof Error", 1)
        self.assertEqual(len(block), 2)
        self.assertNotIn("setEntwurf(null)", block[1][:200])
        self.assertIn("{speicherFehler}", self.VORSCHAU)
