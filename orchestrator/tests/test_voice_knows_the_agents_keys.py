"""Die Sprachfront muss wissen, WELCHE Zugaenge der Agent hat — und die Werte nie.

Nutzerbericht vom 18.08.2026, mit Bildschirmfoto: „hast du in deinen
Umgebungsvariablen einen Zugang zu diesem Project Planner?" Antwort der
Sprachfront: „Ich habe keine speziellen Umgebungsvariablen für einen Project
Planner API Key gefunden" — und dann zaehlte sie ihre eigenen Einstellungen auf
(Modell, Harness, Provider, Autonomie, Budget).

Sie hatte darauf schlicht keinen Blick: ``agent_manager._get_secrets_env`` legt
die zugewiesenen Schluessel als Umgebungsvariablen in den AGENTEN-Container; die
Sprachfront laeuft im Orchestrator und sah nur sich selbst.

**Die Werte bleiben trotzdem draussen.** Der gesprochene Verlauf wird als
Nachricht gespeichert und geht Wort fuer Wort an einen fremden Dienst. Ein
Schluessel, der dort einmal landet, ist nicht mehr einzufangen — und muesste
gedreht werden. Die Sprachfront braucht ihn auch nicht: der Agent hat die
Variable bereits und ruft die Schnittstelle selbst auf.
"""

import inspect
import unittest

from app.services.realtime_voice_session import (
    LIST_AGENT_SECRETS_TOOL,
    RealtimeVoiceSession,
)


class TheVoiceCanSeeWhichKeysExistTests(unittest.TestCase):
    SRC = inspect.getsource(RealtimeVoiceSession)

    def test_the_tool_exists(self):
        self.assertEqual(LIST_AGENT_SECRETS_TOOL["toolSpec"]["name"], "list_agent_secrets")

    def test_it_is_offered_to_the_model(self):
        """Ein Werkzeug, das nicht in der Liste steht, gibt es fuer das Modell
        nicht — es wuerde weiter raten."""
        self.assertIn("LIST_AGENT_SECRETS_TOOL,", self.SRC)

    def test_the_call_is_wired(self):
        self.assertIn('if name == "list_agent_secrets":', self.SRC)

    def test_it_reads_the_assignments_not_the_voice_settings(self):
        helfer = inspect.getsource(RealtimeVoiceSession._fast_secrets)
        self.assertIn("AgentSecretAssignment", helfer)
        self.assertIn("agent_id == self.agent_id", helfer)

    def test_inactive_keys_do_not_count(self):
        """Ein abgeschalteter Zugang wird auch nicht in den Container gelegt —
        ihn zu melden waere ein falsches Versprechen."""
        self.assertIn("is_active.is_(True)", inspect.getsource(RealtimeVoiceSession._fast_secrets))


class TheValuesNeverReachTheVoiceTests(unittest.TestCase):
    HELFER = inspect.getsource(RealtimeVoiceSession._fast_secrets)

    def test_nothing_is_decrypted_here(self):
        self.assertNotIn("decrypt", self.HELFER)

    def test_the_encrypted_column_is_never_read(self):
        self.assertNotIn("value_encrypted", self.HELFER)

    def test_only_name_and_variable_are_named(self):
        self.assertIn("r.name", self.HELFER)
        self.assertIn("r.key_name", self.HELFER)

    def test_the_tool_tells_the_model_to_delegate(self):
        """Sonst versucht es, den Aufruf selbst zu machen — und fragt am Ende den
        Nutzer nach dem Schluessel."""
        beschr = LIST_AGENT_SECRETS_TOOL["toolSpec"]["description"]
        self.assertIn("ask_agent", beschr)

    def test_the_tool_forbids_asking_the_user_to_read_a_key_aloud(self):
        beschr = LIST_AGENT_SECRETS_TOOL["toolSpec"]["description"]
        self.assertIn("read a key out loud", beschr)


class TheAnswerIsUsefulWhenThereIsNothingTests(unittest.TestCase):
    HELFER = inspect.getsource(RealtimeVoiceSession._fast_secrets)

    def test_it_says_where_to_assign_one(self):
        """„Nichts gefunden" ohne Weg weiter ist genau die Antwort, ueber die
        sich der Nutzer beschwert hat."""
        self.assertIn("zuweisen", self.HELFER.lower())

    def test_assigned_but_inactive_is_its_own_answer(self):
        self.assertIn("keiner davon ist aktiv", self.HELFER)


if __name__ == "__main__":
    unittest.main()
