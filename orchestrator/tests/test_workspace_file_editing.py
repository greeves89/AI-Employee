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

import re
import unittest
from unittest.mock import MagicMock

from app.core.file_manager import MAX_EDIT_SIZE_BYTES, FileManager


def _ohne_js_kommentare(quelle: str) -> str:
    """Block- und Zeilenkommentare tilgen, damit ein auskommentierter Aufruf
    nicht als vorhanden zaehlt (und ein auskommentiertes Verbot nicht als
    Verstoss)."""
    quelle = re.sub(r"/\*.*?\*/", "", quelle, flags=re.S)
    return "\n".join(
        z for z in quelle.splitlines() if not z.lstrip().startswith("//"))


def _js_block(quelle: str, marke: str) -> str:
    """Quelltext ab ``marke`` bis zum Ende des umschliessenden ``{...}``-Blocks.

    Eine syntaktische Grenze statt eines Zeichenfensters: der Test fragt
    "steht der Aufruf im SELBEN Zweig?", nicht "steht er in Reichweite?".
    """
    quelle = _ohne_js_kommentare(quelle)
    start = quelle.index(marke)
    tiefe = 0
    eigene_ebene = []
    for z in quelle[start:]:
        if z == "{":
            tiefe += 1
        elif z == "}":
            if tiefe == 0:
                return "".join(eigene_ebene)
            tiefe -= 1
        elif tiefe == 0:
            #: Verschachtelte Bloecke (innere Funktionen, if/try) gehoeren
            #: NICHT zum Zweig — sonst zaehlt ein Aufruf, der nur in einem
            #: Unterfall laeuft, als "im selben Zweig".
            eigene_ebene.append(z)
    raise AssertionError(f"kein schliessender Block nach {marke!r}")


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


class TheEndpointIsGuardedTests(unittest.IsolatedAsyncioTestCase):
    """Der Schreibweg muss dieselben Wachen haben wie das Herunterladen —
    sonst kann ein Fremder in einen fremden Arbeitsbereich schreiben.

    Frueher stand hier ein 1400-Zeichen-Fenster ueber dem Quelltext des
    Endpunkts: das beweist nur, dass eine Zeile in der Naehe STEHT, nicht dass
    sie beim Aufruf auch GILT — ein auskommentierter ``_check_owner``-Aufruf
    haette das Fenster klaglos bestanden. Jetzt wird der Endpunkt wirklich
    aufgerufen, mit Attrappen fuer Besitzpruefung, Manager und FileManager."""

    async def _call(self, *, check_owner_error=None, file_mgr=None):
        from unittest.mock import AsyncMock, MagicMock, patch

        from app.api.agents import DateiInhalt, save_file_content

        check_owner = AsyncMock(side_effect=check_owner_error)
        manager = MagicMock()
        manager._get_agent = AsyncMock(return_value=MagicMock(container_id="c1"))
        file_mgr = file_mgr if file_mgr is not None else MagicMock(
            write_file=MagicMock(return_value=5))
        body = DateiInhalt(path="/workspace/x.txt", content="hallo")
        with patch("app.api.agents._check_owner", check_owner):
            ergebnis = await save_file_content(
                "a1", body, user=object(), db=object(),
                manager=manager, file_mgr=file_mgr,
            )
        return check_owner, manager, file_mgr, ergebnis

    async def test_it_requires_a_login(self):
        """``Depends(require_auth)`` ist eine Zusage der echten Signatur, nicht
        Text in der Naehe — hier wird die Signatur selbst gelesen."""
        import inspect

        from app.api.agents import save_file_content
        from app.dependencies import require_auth

        sig = inspect.signature(save_file_content)
        self.assertIs(sig.parameters["user"].default.dependency, require_auth)

    async def test_a_failed_ownership_check_stops_the_write(self):
        """Schlaegt die Besitzpruefung fehl, darf ueberhaupt nichts geschrieben
        werden. Waere der Aufruf auskommentiert, schriebe ein Fremder anstandslos —
        genau die Luecke, die ein Zeichenfenster nicht sieht."""
        from fastapi import HTTPException

        with self.assertRaises(HTTPException) as gefangen:
            await self._call(check_owner_error=HTTPException(status_code=403, detail="Access denied"))
        self.assertEqual(gefangen.exception.status_code, 403)

    async def test_it_goes_through_the_file_manager_not_straight_to_docker(self):
        """Der Weg fuehrt ueber ``FileManager.write_file`` — nicht daran vorbei
        zu einem rohen ``exec_run``, wo keine der Pfad-Pruefungen sitzt."""
        _, manager, file_mgr, ergebnis = await self._call()
        manager._get_agent.assert_awaited_once_with("a1")
        file_mgr.write_file.assert_called_once_with("c1", "/workspace/x.txt", "hallo")
        self.assertEqual(ergebnis, {"path": "/workspace/x.txt", "bytes": 5})

    async def test_a_rejected_path_becomes_a_400_not_a_crash(self):
        """``FileManager.write_file`` entscheidet per ``ValueError`` — der
        Endpunkt muss diese Ablehnung als 400 weiterreichen."""
        from unittest.mock import MagicMock

        from fastapi import HTTPException

        file_mgr = MagicMock(write_file=MagicMock(side_effect=ValueError("raus")))
        with self.assertRaises(HTTPException) as gefangen:
            await self._call(file_mgr=file_mgr)
        self.assertEqual(gefangen.exception.status_code, 400)


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
        #: Derselbe Effekt, der die Ansicht zuruecksetzt, muss auch den
        #: Entwurf verwerfen — nicht "irgendwo in den naechsten 200 Zeichen".
        block = _js_block(self.VORSCHAU, 'setHtmlTab("rendered");')
        self.assertIn("setEntwurf(null)", block)

    def test_saving_is_offered_in_both_file_trees(self):
        """Ohne `agentId` bleibt die Ansicht lesend — beide Baeume muessen ihn
        durchreichen, sonst kann man nur in einem bearbeiten."""
        self.assertIn("agentId={agentId}", self.AGENT)
        self.assertIn("agentId={selectedFile.agentId}", self.DATEIEN)

    def test_a_failed_save_is_shown_and_the_text_is_kept(self):
        """Der Entwurf darf beim Fehlschlag nicht verlorengehen."""
        #: Der catch-Zweig, der den Fehler meldet, darf den Entwurf nicht
        #: verwerfen — geprueft ueber den ganzen Zweig, nicht ein Zeichenfenster.
        catch_zweig = _js_block(self.VORSCHAU, "setSpeicherFehler(e instanceof Error")
        self.assertNotIn("setEntwurf(null)", catch_zweig)
        self.assertIn("{speicherFehler}", self.VORSCHAU)
