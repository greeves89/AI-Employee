"""Die Stimme des Echtzeit-Gespraechs muss einstellbar sein — und gueltig bleiben.

Kundenwunsch 2026-08-05: „Kannst du bitte noch einbauen, dass ich die AWS-Stimme
auch anpassen kann." Das Backend konnte es laengst (`nova_sonic_voice` wird beim
Verbindungsaufbau gelesen), aber der Wert wurde von der Settings-API nicht
zurueckgegeben — die Oberflaeche konnte ihn also weder anzeigen noch setzen.

Die Falle dabei: Eine erfundene Stimm-ID laesst die Sitzung erst beim
Verbindungsaufbau scheitern, und dieser Fehler von AWS taucht nirgends im Log auf
(siehe v1.143.2). Deshalb haelt dieser Test die Liste an die AWS-Doku gebunden.
"""

import pathlib
import re
import unittest

FRONTEND = pathlib.Path(__file__).resolve().parents[2] / "frontend/src/components/settings/voice-settings.tsx"

# Stand docs.aws.amazon.com/nova/latest/userguide/available-voices.html (2026-08).
# matthew + tiffany sind polyglott (sprechen u.a. Deutsch).
AWS_VOICES = {
    "matthew", "tiffany", "amy", "olivia", "lupe", "carlos", "ambre", "florian",
    "lennart", "beatrice", "lorenzo", "tina", "carolina", "leo", "kiara", "arjun",
}


class NovaVoiceSettingTests(unittest.TestCase):
    def test_setting_is_writable(self):
        """Ohne Eintrag in der erlaubten Liste wird ein PATCH still verworfen."""
        from app.services.settings_service import ALLOWED_KEYS  # noqa: PLC0415
        self.assertIn("nova_sonic_voice", ALLOWED_KEYS)

    def test_setting_survives_the_patch_path(self):
        """Die eigentliche Falle: Die erlaubten Schluessel stehen an ZWEI Stellen.

        `nova_sonic_voice` stand in ALLOWED_KEYS des Service, fehlte aber in
        `_VOICE_FIELDS` des PATCH-Endpunkts — der Wert wurde still verworfen. Der
        Nutzer waehlte „tiffany", bekam „Gespeichert." und hoerte weiter Matthew.
        Zusaetzlich muss das Request-Schema das Feld kennen, sonst kommt es nicht an.
        """
        api = (pathlib.Path(__file__).resolve().parents[1] / "app/api/settings.py").read_text()
        self.assertIn('"nova_sonic_voice"', api,
                      "fehlt in _VOICE_FIELDS → PATCH verwirft den Wert still")

        from app.schemas.settings import SettingsUpdate  # noqa: PLC0415
        self.assertIn("nova_sonic_voice", SettingsUpdate.model_fields,
                      "fehlt im Request-Schema → Wert erreicht den Endpunkt nie")

    def test_setting_is_returned_to_the_ui(self):
        """Genau das fehlte: Der Sprach-Layer las den Wert, die Oberflaeche sah ihn nie."""
        from app.schemas.settings import VoiceSettings  # noqa: PLC0415
        self.assertIn("nova_sonic_voice", VoiceSettings.model_fields)
        self.assertEqual(VoiceSettings().nova_sonic_voice, "matthew")

    def test_ui_offers_only_real_aws_voices(self):
        """Eine erfundene ID scheitert erst beim Verbindungsaufbau — unsichtbar."""
        ids = set(re.findall(r'\{ id: "([a-z]+)"', FRONTEND.read_text()))
        self.assertTrue(ids, "keine Stimmen in der Oberflaeche gefunden")
        self.assertEqual(ids - AWS_VOICES, set(), "Stimmen, die es bei AWS nicht gibt")

    def test_the_polyglot_voices_are_offered(self):
        """Nur diese beiden sprechen Deutsch — ohne sie ist die Auswahl fuer DACH nutzlos."""
        text = FRONTEND.read_text()
        self.assertIn('id: "matthew"', text)
        self.assertIn('id: "tiffany"', text)

    def test_german_capable_voices_are_marked(self):
        """Sonst waehlt jemand „Amy" und wundert sich ueber englischen Akzent."""
        text = FRONTEND.read_text()
        self.assertIn("spricht Deutsch", text)


if __name__ == "__main__":
    unittest.main()
