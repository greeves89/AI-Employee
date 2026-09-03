"""Das Browser-Profil der Bridge darf nur dem angemeldeten Nutzer gehoeren.

Nach der Einmal-Anmeldung liegen dort Sitzungs-Cookies und Anmeldedaten. Mit der
Vorgabe von ``os.makedirs`` (0755 nach umask) koennte jeder andere lokale Account
sie mitlesen — genau der Diebstahl, gegen den die Chrome/Edge-136-Haertung gebaut
wurde, nur eine Ebene tiefer. Damit waere die Begruendung des ganzen Entwurfs
("kein Cookie-Import aus dem privaten Profil") hinfaellig.

Geprueft wird die ECHTE Methode ``_prepare_profile_dir`` — nicht eine Kopie ihrer
Logik und nicht der Quelltext.
"""

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

BRIDGE = Path(__file__).resolve().parents[2] / "computer-use-bridge/bridge.py"


def _load_browser_controller():
    """Nur die BrowserController-Klasse ausfuehren — bridge.py als Ganzes
    braucht pyautogui/pynput, die hier nicht installiert sind."""
    src = BRIDGE.read_text(encoding="utf-8")
    block = src[src.index("BROWSER_PROFILE_DIR ="):src.index("def list_windows()")]
    ns: dict = {
        "os": os, "json": __import__("json"), "time": __import__("time"),
        "queue": __import__("queue"), "threading": __import__("threading"),
        "base64": __import__("base64"),
    }
    exec(compile(block, "bridge_browser", "exec"), ns)  # noqa: S102
    return ns["BrowserController"]


@unittest.skipIf(sys.platform.startswith("win"), "POSIX-Rechte gibt es unter Windows nicht")
class BrowserProfilePermissionTests(unittest.TestCase):
    def setUp(self):
        self.BrowserController = _load_browser_controller()

    def _mode(self, path: str) -> int:
        return stat.S_IMODE(os.stat(path).st_mode)

    def test_new_profile_dir_is_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = os.path.join(tmp, "browser-profile")
            self.BrowserController(profile_dir=profile)._prepare_profile_dir()

            self.assertTrue(os.path.isdir(profile))
            self.assertEqual(
                self._mode(profile) & 0o077, 0,
                f"Profil ist fuer Gruppe/andere zugaenglich ({oct(self._mode(profile))}) "
                "— dort liegen Sitzungs-Cookies",
            )

    def test_existing_wide_open_profile_dir_is_tightened(self):
        """Der Fall, den ``mode=`` allein NICHT abdeckt: das Verzeichnis gibt es
        schon (z. B. aus einer aelteren Fassung) und ist weltlesbar."""
        with tempfile.TemporaryDirectory() as tmp:
            profile = os.path.join(tmp, "browser-profile")
            os.makedirs(profile)
            os.chmod(profile, 0o755)
            self.assertNotEqual(self._mode(profile) & 0o077, 0, "Vorbedingung")

            self.BrowserController(profile_dir=profile)._prepare_profile_dir()

            self.assertEqual(
                self._mode(profile) & 0o077, 0,
                "Ein bereits vorhandenes, zu weit geoeffnetes Profil muss "
                "nachtraeglich verengt werden",
            )

    def test_umask_cannot_widen_the_profile_dir(self):
        """Eine grosszuegige umask darf die Zusage nicht aushebeln — genau
        deshalb steht neben ``mode=`` noch ein ``chmod``."""
        old = os.umask(0)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                profile = os.path.join(tmp, "browser-profile")
                self.BrowserController(profile_dir=profile)._prepare_profile_dir()
                self.assertEqual(self._mode(profile) & 0o077, 0)
        finally:
            os.umask(old)


if __name__ == "__main__":
    unittest.main()
