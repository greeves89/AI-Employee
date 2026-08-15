"""Ein Agent bekommt sein Modell aus genau ZWEI Quellen — und aus keiner dritten.

Vorgabe des Nutzers (15.08.2026): „Einem User soll es möglich sein einen Agenten
mittels Codex oder Claude Abo zu nutzen. Es soll aber vom Admin auch ermöglicht
werden, dass der Azure, AWS oder Google anbindet und Modelle bereitstellt, die
dann auch vom Anwender genutzt werden können."

Beide Wege gab es bereits. Was fehlte, war die Einheitlichkeit — es gab einen
DRITTEN: Endpunkt und Schluessel direkt am Agenten eintippen. Der Zugang gehoert
dann niemandem, taucht in keiner Uebersicht auf, laesst sich nicht entziehen,
und beim naechsten Neuerstellen ist er weg (er steht nur in den
Umgebungsvariablen des Containers).

Genau das ist an diesem Tag passiert: ein so angelegter Agent verlor seinen
Zugang beim ersten ``update_agent`` — ohne Fehlermeldung, er lief einfach mit den
alten Werten weiter, bis jemand hinsah.

Dazu der zweite Fehler derselben Baustelle: fuehrt das Konto das gewaehlte Modell
nicht, nahm der Code **kommentarlos** den ersten Eintrag. Ausgewaehlt war
gpt-5.6-sol, gelaufen ist gpt-5.3-codex. In einer Anlage, in der der
Administrator Modelle FREIGIBT, ist das der gefaehrlichste Fehler ueberhaupt:
man glaubt, das freigegebene Modell zu benutzen.
"""

import inspect
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API = (ROOT / "orchestrator/app/api/agents.py").read_text()

from app.core.agent_manager import AgentManager  # noqa: E402


class OnlyTwoWaysInTests(unittest.TestCase):
    def test_an_account_or_an_own_subscription_is_required(self):
        self.assertIn('if data.mode == "custom_llm" and not data.ai_account_id:', API)

    def test_the_error_names_both_ways(self):
        """Eine Fehlermeldung, die nur sagt was fehlt, laesst den Nutzer stehen."""
        block = API.split('if data.mode == "custom_llm" and not data.ai_account_id:', 1)[1][:1400]
        self.assertIn("KI-Konto", block)
        self.assertIn("Claude", block)
        self.assertIn("Codex", block)

    def test_a_normal_user_cannot_paste_credentials_at_the_agent(self):
        block = API.split('if data.mode == "custom_llm" and not data.ai_account_id:', 1)[1][:1400]
        self.assertIn("status_code=403", block)

    def test_an_admin_still_may(self):
        """Fuer Sonderfaelle und zum Erproben eines neuen Anbieters, bevor daraus
        ein Konto wird — ein Verbot ohne Ausweg wird umgangen."""
        block = API.split('if data.mode == "custom_llm" and not data.ai_account_id:', 1)[1][:1400]
        self.assertIn("_ist_admin", block)


class NoSilentModelSubstitutionTests(unittest.TestCase):
    SRC = inspect.getsource(AgentManager._effective_llm_config)

    def test_a_substitution_is_logged(self):
        self.assertIn("nicht im Konto", self.SRC)

    def test_the_owner_is_told(self):
        """Nur ins Protokoll zu schreiben heisst: niemand erfaehrt es."""
        self.assertIn("_warn_model_substituted", self.SRC)

    def test_it_only_warns_when_a_model_was_actually_chosen(self):
        """Ohne gewaehltes Modell ist der erste Eintrag die richtige Vorgabe —
        dafuer darf es keine Warnung geben, sonst gewoehnt man sich sie ab."""
        self.assertIn("if agent_model:", self.SRC)

    def test_the_agent_still_starts(self):
        """Ein laufender Agent darf nicht stehenbleiben, weil jemand ein Modell
        aus dem Konto genommen hat — laut sein ja, blockieren nein."""
        self.assertIn("entry = models[0]", self.SRC)

    def test_the_warning_says_how_to_fix_it(self):
        warn = inspect.getsource(AgentManager._warn_model_substituted)
        self.assertIn("Trage", warn)
        self.assertIn("freigegebenes Modell", warn)

    def test_a_failing_warning_does_not_break_agent_creation(self):
        warn = inspect.getsource(AgentManager._warn_model_substituted)
        self.assertIn("except Exception", warn)


class TheTwoWaysActuallyExistTests(unittest.TestCase):
    """Die Regel oben waere sinnlos, wenn einer der beiden Wege fehlte."""

    def test_the_company_path_exists(self):
        self.assertTrue((ROOT / "orchestrator/app/api/ai_accounts.py").exists())

    def test_the_personal_path_exists(self):
        self.assertTrue((ROOT / "orchestrator/app/api/my_ai_credentials.py").exists())

    def test_accounts_are_released_per_group_and_deny_by_default(self):
        src = (ROOT / "orchestrator/app/api/ai_accounts.py").read_text()
        self.assertIn("_allowed_account_ids", src)
        self.assertIn("EMPTY set (deny)", src)

    def test_mcp_servers_are_released_the_same_way(self):
        """Derselbe Mechanismus fuer die MCP-Schnittstellen des Administrators —
        eine zweite Rechte-Logik daneben waere die naechste Baustelle."""
        perms = (ROOT / "orchestrator/app/core/permissions.py").read_text()
        self.assertIn("mcp_server_ids", perms)
        self.assertIn("ai_account_ids", perms)


if __name__ == "__main__":
    unittest.main()
