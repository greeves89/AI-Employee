"""Gespiegelte Ordner (#1092): die Bridge liest nur in freigegebenen Ordnern.

Die Plattform fragt nach Liste und Inhalt; die Bridge antwortet nur innerhalb der
Ordner, die der Nutzer unter Berechtigungen → Ordner-Zugriff freigegeben hat —
ohne versteckte Ordner, ohne symbolische Links, mit Größengrenzen.
"""

import base64
import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bridge  # noqa: E402


class WissenSpiegel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        basis = os.path.realpath(self.tmp.name)
        self.vault = os.path.join(basis, "vault")
        self.geheim = os.path.join(basis, "geheim")
        for pfad, inhalt in (
            ("vault/index.md", b"# Start"),
            ("vault/Kunden/notiz.txt", b"Telefon klaeren"),
            ("vault/Angebot.pdf", b"%PDF-1.4 kein echtes"),
            ("vault/bild.png", b"\x89PNG"),
            ("vault/.obsidian/workspace.json", b"{}"),
            ("vault/.git/config", b"[core]"),
            ("vault/.versteckt.md", b"nein"),
            ("vault/node_modules/paket/README.md", b"fremd"),
            ("geheim/passwoerter.md", b"nur lokal"),
        ):
            ziel = os.path.join(basis, pfad)
            os.makedirs(os.path.dirname(ziel), exist_ok=True)
            with open(ziel, "wb") as fh:
                fh.write(inhalt)
        os.symlink(os.path.join(self.geheim, "passwoerter.md"), os.path.join(self.vault, "link.md"))
        os.symlink(self.geheim, os.path.join(self.vault, "ordnerlink"))
        p = patch.object(bridge, "allowed_shell_dirs", return_value=[self.vault])
        p.start()
        self.addCleanup(p.stop)

    def test_ordner_nennt_die_freigaben(self):
        self.assertEqual(bridge.wissen_ordner(), {"ok": True, "ordner": [self.vault]})

    def test_liste_nur_spiegelbare_dateien_mit_pruefsumme(self):
        r = bridge.wissen_liste(self.vault)
        self.assertTrue(r["ok"])
        pfade = sorted(d["pfad"] for d in r["dateien"])
        self.assertEqual(pfade, ["Angebot.pdf", "Kunden/notiz.txt", "index.md"])
        index = next(d for d in r["dateien"] if d["pfad"] == "index.md")
        self.assertEqual(index["sha"], hashlib.sha256(b"# Start").hexdigest())
        self.assertEqual(index["bytes"], 7)

    def test_nur_genau_der_freigegebene_ordner(self):
        # Auch kein Unterordner: der koennte ein versteckter sein (~/.ssh unter ~).
        for ordner in (self.geheim, os.path.join(self.vault, "Kunden"), os.path.join(self.vault, ".obsidian"),
                       os.path.join(self.vault, "ordnerlink"), "", None, "/"):
            with self.subTest(ordner=ordner):
                self.assertFalse(bridge.wissen_liste(ordner)["ok"])
                self.assertFalse(bridge.wissen_datei(ordner, "passwoerter.md")["ok"])

    def test_ohne_freigabe_nichts(self):
        with patch.object(bridge, "allowed_shell_dirs", return_value=[]):
            self.assertFalse(bridge.wissen_liste(self.vault)["ok"])
            self.assertFalse(bridge.wissen_datei(self.vault, "index.md")["ok"])

    def test_datei_liefert_inhalt(self):
        r = bridge.wissen_datei(self.vault, "Kunden/notiz.txt")
        self.assertEqual(base64.b64decode(r["inhalt_b64"]), b"Telefon klaeren")

    def test_datei_bleibt_im_ordner(self):
        for pfad in ("../geheim/passwoerter.md", "link.md", "ordnerlink/passwoerter.md", ".obsidian/workspace.json",
                     "node_modules/paket/README.md",
                     ".git/config", os.path.join(self.geheim, "passwoerter.md"), "bild.png", "fehlt.md", "a\x00.md"):
            with self.subTest(pfad=pfad):
                self.assertFalse(bridge.wissen_datei(self.vault, pfad)["ok"])

    def test_zu_grosse_dateien_fehlen_in_der_liste(self):
        with open(os.path.join(self.vault, "gross.md"), "wb") as fh:
            fh.write(b"x" * (bridge.WISSEN_MAX_TEXT_BYTES + 1))
        pfade = [d["pfad"] for d in bridge.wissen_liste(self.vault)["dateien"]]
        self.assertNotIn("gross.md", pfade)
        self.assertFalse(bridge.wissen_datei(self.vault, "gross.md")["ok"])

    def test_ueber_den_dispatcher_erreichbar(self):
        d = bridge.CommandDispatcher.__new__(bridge.CommandDispatcher)
        r = d.dispatch({"action": "wissen_liste", "params": {"ordner": self.vault}})
        self.assertTrue(r["ok"])


    def test_getauschte_datei_wird_nicht_gelesen(self):
        """Zwischen Liste und Abholen gegen einen Link getauscht: nicht lesen."""
        ziel = os.path.join(self.vault, "index.md")
        os.remove(ziel)
        os.symlink(os.path.join(self.geheim, "passwoerter.md"), ziel)
        self.assertFalse(bridge.wissen_datei(self.vault, "index.md")["ok"])
        with self.assertRaises(OSError):
            os.close(bridge._wissen_oeffnen(ziel))


if __name__ == "__main__":
    unittest.main()
