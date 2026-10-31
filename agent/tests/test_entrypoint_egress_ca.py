"""Startskript: CA-Zertifikat des Egress-Proxys in den Systemspeicher (orchestrator/app/core/egress.py).

Gemessen an einer Attrappe von update-ca-certificates, nicht am Skripttext.
"""
import os
import subprocess
import tempfile
import unittest

_ENTRYPOINT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "entrypoint.sh")
PEM = "-----BEGIN CERTIFICATE-----\nMIIBattrappe\n-----END CERTIFICATE-----"


class EgressCaTests(unittest.TestCase):
    def _lauf(self, ca: str | None, update_ok: bool = True):
        tmp = tempfile.mkdtemp()
        bin_dir = os.path.join(tmp, "bin")
        os.makedirs(bin_dir)
        protokoll = os.path.join(tmp, "aufrufe")
        with open(os.path.join(bin_dir, "update-ca-certificates"), "w") as f:
            f.write(f"#!/bin/sh\necho lauf >> {protokoll}\nexit {0 if update_ok else 1}\n")
        os.chmod(os.path.join(bin_dir, "update-ca-certificates"), 0o755)
        env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "ENTRYPOINT_NUR_DEFINIEREN": "1",
               "ENTRYPOINT_CA_DIR": os.path.join(tmp, "ca")}
        if ca is not None:
            env["AIE_EGRESS_CA"] = ca
        r = subprocess.run(["bash", "-c", f". {_ENTRYPOINT}; egress_ca_einspielen"],
                           env=env, capture_output=True, text=True, timeout=30)
        datei = os.path.join(tmp, "ca", "aie-egress.crt")
        inhalt = open(datei).read() if os.path.exists(datei) else None
        aufrufe = open(protokoll).read().count("lauf") if os.path.exists(protokoll) else 0
        return r, inhalt, aufrufe

    def test_zertifikat_wird_eingespielt(self):
        r, inhalt, aufrufe = self._lauf(PEM)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(inhalt, PEM + "\n")
        self.assertEqual(aufrufe, 1)

    def test_ohne_proxy_nichts(self):
        r, inhalt, aufrufe = self._lauf(None)
        self.assertEqual((r.returncode, inhalt, aufrufe), (0, None, 0))

    def test_kein_zertifikat_wird_nicht_eingespielt(self):
        r, inhalt, aufrufe = self._lauf("-----BEGIN PRIVATE KEY-----\nx\n")
        self.assertEqual((r.returncode, inhalt, aufrufe), (0, None, 0))
        self.assertIn("kein Zertifikat", r.stdout)

    def test_scheitern_haelt_den_start_nicht_auf(self):
        r, inhalt, aufrufe = self._lauf(PEM, update_ok=False)
        self.assertEqual(r.returncode, 0)
        self.assertIn("WARNUNG", r.stdout)


if __name__ == "__main__":
    unittest.main()
