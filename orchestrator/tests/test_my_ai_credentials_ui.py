"""Das eigene Claude-/Codex-Abo laesst sich auch tatsaechlich verbinden.

Die Schnittstelle ``/me/ai-credentials`` gibt es seit v1.185.0. Am 2026-08-15
fiel auf: sie wurde im gesamten Frontend **kein einziges Mal** aufgerufen, und
die Einstellungsseite stand in keiner Menuegruppe. Kein Nutzer konnte sein Abo
hinterlegen.

Das war nicht bloss eine fehlende Seite. Die Plattform verweist an mehreren
Stellen ausdruecklich darauf — die Agenten-Anlage lehnt seit v1.210.0 sogar ab
mit „verbinde dein eigenes Abo unter Einstellungen". Eine Fehlermeldung, die auf
etwas Nichtexistierendes zeigt, ist schlimmer als gar keine.

Geprueft wird deshalb die ganze Kette: Schnittstelle da, Aufruf da, Reiter da,
Menueeintrag da.
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API = (ROOT / "orchestrator/app/api/my_ai_credentials.py").read_text()
CLIENT = (ROOT / "frontend/src/lib/api.ts").read_text()
KOMPONENTE = (ROOT / "frontend/src/components/settings/my-ai-credentials.tsx").read_text()
VIEW = (ROOT / "frontend/src/app/settings/view.tsx").read_text()
MENUE = (ROOT / "frontend/src/components/layout/user-menu.tsx").read_text()


class TheChainIsCompleteTests(unittest.TestCase):
    """Jedes einzelne Glied — ein fehlendes und der Nutzer steht wieder vor
    nichts."""

    def test_the_endpoint_exists(self):
        self.assertIn('prefix="/me/ai-credentials"', API)

    def test_the_frontend_calls_it(self):
        for fn in ("getMyAiCredentials", "putMyAiCredential", "deleteMyAiCredential"):
            with self.subTest(funktion=fn):
                self.assertIn(f"export async function {fn}", CLIENT)

    def test_the_component_uses_those_calls(self):
        self.assertIn("api.getMyAiCredentials()", KOMPONENTE)
        self.assertIn("api.putMyAiCredential(", KOMPONENTE)
        self.assertIn("api.deleteMyAiCredential(", KOMPONENTE)

    def test_the_settings_page_shows_it(self):
        self.assertIn("<MyAiCredentials />", VIEW)
        self.assertIn('secTab === "meine"', VIEW)

    def test_the_page_is_reachable_from_the_user_menu(self):
        """Die Seite existierte schon — sie stand nur in keinem Menue."""
        self.assertIn('router.push("/settings")', MENUE)
        self.assertIn("Einstellungen", MENUE)


class BothHarnessesAreOfferedTests(unittest.TestCase):
    def test_claude_and_codex(self):
        self.assertIn('id: "claude_code"', KOMPONENTE)
        self.assertIn('id: "codex"', KOMPONENTE)

    def test_it_says_what_to_paste(self):
        """Ohne diesen Hinweis raet der Nutzer — bei Codex ist es der Inhalt
        einer Datei, bei Claude ein Token. Das weiss niemand auswendig."""
        self.assertIn("setup-token", KOMPONENTE)
        self.assertIn("auth.json", KOMPONENTE)


class TheSecretIsNeverShownTests(unittest.TestCase):
    """Die Schnittstelle gibt das Geheimnis nicht zurueck — die Oberflaeche darf
    also gar nicht erst so tun, als koenne sie es anzeigen."""

    def test_the_api_returns_everything_except_the_secret(self):
        block = API.split("def _to_response", 1)[1][:400]
        self.assertNotIn("secret", block)
        self.assertIn("last_status", block)

    def test_the_ui_only_shows_that_something_is_stored(self):
        self.assertIn("nicht verbunden", KOMPONENTE)
        self.assertIn("verbunden", KOMPONENTE)


class ItTellsTheUserWhatHappensWithoutOneTests(unittest.TestCase):
    """Ohne eigenen Zugang laufen die Agenten entweder ueber die Firmenlizenz
    oder gar nicht — das muss dastehen, sonst probiert man im Dunkeln."""

    def test_the_team_license_case_is_explained(self):
        self.assertIn("team_license_allowed", API)
        self.assertIn("Firmenlizenz", KOMPONENTE)

    def test_the_other_case_names_the_alternative(self):
        self.assertIn("freigegebenes KI-Konto", KOMPONENTE)


if __name__ == "__main__":
    unittest.main()
