"""Beim Hochladen muss der Zielordner waehlbar sein — und zwar genau der, den
der Server auch annimmt.

Nutzerbericht vom 18.08.2026, mit Bildschirmfoto: „im Frontend kann ich nicht
aussuchen wohin die datei soll". Das Fenster zeigte fest „to /workspace"; wer
woanders hin wollte, musste hochladen und danach von Hand verschieben.

Der zweite Teil ist der wichtigere: die Auswahl darf nur anbieten, was der
Server auch erlaubt. ``file_manager.upload_files`` weist alles ausserhalb von
``/workspace`` mit einem Fehler ab. Ein Schnellziel ``/shared`` waere ein
Vorschlag, der garantiert fehlschlaegt — beim Bauen dieser Auswahl zunaechst
genau so passiert und vor dem Ausliefern aufgefallen.
"""

import inspect
import re
import unittest
from pathlib import Path

from app.core.file_manager import FileManager

ROOT = Path(__file__).resolve().parents[2]
UI = (ROOT / "frontend/src/components/files/file-uploader.tsx").read_text()


class TheTargetCanBeChosenTests(unittest.TestCase):
    def test_there_is_a_field_for_it(self):
        self.assertIn("Zielordner", UI)

    def test_the_passed_path_is_only_a_default(self):
        """Vorher war er fest verdrahtet."""
        self.assertIn("useState(targetPath)", UI)

    def test_the_upload_uses_the_chosen_folder(self):
        self.assertIn("uploadFiles(agentId, zielSauber, files)", UI)
        self.assertNotIn("uploadFiles(agentId, targetPath, files)", UI)

    def test_common_folders_are_one_click_away(self):
        self.assertIn("SCHNELLZIELE", UI)


class TheUiDrawsTheSameLineAsTheServerTests(unittest.TestCase):
    """Eine Auswahl, die etwas anbietet, was der Server ablehnt, ist schlimmer
    als gar keine Auswahl."""

    SERVER = inspect.getsource(FileManager.upload_files)

    def test_the_server_only_accepts_workspace(self):
        self.assertIn('target_path.startswith("/workspace")', self.SERVER)

    def test_the_ui_offers_nothing_outside_workspace(self):
        vorschlaege = re.findall(r'pfad: "([^"]+)"', UI)
        self.assertTrue(vorschlaege, "keine Schnellziele gefunden")
        for p in vorschlaege:
            with self.subTest(pfad=p):
                self.assertTrue(p.startswith("/workspace"), f"{p} wuerde abgewiesen")

    def test_the_ui_refuses_the_same_paths(self):
        self.assertIn(r"/^\/workspace(\/|$)/", UI)

    def test_it_says_so_before_uploading(self):
        """Sonst kommt die Ablehnung erst als 400, wenn die Datei schon
        unterwegs war."""
        self.assertIn("Der Zielordner muss unter /workspace liegen.", UI)


class ThePromisedFolderIsActuallyCreatedTests(unittest.TestCase):
    """Die Oberflaeche verspricht „wird angelegt, falls es ihn noch nicht gibt"
    — das muss stimmen, sonst schickt man Nutzer ins Leere."""

    def test_the_ui_promises_it(self):
        self.assertIn("wird angelegt", UI)

    def test_the_server_keeps_that_promise(self):
        self.assertIn('["mkdir", "-p", safe_path]',
                      inspect.getsource(FileManager.upload_files))


if __name__ == "__main__":
    unittest.main()
