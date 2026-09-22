"""Ein Video muss im Chat als Video ankommen, nicht als namenlose Datei.

Am 22.09.2026 lieferte der Agent zwei erzeugte Videos in den Chat. In der
Datenbank standen sie mit ``media_type=application/octet-stream``:

    finn_test_cinemastudio3_14s.mp4   media_type=application/octet-stream
    finn_test_seedance26_26s.mp4      media_type=application/octet-stream

Die Tabelle in ``notification-server.mjs`` kannte PDF, Office, ZIP und Bilder
-- aber **kein einziges Video- oder Tonformat**. Alles andere fiel auf
``application/octet-stream``, und die Oberflaeche konnte daran nicht erkennen,
dass hier etwas Abspielbares ankommt.

Die Oberflaeche prueft deshalb zusaetzlich die Dateiendung; verlassen sollte
sie sich darauf aber nicht muessen.
"""

import re
import unittest
from pathlib import Path

_SERVER = Path(__file__).resolve().parents[1] / "mcp" / "notification-server.mjs"


def _tabelle() -> dict[str, str]:
    """Die Zuordnung aus dem Quelltext lesen -- ohne Node auszufuehren."""
    quelle = _SERVER.read_text(encoding="utf-8")
    block = re.search(r"const types = \{(.*?)\};", quelle, re.S)
    assert block, "Zuordnungstabelle nicht gefunden"
    return dict(re.findall(r'"(\.[a-z0-9]+)":\s*"([^"]+)"', block.group(1)))


class MedientypenTest(unittest.TestCase):

    def setUp(self):
        self.typen = _tabelle()

    def test_die_tabelle_wurde_gefunden(self):
        """Sonst pruefen die folgenden Vergleiche ein leeres Verzeichnis."""
        self.assertGreater(len(self.typen), 10)

    def test_videoformate_werden_als_video_gemeldet(self):
        for endung in (".mp4", ".webm", ".mov", ".m4v"):
            with self.subTest(endung=endung):
                self.assertIn(endung, self.typen,
                              f"{endung} fehlt — ein solches Video kommt als "
                              f"application/octet-stream an und gilt in der "
                              f"Oberflaeche nicht als abspielbar.")
                self.assertTrue(self.typen[endung].startswith("video/"),
                                f"{endung} -> {self.typen[endung]}")

    def test_tonformate_werden_als_ton_gemeldet(self):
        for endung in (".mp3", ".m4a", ".wav", ".ogg"):
            with self.subTest(endung=endung):
                self.assertIn(endung, self.typen)
                self.assertTrue(self.typen[endung].startswith("audio/"),
                                f"{endung} -> {self.typen[endung]}")

    def test_bilder_bleiben_unveraendert(self):
        """Wache: Die Ergaenzung darf nichts Bestehendes verdrehen."""
        self.assertEqual(self.typen[".png"], "image/png")
        self.assertEqual(self.typen[".pdf"], "application/pdf")


class OberflaecheErkenntVideosAuchOhneTyp(unittest.TestCase):
    """Doppelte Absicherung: Alte Anhaenge tragen weiterhin den falschen Typ.

    In der Datenbank stehen bereits Videos mit ``application/octet-stream``.
    Die lassen sich nicht nachtraeglich reparieren, also muss die Oberflaeche
    auch an der Dateiendung erkennen, dass etwas abspielbar ist.
    """

    def test_die_oberflaeche_prueft_auch_die_endung(self):
        chat = (Path(__file__).resolve().parents[2] / "frontend" / "src"
                / "components" / "agents" / "chat.tsx")
        if not chat.exists():
            self.skipTest("Frontend nicht vorhanden")
        quelle = chat.read_text(encoding="utf-8")
        treffer = re.search(r"function isVideoFile\(file: ChatFile\) \{(.*?)\n\}", quelle, re.S)
        self.assertIsNotNone(treffer, "isVideoFile nicht gefunden")
        koerper = treffer.group(1)
        self.assertIn('media_type?.startsWith("video/")', koerper)
        self.assertIn("mp4", koerper,
                      "Ohne Endungspruefung bleiben die bereits gespeicherten "
                      "Videos fuer immer nur Download-Kacheln.")


if __name__ == "__main__":
    unittest.main()
