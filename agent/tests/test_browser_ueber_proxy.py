"""Agenten-Browser ueber den Egress-Proxy (tools/browser.py, main.py, entrypoint.sh).

Chromium beachtet HTTPS_PROXY nicht und liest unter Linux seine eigene
NSS-Datenbank statt des Systemspeichers. Ohne beides ginge der Browser am Proxy
vorbei bzw. scheiterte an jedem Ziel, an dem der Proxy einen Key einsetzt.
"""
import json
import os
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from app.tools import browser

PROXY = "http://a1b2c3d4:tok%2Fen@ai-employee-egress-proxy:8080"
_ENTRYPOINT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "entrypoint.sh")


class ProxyEinstellungTests(unittest.TestCase):
    def test_aus_der_umgebung(self):
        with patch.dict(os.environ, {"HTTPS_PROXY": PROXY}, clear=False):
            p = browser.proxy_einstellung()
        self.assertEqual(p["server"], "http://ai-employee-egress-proxy:8080")
        self.assertEqual((p["username"], p["password"]), ("a1b2c3d4", "tok/en"))
        self.assertNotIn("tok", p["server"], "Chromium nimmt keine Anmeldung in der Server-Adresse")
        for intern in ("<local>", "10.0.0.0/8", "192.168.0.0/16", "*.internal"):
            self.assertIn(intern, p["bypass"])

    def test_ohne_proxy(self):
        umgebung = {k: v for k, v in os.environ.items() if k.lower() != "https_proxy"}
        with patch.dict(os.environ, umgebung, clear=True):
            self.assertIsNone(browser.proxy_einstellung())
            self.assertNotIn("--proxy-server", browser.mcp_browser_argumente())

    def test_mcp_argumente(self):
        with patch.dict(os.environ, {"HTTPS_PROXY": PROXY}, clear=False), \
                patch.object(browser, "_chromium_pfad", return_value="/usr/bin/chromium"), \
                patch.object(browser, "_KOPFLOS", True):
            a = browser.mcp_browser_argumente()
        self.assertEqual(a[a.index("--user-data-dir") + 1], browser.PROFIL_DIR)
        self.assertEqual(a[a.index("--executable-path") + 1], "/usr/bin/chromium")
        self.assertIn("--headless", a)
        self.assertIn("--no-sandbox", a)
        self.assertEqual(a[a.index("--proxy-server") + 1], PROXY)
        self.assertIn("<local>", a[a.index("--proxy-bypass") + 1])

    def test_mcp_wird_mit_trenner_eingetragen(self):
        """``claude mcp add <name> npx … --user-data-dir`` brach ab (unknown option)."""
        import app.main as hauptprogramm
        quelle = open(hauptprogramm.__file__).read()
        self.assertIn('mcp_args = ["--", "playwright", "npx", "@playwright/mcp@latest"', quelle)


class NssTests(unittest.TestCase):
    """entrypoint.sh: egress_ca_fuer_browser — gemessen an einer certutil-Attrappe."""

    def _lauf(self, mit_certutil=True):
        tmp = tempfile.mkdtemp()
        bin_dir = os.path.join(tmp, "bin")
        os.makedirs(bin_dir)
        protokoll = os.path.join(tmp, "aufrufe")
        if mit_certutil:
            with open(os.path.join(bin_dir, "certutil"), "w") as f:
                f.write(f'#!/bin/sh\necho "$@" >> {protokoll}\n')
            os.chmod(os.path.join(bin_dir, "certutil"), 0o700)
        ca_dir = os.path.join(tmp, "ca")
        os.makedirs(ca_dir)
        open(os.path.join(ca_dir, "aie-egress.crt"), "w").write("-----BEGIN CERTIFICATE-----\nx\n")
        env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "ENTRYPOINT_NUR_DEFINIEREN": "1",
               "ENTRYPOINT_CA_DIR": ca_dir, "ENTRYPOINT_NSS_DIR": os.path.join(tmp, "pki", "nssdb")}
        r = subprocess.run(["bash", "-c", f". {_ENTRYPOINT}; egress_ca_fuer_browser"],
                           env=env, capture_output=True, text=True, timeout=30)
        aufrufe = open(protokoll).read() if os.path.exists(protokoll) else ""
        return r, aufrufe

    def test_zertifikat_in_nss(self):
        r, aufrufe = self._lauf()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("-N --empty-password", aufrufe)               # Datenbank angelegt
        self.assertIn("-A -t C,, -n aie-egress -i", aufrufe)        # Zertifikat eingetragen

    def test_ohne_certutil_kein_abbruch(self):
        r, aufrufe = self._lauf(mit_certutil=False)
        self.assertEqual(r.returncode, 0)
        self.assertIn("certutil fehlt", r.stdout)


if __name__ == "__main__":
    unittest.main()
