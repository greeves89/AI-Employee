"""Eigene KI-Abos muss der Betreiber zentral erlauben oder verbieten koennen.

Vorgabe aus dem Kundentermin am 18.08.2026, woertlich: es fehle die „globale
Freigabe, damit Mitarbeiter eigene Abo-Accounts einbinden duerfen —
Sicherheitsrisiko sonst, muss zentral steuerbar sein."

Der persoenliche Weg („Meine KI-Zugaenge", Claude- und Codex-Anmeldung) wurde am
SELBEN TAG gebaut — ohne diesen Schalter. Damit konnte jeder eingeloggte Nutzer
sein privates Abo einbinden, und ein Administrator konnte es weder sehen noch
unterbinden. Genau das Risiko, das der Kunde vorbeugend benannt hatte.

Zwei Dinge muessen stimmen, sonst ist der Schalter Fassade:

1. Er wirkt im **Zugangs-Pfad**. Nur die Oberflaeche abzuschalten hiesse: bereits
   hinterlegte Zugaenge liefen weiter — und genau die sollen aufhoeren zu wirken.
2. Er wirkt in der **Schnittstelle**. Sonst legt jemand etwas an, das
   anschliessend wirkungslos ist; eine Anmeldung, die scheinbar klappt und dann
   nichts bewirkt, ist schlimmer als eine klare Absage.
"""

import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.api import my_ai_credentials as api
from app.core import agent_credentials as creds


class TheSwitchExistsTests(unittest.TestCase):
    def test_it_can_be_read(self):
        self.assertIn(personal := "personal_credentials_allowed", dir(creds))
        self.assertIsInstance(getattr(creds, personal)(), bool)

    def test_it_defaults_to_forbidden(self):
        """KORRIGIERT: die erste Fassung hatte die Vorgabe auf AN, mit der
        Begruendung „eine bestehende Anlage darf nicht ohne Zugang dastehen".
        Das traegt hier nicht — private Abos waren vorher gar nicht moeglich,
        es kann nichts wegbrechen.

        Woertlich vom Kunden: „dass man das generell unterbinden kann, weil das
        ist sonst natuerlich ein kleines Sicherheitsrisiko."
        """
        self.assertFalse(creds.personal_credentials_allowed())

    def test_a_released_user_may_do_it_even_when_globally_off(self):
        """Die zweite Ebene: „dass man dann fuer User manuell freischalten
        kann … der darf gerne sein privates Modell einbinden"."""
        freigegeben = SimpleNamespace(allow_personal_credentials=True)
        self.assertTrue(creds.personal_credentials_allowed(freigegeben))

    def test_an_ordinary_user_may_not(self):
        gewoehnlich = SimpleNamespace(allow_personal_credentials=False)
        self.assertFalse(creds.personal_credentials_allowed(gewoehnlich))

    def test_the_global_switch_opens_it_for_everyone(self):
        """Wer es fuer die ganze Anlage aufmachen will, soll nicht jeden Nutzer
        einzeln anhaken muessen."""
        with patch.object(creds.settings, "allow_personal_credentials", True):
            self.assertTrue(creds.personal_credentials_allowed(SimpleNamespace(
                allow_personal_credentials=False)))

    def test_an_admin_can_release_a_single_user(self):
        from app.api.auth import UserUpdateRequest
        self.assertIn("allow_personal_credentials", UserUpdateRequest.model_fields)

    def test_it_is_a_saved_setting_not_only_an_env_var(self):
        """Sonst muesste man den Container anfassen, um es umzustellen — der
        Kunde hat „zentral steuerbar" verlangt, nicht „per Neustart"."""
        from app.services import settings_service
        self.assertIn("allow_personal_credentials", inspect.getsource(settings_service))


class ItWorksWhereItMattersTests(unittest.TestCase):
    """Der Zugangs-Pfad ist die Stelle, an der es wirklich zaehlt."""

    SRC = inspect.getsource(creds.resolve)

    def test_the_personal_branch_is_gated(self):
        self.assertIn("if personal_credentials_allowed(besitzer):", self.SRC)

    def test_a_blocked_lookup_is_logged(self):
        """Sonst sucht jemand stundenlang, warum sein Zugang nicht greift."""
        self.assertIn("gesperrt", self.SRC)

    def test_the_team_licence_path_is_untouched(self):
        self.assertIn("if not team_license_allowed():", self.SRC)


class TheApiRefusesToStoreSomethingUselessTests(unittest.TestCase):
    GUARD = inspect.getsource(api._eigene_zugaenge_erlaubt)

    def test_it_refuses_with_403(self):
        self.assertIn("status_code=403", self.GUARD)

    def test_it_says_who_can_change_it(self):
        """„Nicht erlaubt" ohne Weg weiter ist eine Sackgasse."""
        self.assertIn("Administrator", self.GUARD)

    def test_it_raises_when_disabled(self):
        with patch.object(creds, "personal_credentials_allowed", return_value=False):
            from fastapi import HTTPException
            with self.assertRaises(HTTPException) as ctx:
                api._eigene_zugaenge_erlaubt(None)
            self.assertEqual(ctx.exception.status_code, 403)

    def test_it_passes_when_allowed(self):
        with patch.object(creds, "personal_credentials_allowed", return_value=True):
            api._eigene_zugaenge_erlaubt(None)   # darf nicht werfen

    def test_every_creating_endpoint_is_guarded(self):
        quelle = inspect.getsource(api)
        for name in ("upsert_my_credential", "start_anthropic_login",
                     "exchange_anthropic_login", "start_codex_login"):
            with self.subTest(endpunkt=name):
                block = quelle.split(f"async def {name}(", 1)[1][:900]
                self.assertIn("_eigene_zugaenge_erlaubt(user)", block)


class ReadingAndDeletingStayOpenTests(unittest.TestCase):
    """Wer seinen Zugang loswerden will, darf daran nicht gehindert werden —
    auch nicht, wenn ein Administrator die Funktion inzwischen zugemacht hat."""

    QUELLE = inspect.getsource(api)

    def test_deleting_is_not_guarded(self):
        block = self.QUELLE.split("async def delete_my_credential(", 1)[1][:800]
        self.assertNotIn("_eigene_zugaenge_erlaubt()", block)

    def test_listing_is_not_guarded(self):
        block = self.QUELLE.split("async def list_my_credentials(", 1)[1][:800]
        self.assertNotIn("_eigene_zugaenge_erlaubt()", block)

    def test_the_listing_tells_the_ui_the_state(self):
        """Damit die Oberflaeche den Bereich ausblendet, statt Knoepfe
        anzubieten, die mit 403 abgewiesen werden."""
        self.assertIn('"personal_allowed"', self.QUELLE)


if __name__ == "__main__":
    unittest.main()
