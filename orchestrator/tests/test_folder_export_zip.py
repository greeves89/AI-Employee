"""Einen Ordner aus dem Arbeitsbereich als ZIP herunterladen.

Wunsch des Nutzers vom 21.08.2026, an zwei Stellen genannt: in der
App-Uebersicht („damit das Workspace Verzeichnis als ZIP heruntergeladen werden
kann") und im Dateibaum („hier auch gern den Export").

Beide benutzen denselben Endpunkt. Zwei Wege mit eigener Logik waeren die
naechste Stelle, die auseinanderlaeuft — an diesem Wochenende ist genau das
schon dreimal passiert (Rueckfrage-Anzeige, Werkzeugliste der Sprachfront,
Modell bei Delegation).

Der Schwerpunkt der Tests: ein Projektordner enthaelt ``node_modules``. Ein
Export, der daran scheitert oder eine Stunde laeuft, hilft niemandem.
"""

import io
import tarfile
import unittest
import zipfile
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.core.file_manager import (
    EXPORT_AUSGENOMMEN,
    MAX_EXPORT_BYTES,
    ExportZuGross,
    FileManager,
    _ist_ausgenommen,
)


def _tar(eintraege: dict[str, bytes]) -> bytes:
    puffer = io.BytesIO()
    with tarfile.open(fileobj=puffer, mode="w") as t:
        for name, inhalt in eintraege.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(inhalt)
            t.addfile(info, io.BytesIO(inhalt))
    return puffer.getvalue()


def _manager(tar_bytes: bytes) -> FileManager:
    container = MagicMock()
    container.get_archive.return_value = ([tar_bytes], {})
    docker = SimpleNamespace(client=SimpleNamespace(containers=MagicMock()))
    docker.client.containers.get.return_value = container
    fm = FileManager.__new__(FileManager)
    fm.docker = docker
    return fm


class TheFolderComesOutAsAZipTests(unittest.TestCase):
    def test_the_files_are_in_the_archive(self):
        fm = _manager(_tar({
            "app/index.js": b"console.log(1)",
            "app/src/main.ts": b"export {}",
        }))
        daten, anzahl = fm.export_folder_zip("c1", "/workspace/projects/app")
        self.assertEqual(anzahl, 2)
        with zipfile.ZipFile(io.BytesIO(daten)) as z:
            self.assertIn("app/src/main.ts", z.namelist())

    def test_the_folder_name_is_kept_as_the_root(self):
        """Entpackt soll wieder ein Ordner entstehen, keine Dateiwolke im
        Download-Verzeichnis."""
        fm = _manager(_tar({"app/index.js": b"x"}))
        daten, _ = fm.export_folder_zip("c1", "/workspace/projects/app")
        with zipfile.ZipFile(io.BytesIO(daten)) as z:
            self.assertTrue(all(n.startswith("app/") for n in z.namelist()))

    def test_the_content_survives(self):
        fm = _manager(_tar({"app/a.txt": b"Inhalt mit Umlaut: Groesse"}))
        daten, _ = fm.export_folder_zip("c1", "/workspace/app")
        with zipfile.ZipFile(io.BytesIO(daten)) as z:
            self.assertEqual(z.read("app/a.txt"), b"Inhalt mit Umlaut: Groesse")


class TheNoiseStaysOutTests(unittest.TestCase):
    """In einem Projektordner machen diese Verzeichnisse leicht das
    Tausendfache des eigentlichen Codes aus — und alles darin ist aus dem Rest
    wiederherstellbar."""

    def test_node_modules_is_skipped(self):
        fm = _manager(_tar({
            "app/index.js": b"x",
            "app/node_modules/left-pad/index.js": b"y" * 5000,
        }))
        daten, anzahl = fm.export_folder_zip("c1", "/workspace/app")
        self.assertEqual(anzahl, 1)
        with zipfile.ZipFile(io.BytesIO(daten)) as z:
            self.assertNotIn("app/node_modules/left-pad/index.js", z.namelist())

    def test_the_usual_suspects_are_covered(self):
        for ordner in ("node_modules", ".git", "__pycache__", ".venv"):
            with self.subTest(ordner=ordner):
                self.assertIn(ordner, EXPORT_AUSGENOMMEN)

    def test_a_nested_occurrence_is_caught_too(self):
        """Nicht nur ganz oben — `packages/x/node_modules` ist derselbe Fall."""
        self.assertTrue(_ist_ausgenommen("app/packages/x/node_modules/y/index.js"))

    def test_a_harmless_name_is_not_caught(self):
        """`.gitignore` ist eine Datei und gehoert dazu — nur das Verzeichnis
        `.git` fliegt raus."""
        self.assertFalse(_ist_ausgenommen("app/.gitignore"))
        self.assertFalse(_ist_ausgenommen("app/src/build.ts"))


class ItRefusesWhatItCannotCarryTests(unittest.TestCase):
    def test_an_oversized_folder_is_refused_with_a_readable_reason(self):
        """Lieber eine Meldung als ein Speicherfehler oder ein Zeitablauf."""
        fm = _manager(_tar({"app/gross.bin": b"x" * 1024}))
        # Grenze kuenstlich unterschreiten, ohne 500 MB zu erzeugen.
        import app.core.file_manager as fmod
        alt = fmod.MAX_EXPORT_BYTES
        fmod.MAX_EXPORT_BYTES = 100
        try:
            with self.assertRaises(ExportZuGross) as fall:
                fm.export_folder_zip("c1", "/workspace/app")
        finally:
            fmod.MAX_EXPORT_BYTES = alt
        self.assertIn("MB", str(fall.exception))

    def test_the_limit_is_sane(self):
        self.assertGreater(MAX_EXPORT_BYTES, 50 * 1024 * 1024)

    def test_the_path_is_jailed_like_everywhere_else(self):
        """Derselbe Riegel wie beim Lesen, Schreiben und Hochladen — kein
        zweiter, eigener Weg."""
        fm = _manager(_tar({"x": b"y"}))
        with self.assertRaises(ValueError):
            fm.export_folder_zip("c1", "/etc")


class BothSurfacesUseTheSameEndpointTests(unittest.TestCase):
    from pathlib import Path
    WURZEL = Path(__file__).resolve().parents[2]
    API = (WURZEL / "orchestrator/app/api/agents.py").read_text()
    APPS = (WURZEL / "frontend/src/app/apps/page.tsx").read_text()
    BAUM_AGENT = (WURZEL / "frontend/src/app/agents/[id]/page.tsx").read_text()
    BAUM_DATEIEN = (WURZEL / "frontend/src/app/files/page.tsx").read_text()

    def test_the_endpoint_is_guarded_like_the_other_file_routes(self):
        rumpf = self.API.split("async def download_folder(", 1)
        self.assertEqual(len(rumpf), 2, "Endpunkt fehlt")
        self.assertIn("_check_owner(agent_id, user, db)", rumpf[1][:900])

    def test_the_apps_overview_offers_it(self):
        self.assertIn("getFolderDownloadUrl(app.agent_id", self.APPS)

    def test_both_file_trees_offer_it(self):
        for name, quelle in (("agents/[id]", self.BAUM_AGENT), ("files", self.BAUM_DATEIEN)):
            with self.subTest(seite=name):
                self.assertIn("getFolderDownloadUrl(agentId, entry.path)", quelle)

    def test_the_exclusions_are_named_in_the_ui(self):
        """Wer exportiert, soll wissen, dass node_modules fehlt — sonst wundert
        er sich beim Entpacken."""
        self.assertIn("node_modules", self.APPS)


if __name__ == "__main__":
    unittest.main()
