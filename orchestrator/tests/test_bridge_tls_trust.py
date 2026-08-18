"""TLS: verifizieren statt abschalten — Pinning wie bei SSH.

Bis v1.238.x stand in bridge.py UND tray_app.py je ein globaler SSL-Kontext mit
``verify_mode = CERT_NONE``: jede Verbindung — Login samt Passwort, Token, alle
Befehle — war gegen einen Mitleser in der Mitte ungeschuetzt. Jetzt gilt:

* Oeffentlich gueltiges Zertifikat → System-Verifikation.
* Selbstsigniertes Zertifikat → beim Erstkontakt gepinnt (TOFU); danach wird
  im HANDSHAKE genau dieses Zertifikat verlangt, bevor irgendein Byte
  Nutzdaten fliesst.
* Geaendertes Zertifikat → harter Fehler mit beiden Fingerabdruecken; neu
  vertraut wird nur ueber die ausdrueckliche Neu-Anmeldung (repin_server).

Die Tests fahren einen echten TLS-Server mit selbstsigniertem Zertifikat hoch
und pruefen das VERHALTEN, nicht den Quelltext.
"""

import json
import os
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "computer-use-bridge"))
import bridge  # noqa: E402


def _make_cert(directory: str, name: str) -> tuple[str, str]:
    key = os.path.join(directory, f"{name}.key")
    crt = os.path.join(directory, f"{name}.crt")
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
         "-keyout", key, "-out", crt, "-days", "2",
         "-subj", "/CN=localhost"],
        check=True, capture_output=True,
    )
    return key, crt


class _TlsEchoServer:
    """Minimaler TLS-Server: Handshake, sonst nichts."""

    def __init__(self, certfile: str, keyfile: str):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile, keyfile)
        self._sock = socket.socket()
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(5)
        self._sock.settimeout(0.2)
        self.port = self._sock.getsockname()[1]
        self._ctx = ctx
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                tls = self._ctx.wrap_socket(conn, server_side=True)
                tls.close()
            except Exception:  # noqa: BLE001 — abgelehnte Handshakes gehoeren zum Test
                try:
                    conn.close()
                except OSError:
                    pass

    def close(self):
        self._stop.set()
        self._thread.join(timeout=2)
        self._sock.close()


class TlsTrustTests(unittest.TestCase):
    def setUp(self):
        self._orig_config = bridge.BRIDGE_CONFIG_PATH
        self._tmp = tempfile.TemporaryDirectory()
        bridge.BRIDGE_CONFIG_PATH = os.path.join(self._tmp.name, "bridge.json")
        key, crt = _make_cert(self._tmp.name, "eins")
        self.server = _TlsEchoServer(crt, key)
        self.url = f"https://127.0.0.1:{self.server.port}"

    def tearDown(self):
        self.server.close()
        bridge.BRIDGE_CONFIG_PATH = self._orig_config
        self._tmp.cleanup()

    def _config(self) -> dict:
        try:
            with open(bridge.BRIDGE_CONFIG_PATH, encoding="utf-8") as fh:
                return json.load(fh)
        except FileNotFoundError:
            return {}

    def test_first_contact_pins_and_handshake_succeeds(self):
        """TOFU: Erstkontakt mit selbstsigniertem Server → Pin + nutzbarer Kontext."""
        ctx = bridge.ssl_context_for(self.url)
        self.assertIsNotNone(ctx)
        tls_cfg = self._config().get("tls") or {}
        self.assertEqual(tls_cfg.get("mode"), "pinned")
        self.assertEqual(tls_cfg.get("host"), "127.0.0.1")
        self.assertTrue(tls_cfg.get("fingerprint"))
        # Der gelieferte Kontext muss den Handshake wirklich BESTEHEN —
        # verifiziert, nicht mit abgeschalteter Pruefung.
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
        with socket.create_connection(("127.0.0.1", self.server.port), timeout=5) as s:
            with ctx.wrap_socket(s, server_hostname="127.0.0.1"):
                pass

    def test_changed_certificate_is_a_hard_error(self):
        """Zertifikat getauscht (Server neu aufgesetzt ODER Mitleser) → Abbruch
        mit beiden Fingerabdruecken, kein stilles Neu-Pinnen."""
        bridge.ssl_context_for(self.url)  # Pin auf Zertifikat 1
        old_fp = self._config()["tls"]["fingerprint"]

        self.server.close()
        key2, crt2 = _make_cert(self._tmp.name, "zwei")
        self.server = _TlsEchoServer(crt2, key2)
        url2 = f"https://127.0.0.1:{self.server.port}"
        # Pin gilt pro Host — Port egal, gepinnt ist 127.0.0.1.
        with self.assertRaises(bridge.TlsTrustError) as caught:
            bridge.ssl_context_for(url2)
        message = str(caught.exception)
        self.assertIn("GEAENDERT", message)
        self.assertIn(bridge.format_fingerprint(old_fp)[:8], message)
        # Der alte Pin bleibt stehen — nichts wurde still ueberschrieben.
        self.assertEqual(self._config()["tls"]["fingerprint"], old_fp)

    def test_repin_is_the_only_way_to_trust_again(self):
        bridge.ssl_context_for(self.url)
        old_fp = self._config()["tls"]["fingerprint"]

        self.server.close()
        key2, crt2 = _make_cert(self._tmp.name, "zwei")
        self.server = _TlsEchoServer(crt2, key2)
        url2 = f"https://127.0.0.1:{self.server.port}"

        new_fp = bridge.repin_server(url2)
        self.assertNotEqual(new_fp, old_fp)
        self.assertEqual(self._config()["tls"]["fingerprint"], new_fp)
        ctx = bridge.ssl_context_for(url2)
        with socket.create_connection(("127.0.0.1", self.server.port), timeout=5) as s:
            with ctx.wrap_socket(s, server_hostname="127.0.0.1"):
                pass

    def test_plain_http_needs_no_context(self):
        self.assertIsNone(bridge.ssl_context_for("http://127.0.0.1:1"))

    def test_no_global_cert_none_left_anywhere(self):
        """Die alte Abkuerzung darf nicht zurueckkommen: kein MODULWEITER
        Kontext mit CERT_NONE mehr — weder in bridge.py noch in tray_app.py.
        (In Funktionen bleibt CERT_NONE fuer die Zertifikats-Probe erlaubt,
        die nichts sendet, und fuer den ausdruecklichen insecure-Modus.)"""
        for fname in ("bridge.py", "tray_app.py"):
            src = (Path(bridge.__file__).parent / fname).read_text(encoding="utf-8")
            self.assertNotIn("_ssl_ctx = ssl.create_default_context()", src,
                             f"{fname}: der globale unverifizierte Kontext ist zurueck")

    def test_insecure_mode_requires_explicit_config(self):
        """Der Notausgang existiert, aber nur von Hand: tls.mode=insecure."""
        with open(bridge.BRIDGE_CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump({"tls": {"mode": "insecure"}}, fh)
        ctx = bridge.ssl_context_for(self.url)
        self.assertEqual(ctx.verify_mode, ssl.CERT_NONE)


if __name__ == "__main__":
    unittest.main()
