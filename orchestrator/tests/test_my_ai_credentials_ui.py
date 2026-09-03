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


class TheLoginIsTheSameAsForAdminsTests(unittest.TestCase):
    """Wunsch des Nutzers (15.08.2026): „Hier brauche ich bitte das gleiche
    login wie in den admin settings… mit dem popup und dann dem Eintragen des
    OAuth Tokens. Sowohl für Codex als auch für Claude."

    Vorher gab es zwei Verfahren fuer dieselbe Sache: der Administrator klickte
    einen Knopf und meldete sich im Browser an — der normale Nutzer musste ein
    Token aus ``claude setup-token`` bzw. den Inhalt einer ``auth.json`` von Hand
    einfuegen. Das umstaendlichere Verfahren traf ausgerechnet den, der sich am
    wenigsten auskennt.

    Der entscheidende Unterschied sitzt am ENDE: das Ergebnis darf nicht als
    plattformweite Integration landen, sondern muss in ``user_ai_credentials`` —
    nur von dort liest ``agent_credentials`` beim Bau eines Containers. Ohne
    diesen Schritt haette sich der Nutzer erfolgreich angemeldet, und seine
    Agenten liefen trotzdem ohne seinen Zugang.
    """

    API_ME = (ROOT / "orchestrator/app/api/my_ai_credentials.py").read_text()

    def test_both_harnesses_can_be_started(self):
        self.assertIn('@router.post("/anthropic/start")', self.API_ME)
        self.assertIn('@router.post("/codex/start")', self.API_ME)

    def test_the_exchange_lands_in_the_personal_store(self):
        block = self.API_ME.split('@router.post("/anthropic/exchange")', 1)[1]
        self.assertIn("UserAiCredential(user_id=user.id", block)
        self.assertIn('harness="claude_code"', block)

    def test_the_token_is_stored_encrypted(self):
        block = self.API_ME.split('@router.post("/anthropic/exchange")', 1)[1]
        self.assertIn("encrypt_token(token)", block)

    def test_it_reuses_the_platform_exchange(self):
        """Eine zweite Umsetzung des OAuth-Austauschs waere die naechste Stelle,
        die auseinanderlaeuft."""
        # Seit der Umstellung auf die vorhandene Abhaengigkeit heisst der
        # Aufruf ``service.exchange_code`` — gebaut wird der Dienst einmal in
        # ``_oauth_service``, genau wie in integrations.py.
        self.assertIn("service.exchange_code(", self.API_ME)

    def test_the_frontend_offers_both_logins(self):
        for fn in ("startMyAnthropicLogin", "exchangeMyAnthropicLogin", "startMyCodexLogin"):
            with self.subTest(funktion=fn):
                self.assertIn(f"export async function {fn}", CLIENT)

    def test_the_browser_is_opened(self):
        self.assertIn('window.open(auth_url, "_blank")', KOMPONENTE)
        self.assertIn('window.open(s.verification_uri, "_blank")', KOMPONENTE)

    def test_the_device_code_is_shown_for_codex(self):
        """Ohne den Code kann sich niemand anmelden — er steht nur hier."""
        self.assertIn("Gerätecode", KOMPONENTE)

    def test_a_pasted_callback_url_is_accepted(self):
        """Die meisten kopieren die ganze Adresszeile, nicht den Code darin."""
        self.assertIn("new URL(roh)", KOMPONENTE)
        self.assertIn('u.searchParams.get("code")', KOMPONENTE)

    def test_the_manual_way_still_exists(self):
        """Wer sein Token schon hat oder ohne Browser arbeitet, soll nicht durch
        die Anmeldung muessen."""
        self.assertIn("Manuell", KOMPONENTE)


