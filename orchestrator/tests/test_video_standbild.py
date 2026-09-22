"""Vorschaubild fuer Videos im Gespraech — ohne das Video zu uebertragen.

Am 22.09.2026 gemeldet: Ein Videoanhang war nur eine Kachel mit Dateinamen.
Man sah nicht, was drin ist, und musste 6 MB laden, um es herauszufinden.

Das Standbild wird deshalb dort erzeugt, wo Werkzeug und Datei ohnehin liegen:
mit ``ffmpeg`` IM Agenten-Container. An einem echten Video nachgemessen:
6347 KB Video -> 21 KB Standbild (480x854).

Zwei Dinge, die dabei leicht schiefgehen:

* **Schwarzes Bild.** Der allererste Bildinhalt ist bei vielen Videos noch
  schwarz, deshalb wird bei 0,5 s gesucht. Ist das Video kuerzer, liegt dort
  gar nichts mehr — dann muss ein zweiter Versuch ohne Vorspulen folgen,
  sonst bekaeme man fuer kurze Videos nie eine Vorschau.
* **Muell im Container.** Das Standbild entsteht als Datei unter /tmp. Wird
  sie nicht wieder entfernt, sammelt sich das im Arbeitsbereich an — der
  ohnehin unter Kontingent steht.
"""

import unittest
from unittest.mock import MagicMock

from app.core.file_manager import FileManager


class _Docker:
    """Docker-Attrappe, die mitschreibt und den Fehlschlag steuern laesst."""

    def __init__(self, ffmpeg_codes=(0,)):
        self.befehle = []
        self._codes = list(ffmpeg_codes)
        self.gelesen = None

    def exec_in_container(self, container_id, cmd, user=None):
        self.befehle.append(cmd)
        if isinstance(cmd, list) and cmd and cmd[0] == "bash":
            return 0, "OK"          # Symlink-Pruefung
        if isinstance(cmd, list) and cmd and cmd[0] == "ffmpeg":
            return (self._codes.pop(0) if self._codes else 0), "fehlertext"
        return 0, ""

    def get_file_from_container(self, container_id, pfad):
        self.gelesen = pfad
        return b"\xff\xd8JPEG"

    def _ffmpeg_aufrufe(self):
        return [c for c in self.befehle if isinstance(c, list) and c and c[0] == "ffmpeg"]


class VideoStandbildTest(unittest.TestCase):

    def _manager(self, docker):
        fm = FileManager.__new__(FileManager)
        fm.docker = docker
        return fm

    def test_es_kommt_ein_bild_zurueck(self):
        d = _Docker()
        bild = self._manager(d).video_standbild("c1", "/workspace/a.mp4")
        self.assertEqual(bild[:2], b"\xff\xd8", "Kein JPEG zurueckgegeben")

    def test_es_wird_nicht_am_anfang_gesucht(self):
        """Bild 0 ist bei vielen Videos noch schwarz."""
        d = _Docker()
        self._manager(d).video_standbild("c1", "/workspace/a.mp4")
        aufruf = d._ffmpeg_aufrufe()[0]
        self.assertIn("-ss", aufruf)
        self.assertNotEqual(aufruf[aufruf.index("-ss") + 1], "0")

    def test_bei_sehr_kurzem_video_wird_ohne_vorspulen_erneut_versucht(self):
        """Sonst bekaemen kurze Videos nie eine Vorschau."""
        d = _Docker(ffmpeg_codes=(1, 0))      # erster Versuch scheitert
        bild = self._manager(d).video_standbild("c1", "/workspace/kurz.mp4")
        self.assertEqual(bild[:2], b"\xff\xd8")
        aufrufe = d._ffmpeg_aufrufe()
        self.assertEqual(len(aufrufe), 2, "Es gab keinen zweiten Versuch")
        zweiter = aufrufe[1]
        self.assertEqual(zweiter[zweiter.index("-ss") + 1], "0")

    def test_scheitert_es_zweimal_gibt_es_einen_klaren_fehler(self):
        d = _Docker(ffmpeg_codes=(1, 1))
        with self.assertRaises(ValueError) as ctx:
            self._manager(d).video_standbild("c1", "/workspace/kaputt.mp4")
        self.assertIn("Standbild", str(ctx.exception))

    def test_das_zwischenbild_wird_wieder_entfernt(self):
        """Sonst waechst /tmp im Container mit jeder Vorschau."""
        d = _Docker()
        self._manager(d).video_standbild("c1", "/workspace/a.mp4")
        aufgeraeumt = [c for c in d.befehle if isinstance(c, list) and c[:2] == ["rm", "-f"]]
        self.assertEqual(len(aufgeraeumt), 1, f"Kein Aufraeumen: {d.befehle}")
        self.assertEqual(aufgeraeumt[0][2], d.gelesen,
                         "Es wurde eine andere Datei entfernt als gelesen wurde.")

    def test_symlinks_werden_abgelehnt(self):
        """Gleiche Absicherung wie beim Lesen — kein zweiter, eigener Weg."""
        d = _Docker()
        d.exec_in_container = MagicMock(return_value=(0, "SYMLINK"))
        with self.assertRaises(ValueError):
            self._manager(d).video_standbild("c1", "/workspace/link.mp4")

    def test_pfade_ausserhalb_des_arbeitsbereichs_werden_abgelehnt(self):
        d = _Docker()
        for pfad in ("/etc/passwd", "../../etc/passwd"):
            with self.subTest(pfad=pfad), self.assertRaises(ValueError):
                self._manager(d).video_standbild("c1", pfad)


if __name__ == "__main__":
    unittest.main()
