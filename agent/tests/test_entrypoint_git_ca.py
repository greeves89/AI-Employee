"""Startskript: Zertifizierungsstelle der Firma für eigene Git-Server (#1194) in den Systemspeicher.

Gemessen an einer Attrappe von update-ca-certificates, wie test_entrypoint_egress_ca.
"""
import os
import subprocess
import tempfile
import unittest

_ENTRYPOINT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "entrypoint.sh")
PEM = "-----BEGIN CERTIFICATE-----\nMIIBattrappe\n-----END CERTIFICATE-----"


class GitCaTests(unittest.TestCase):
    def _lauf(self, ca: str | None):
        tmp = tempfile.mkdtemp()
        bin_dir = os.path.join(tmp, "bin")
        os.makedirs(bin_dir)
        protokoll = os.path.join(tmp, "aufrufe")
        with open(os.path.join(bin_dir, "update-ca-certificates"), "w") as f:
            f.write(f"#!/bin/sh\necho lauf >> {protokoll}\nexit 0\n")
        os.chmod(os.path.join(bin_dir, "update-ca-certificates"), 0o700)
        env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "ENTRYPOINT_NUR_DEFINIEREN": "1",
               "ENTRYPOINT_CA_DIR": os.path.join(tmp, "ca")}
        if ca is not None:
            env["AIE_GIT_CA"] = ca
        r = subprocess.run(["bash", "-c", f". {_ENTRYPOINT}; git_ca_einspielen"],
                           env=env, capture_output=True, text=True, timeout=30)
        datei = os.path.join(tmp, "ca", "aie-git-ca.crt")
        inhalt = open(datei).read() if os.path.exists(datei) else None
        aufrufe = open(protokoll).read().count("lauf") if os.path.exists(protokoll) else 0
        return r, inhalt, aufrufe

    def test_zertifikat_wird_eingespielt(self):
        r, inhalt, aufrufe = self._lauf(PEM)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((inhalt, aufrufe), (PEM + "\n", 1))

    def test_ohne_stelle_nichts(self):
        self.assertEqual(self._lauf(None)[1:], (None, 0))

    def test_schluessel_oder_unsinn_wird_nicht_eingespielt(self):
        for wert in ("-----BEGIN PRIVATE KEY-----\nx\n", PEM + "\n-----BEGIN PRIVATE KEY-----\nx\n", "kaputt"):
            with self.subTest(wert=wert[:20]):
                r, inhalt, aufrufe = self._lauf(wert)
                self.assertEqual((r.returncode, inhalt, aufrufe), (0, None, 0))
                self.assertIn("WARNUNG", r.stdout)


if __name__ == "__main__":
    unittest.main()
