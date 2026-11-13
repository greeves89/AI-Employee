"""Startskript: Zertifizierungsstelle der Firma NUR für freigegebene Git-Server (#1194).

Gemessen an echten git-Einstellungen in einer Testdatei und einer Attrappe von
update-ca-certificates, wie test_entrypoint_egress_ca.
"""
import os
import subprocess
import tempfile
import unittest

_ENTRYPOINT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "entrypoint.sh")
PEM = "-----BEGIN CERTIFICATE-----\nMIIBattrappe\n-----END CERTIFICATE-----"


class GitCaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin)
        self.protokoll = os.path.join(self.tmp, "aufrufe")
        with open(os.path.join(self.bin, "update-ca-certificates"), "w") as f:
            f.write(f"#!/bin/sh\necho lauf >> {self.protokoll}\nexit 0\n")
        os.chmod(os.path.join(self.bin, "update-ca-certificates"), 0o700)
        self.system = os.path.join(self.tmp, "system.crt")
        with open(self.system, "w") as f:
            f.write("SYSTEMSPEICHER\n")
        self.gitconfig = os.path.join(self.tmp, "gitconfig")
        self.buendel = os.path.join(self.tmp, "gitca", "buendel.crt")

    def _lauf(self, ca: str | None, hosts: str | None = "gitlab.example.com,git2.example.com:8443"):
        env = {"PATH": f"{self.bin}:/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "ENTRYPOINT_NUR_DEFINIEREN": "1",
               "ENTRYPOINT_CA_DIR": os.path.join(self.tmp, "ca"),
               "ENTRYPOINT_GIT_CA_DIR": os.path.join(self.tmp, "gitca"),
               "ENTRYPOINT_GIT_SYSTEM": self.gitconfig, "ENTRYPOINT_SYSTEM_BUNDLE": self.system,
               "HOME": self.tmp}
        if ca is not None:
            env["AIE_GIT_CA"] = ca
        if hosts is not None:
            env["AIE_GIT_CA_HOSTS"] = hosts
        r = subprocess.run(["bash", "-c", f"set -eu; . {_ENTRYPOINT}; git_ca_einspielen"],
                           env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        return r

    def _git(self) -> str:
        if not os.path.exists(self.gitconfig):
            return ""
        return subprocess.run(["git", "config", "--file", self.gitconfig, "--list"],
                              capture_output=True, text=True).stdout

    def test_nur_fuer_die_freigegebenen_server_nicht_im_systemspeicher(self):
        self._lauf(PEM)
        einstellungen = self._git()
        self.assertIn(f"http.https://gitlab.example.com/.sslcainfo={self.buendel}", einstellungen)
        self.assertIn(f"http.https://git2.example.com:8443/.sslcainfo={self.buendel}", einstellungen)
        self.assertEqual(einstellungen.count("sslcainfo"), 2)
        inhalt = open(self.buendel).read()
        self.assertTrue(inhalt.startswith("SYSTEMSPEICHER"))
        self.assertIn(PEM, inhalt)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "ca", "aie-git-ca.crt")))
        self.assertFalse(os.path.exists(self.protokoll))   # Systemspeicher unberührt

    def test_entzug_raeumt_auf_fremde_einstellungen_bleiben(self):
        self._lauf(PEM)
        subprocess.run(["git", "config", "--file", self.gitconfig, "http.https://andere.example/.sslCAInfo",
                        "/etc/eigene.crt"], check=True)
        self._lauf(None, None)
        self.assertNotIn(self.buendel, self._git())
        self.assertIn("andere.example", self._git())
        self.assertFalse(os.path.exists(self.buendel))

    def test_alte_fassung_im_systemspeicher_wird_entfernt(self):
        os.makedirs(os.path.join(self.tmp, "ca"))
        alt = os.path.join(self.tmp, "ca", "aie-git-ca.crt")
        open(alt, "w").write(PEM)
        self._lauf(None, None)
        self.assertFalse(os.path.exists(alt))
        self.assertEqual(open(self.protokoll).read().count("lauf"), 1)

    def test_schluessel_unsinn_oder_ohne_server_wird_nicht_eingespielt(self):
        for wert, hosts in (("-----BEGIN PRIVATE KEY-----\nx\n", "g.example"),
                            (PEM + "\n-----BEGIN PRIVATE KEY-----\nx\n", "g.example"),
                            ("kaputt", "g.example"), (PEM, None)):
            with self.subTest(wert=wert[:20], hosts=hosts):
                r = self._lauf(wert, hosts)
                self.assertNotIn("sslcainfo", self._git())
                self.assertIn("WARNUNG", r.stdout)

    def test_unsaubere_servernamen_werden_uebersprungen(self):
        r = self._lauf(PEM, "gitlab.example.com,böse/.x,x;y.example,$(id).example")
        self.assertEqual(self._git().count("sslcainfo"), 1)
        self.assertIn("WARNUNG", r.stdout)


if __name__ == "__main__":
    unittest.main()
