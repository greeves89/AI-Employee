"""#853: Die Schreib-Helfer lehnen Dateinamen, die aus dem Zielordner zeigen, selbst ab.

Bisher hing der Schutz an den Aufrufern (``file_manager`` sanitisiert,
``skill_marketplace._push_skill_files_to_agent`` reicht Namen aus der Datenbank
ungeprueft durch). Geprueft wird am Helfer: unzulaessige Namen werden abgelehnt,
BEVOR der Zielordner vorbereitet oder irgendetwas in den Behaelter geschrieben
wird; legitime verschachtelte Namen gehen weiter durch.
"""

import unittest

from app.services.docker_service import (
    DockerService,
    UnzulaessigerDateiname,
    ZielordnerNichtVorbereitbar,
    _pruefe_dateinamen,
)

BOESE = [
    "../../etc/passwd",
    "../x",
    "a/../../b",
    "/etc/passwd",
    "a//b",
    "./x",
    "a/./b",
    "",
    "..",
    ".",
    "a\\..\\b",
    "a\x00b",
]
GUT = ["main.py", "meine-app/app/main.py", "skills/x/README.md", ".env.example", "a..b/c...txt"]


class _Attrappe(DockerService):
    """Ohne Docker: zeichnet nur auf, ob schon eine Seitenwirkung lief."""

    def __init__(self):  # noqa: D401 — bewusst kein super().__init__ (kein Docker)
        self.aufrufe = []

        class _Client:
            class containers:  # noqa: N801
                @staticmethod
                def get(cid):
                    raise AssertionError("darf bei unzulaessigem Namen nie erreicht werden")

        self.client = _Client()

    def prepare_target_dir(self, *a, **k):
        self.aufrufe.append(("prepare_target_dir", a))
        raise _Halt()


class _Halt(Exception):
    pass


class PruefungTests(unittest.TestCase):
    def test_boese_namen_werden_abgelehnt(self):
        for name in BOESE:
            with self.subTest(name=name), self.assertRaises(UnzulaessigerDateiname):
                _pruefe_dateinamen([name])

    def test_legitime_verschachtelte_namen_gehen_durch(self):
        _pruefe_dateinamen(GUT)  # wirft nicht

    def test_ablehnung_ist_ein_zielordner_fehler(self):
        # Aufrufer behandeln ZielordnerNichtVorbereitbar schon ("nichts geschrieben").
        self.assertTrue(issubclass(UnzulaessigerDateiname, ZielordnerNichtVorbereitbar))


class HelferTests(unittest.TestCase):
    def test_mehrere_dateien_abgelehnt_vor_jeder_seitenwirkung(self):
        d = _Attrappe()
        with self.assertRaises(UnzulaessigerDateiname):
            d.write_files_in_container("c1", "/workspace/skills/x",
                                       [("ok.txt", b"1"), ("../../etc/passwd", b"x")])
        self.assertEqual(d.aufrufe, [])

    def test_mehrere_dateien_legitim_erreicht_die_vorbereitung(self):
        d = _Attrappe()
        with self.assertRaises(_Halt):
            d.write_files_in_container("c1", "/workspace/app", [("meine-app/app/main.py", b"1")])
        self.assertEqual(len(d.aufrufe), 1)

    def test_einzelne_datei_mit_punktpunkt_im_pfad_abgelehnt(self):
        d = _Attrappe()
        for pfad in ("/workspace/../etc/passwd", "/workspace/x/..", "/workspace/"):
            with self.subTest(pfad=pfad), self.assertRaises(UnzulaessigerDateiname):
                d.write_file_in_container("c1", pfad, "inhalt")
        self.assertEqual(d.aufrufe, [])

    def test_einzelne_datei_legitim_erreicht_die_vorbereitung(self):
        d = _Attrappe()
        with self.assertRaises(_Halt):
            d.write_file_in_container("c1", "/workspace/knowledge.md", "inhalt")
        self.assertEqual(len(d.aufrufe), 1)


if __name__ == "__main__":
    unittest.main()
