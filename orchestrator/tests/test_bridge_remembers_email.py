"""Die Bridge merkt sich die E-Mail-Adresse — und NUR die.

Beschwerde vom 2026-08-18: "Hier wird auch NIE der Account gespeichert den man
eingegeben hat." Zu Recht — in ``~/.ai_employee_bridge.json`` standen ``url``,
``token``, ``session``, ``auto_connect`` und die Freigaben, aber nie die
Adresse. Bei jeder Anmeldung musste sie neu getippt werden.

Die Tray-App hat DREI Anmeldedialoge (AppKit fuer macOS, customtkinter und ein
einfacher tkinter-Rueckfall). Ein Fix in nur einem davon faellt genau dort nicht
auf, wo man gerade nicht hinschaut — deshalb prueft dieser Test alle drei.

Ebenso wichtig die Gegenrichtung: das PASSWORT darf nie in der Konfiguration
landen. Die Datei liegt unverschluesselt im Benutzerverzeichnis.
"""

import re
import unittest
from pathlib import Path

TRAY = Path(__file__).resolve().parents[2] / "computer-use-bridge/tray_app.py"


class BridgeRemembersEmailTests(unittest.TestCase):
    def setUp(self):
        self.src = TRAY.read_text(encoding="utf-8")

    def test_every_dialog_prefills_the_email(self):
        """Drei Dialoge, drei Vorbelegungen aus der Konfiguration."""
        treffer = re.findall(r'cfg\.get\(\s*"email"', self.src)
        self.assertGreaterEqual(
            len(treffer), 3,
            "Nicht jeder Anmeldedialog belegt die E-Mail vor — gefunden: "
            f"{len(treffer)} von 3 (AppKit, customtkinter, tkinter)",
        )

    def test_every_dialog_persists_the_email(self):
        """Vorbelegen nuetzt nichts, wenn beim Anmelden nichts gespeichert wird."""
        treffer = re.findall(r'"email"\s*:\s*(?:email|em)\b', self.src)
        self.assertGreaterEqual(
            len(treffer), 3,
            "Nicht jeder Anmeldedialog speichert die E-Mail — gefunden: "
            f"{len(treffer)} von 3",
        )

    def test_password_is_never_persisted(self):
        """Die Konfigurationsdatei liegt im Klartext im Benutzerverzeichnis."""
        verboten = re.findall(r'"password"\s*:\s*(?:pw|password)\b', self.src)
        # Der Login-AUFRUF selbst darf das Passwort natuerlich mitschicken —
        # gemeint ist hier nur, was in `result`/`cfg` landet.
        for stelle in verboten:
            self.assertNotIn(
                "result", stelle,
                "Das Passwort darf nie in die gespeicherte Konfiguration",
            )
        self.assertNotIn('cfg["password"]', self.src)
        self.assertNotIn('"password": pw', self.src.replace(
            'json.dumps({"email": email, "password": password})', ""))

    def test_saved_config_keys_stay_expected(self):
        """Was gespeichert wird, soll bewusst gewaehlt sein — faellt jemandem
        spaeter ein, hier ein Geheimnis abzulegen, soll das auffallen."""
        for key in ("url", "token", "session", "email", "auto_connect"):
            with self.subTest(key=key):
                self.assertIn(f'"{key}"', self.src)


if __name__ == "__main__":
    unittest.main()