class TheOAuthServiceIsBuiltCorrectlyTests(unittest.TestCase):
    """Der erste Anlauf baute Redis von Hand und uebergab nur ihn —
    ``OAuthService`` erwartet aber ``(db, redis)``. Ergebnis: ein 500 beim Klick
    auf „Mit Claude anmelden", und kein einziger Test hat es bemerkt, weil alle
    nur den Quelltext gelesen haben.

    Hier wird die Signatur wirklich gegen den Aufruf gehalten.
    """

    def test_the_dependency_matches_the_constructor(self):
        import inspect

        from app.api.my_ai_credentials import _oauth_service
        from app.services.oauth_service import OAuthService

        erwartet = [p for p in inspect.signature(OAuthService.__init__).parameters
                    if p != "self"]
        uebergeben = list(inspect.signature(_oauth_service).parameters)
        self.assertEqual(erwartet, uebergeben,
                         "Reihenfolge und Anzahl muessen zum Konstruktor passen")

    def test_it_uses_the_same_construction_as_integrations(self):
        """Zwei Bauweisen fuer denselben Dienst laufen frueher oder spaeter
        auseinander — genau so ist der 500 entstanden."""
        src = (ROOT / "orchestrator/app/api/my_ai_credentials.py").read_text()
        self.assertIn("OAuthService(db, redis)", src)
        self.assertIn("Depends(get_redis_service)", src)

    def test_every_route_is_registered(self):
        from app.api.my_ai_credentials import router

        pfade = {r.path for r in router.routes}
        for p in ("/me/ai-credentials/anthropic/start",
                  "/me/ai-credentials/anthropic/exchange",
                  "/me/ai-credentials/codex/start"):
            with self.subTest(pfad=p):
                self.assertIn(p, pfade)


class TheCodexLoginCompletesByItselfTests(unittest.TestCase):
    """Nutzerbericht (15.08.2026): „ich habe von codex der anmeldung diesen code
    da erhalten… dann stand bei codex seite kann geschlossen werden, dann war
    diese zu. ich habe aber keine auth.json erhalten oder so."

    Mein erster Anlauf war unerfuellbar: ich habe nach dem Inhalt einer Datei
    gefragt, die der Nutzer nie zu sehen bekommt. Codex legt die ``auth.json``
    IM CONTAINER an, der Dienst liest sie und raeumt das Verzeichnis wieder weg.
    Der Administrator-Weg macht es laengst richtig — er fragt den Status ab.

    Der Dienst bekommt jetzt mit, FUER WEN die Anmeldung laeuft, und legt das
    Ergebnis entsprechend ab: persoenlicher Zugang statt Anlagen-Zugang.
    """

    DIENST = (ROOT / "orchestrator/app/services/codex_device_auth_service.py").read_text()
    API_ME = (ROOT / "orchestrator/app/api/my_ai_credentials.py").read_text()

    def test_the_session_knows_who_it_is_for(self):
        self.assertIn("for_user_id: str | None = None", self.DIENST)

    def test_a_personal_login_lands_in_the_personal_store(self):
        block = self.DIENST.split("if session.for_user_id:", 1)[1][:1800]
        self.assertIn("UserAiCredential(user_id=session.for_user_id", block)
        self.assertIn('harness="codex"', block)
        self.assertIn("encrypt_token(auth_json)", block)

    def test_the_platform_path_is_unchanged(self):
        """Der Administrator-Weg darf sich nicht mitaendern."""
        self.assertIn('store_auth_json("codex", auth_json)', self.DIENST)

    def test_the_shared_file_stays_platform_only(self):
        """``sync_auth_json`` schreibt die gemeinsame Datei — ein persoenliches
        Abo darf dort nicht landen, sonst benutzen ihn alle Agenten."""
        block = self.DIENST.split("if session.for_user_id:", 1)[1]
        vor_else = block.split("else:", 1)[0]
        self.assertNotIn("sync_auth_json", vor_else)

    def test_the_ui_asks_the_server_instead_of_the_user(self):
        self.assertIn("getMyCodexLoginStatus", KOMPONENTE)
        self.assertIn("Warte auf die Bestätigung", KOMPONENTE)

    def test_the_ui_no_longer_asks_for_a_file_it_cannot_have(self):
        self.assertNotIn("Danach den Inhalt deiner", KOMPONENTE)

    def test_another_users_session_is_not_readable(self):
        """Der Zustand einer fremden Anmeldung geht niemanden etwas an."""
        block = self.API_ME.split('@router.get("/codex/status/{session_id}")', 1)[1]
        self.assertIn("session.for_user_id != user.id", block)
