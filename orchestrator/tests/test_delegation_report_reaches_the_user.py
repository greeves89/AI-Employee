"""Die Fertigmeldung muss dort ankommen, wo der Mensch hinsieht.

Kundenmeldung vom 2026-08-13, 06:22 Uhr: *„Er hat die Aufgabe delegiert — es fehlt
aber die Rückmeldung, ob der Agent das auch gemacht hat bzw. ob die Aufgabe
abgeschlossen ist."*

Der Rückmeldeweg existierte. Er lief nur ins Leere, aus zwei Gründen:

1. **Falscher Schlüssel.** Die Meldung wurde mit ``session_id`` verschickt, der
   Agent liest aber ``chat_session_id``. Ergebnis: der Faden war leer, der Agent
   landete in ``webapp:default`` — einem Gespräch, das niemand ansieht. Der Mensch
   sah nie eine Antwort, obwohl der Auftrag längst fertig war.
2. **Kein Ursprungsfaden.** Selbst mit richtigem Schlüssel stand nirgends, in
   welchem Gespräch die Delegation beauftragt worden war. Der Auftrag trägt ihn
   jetzt mit.

Dazu ein dritter Fund: der Text trug Emojis, obwohl sie in nutzersichtbarem Text
ausdrücklich nicht vorkommen dürfen.
"""

import inspect
import unittest
from pathlib import Path

from app.core import task_router


class TheCallbackUsesTheKeyTheAgentReadsTests(unittest.TestCase):
    """Der Kern des Fehlers — ein falscher Schlüsselname, und alles verschwindet."""

    def setUp(self):
        self.src = inspect.getsource(task_router.TaskRouter._notify_delegating_agent)

    def test_the_chat_payload_carries_chat_session_id(self):
        self.assertIn('"chat_session_id": origin_session', self.src)

    def test_the_old_wrong_key_is_gone(self):
        self.assertNotIn('"session_id": "delegation-callback"', self.src)

    def test_the_origin_session_comes_from_the_task(self):
        self.assertIn('(task.metadata_ or {}).get("chat_session_id")', self.src)

    def test_there_is_a_fallback_for_tasks_without_a_thread(self):
        """Ueber den stdio-MCP-Server (Claude Code, Codex) entstandene Auftraege
        fuehren keinen Faden mit — die duerfen nicht wieder im Nichts landen."""
        self.assertIn("_latest_chat_session", self.src)


class NoEmojiInUserFacingTextTests(unittest.TestCase):
    """Harte Vorgabe des Projekts. Hier stand sie in Produktion."""

    def test_the_callback_text_has_none(self):
        src = inspect.getsource(task_router.TaskRouter._notify_delegating_agent)
        for symbol in ("✅", "❌", "⚠️", "🔴"):
            with self.subTest(symbol):
                self.assertNotIn(symbol, src)


class TheAgentIsAskedToReportTests(unittest.TestCase):
    """Die Meldung allein genuegt nicht — sie muss den Lead auffordern, dem
    Menschen zu berichten. Sonst nimmt er sie zur Kenntnis und schweigt."""

    def test_it_asks_for_a_report(self):
        src = inspect.getsource(task_router.TaskRouter._notify_delegating_agent)
        self.assertIn("Berichte dem Menschen", src)

    def test_it_asks_for_honesty_about_a_bad_result(self):
        src = inspect.getsource(task_router.TaskRouter._notify_delegating_agent)
        self.assertIn("nicht erfuellt", src)


class TheTaskCarriesItsOriginTests(unittest.TestCase):
    def test_the_schema_accepts_the_thread(self):
        from app.schemas.task import TaskCreate

        self.assertIn("chat_session_id", TaskCreate.model_fields)

    def test_both_creation_paths_store_it(self):
        """Einzelauftrag UND Stapel — der Lead benutzt mal das eine, mal das andere."""
        src = inspect.getsource(__import__("app.api.tasks", fromlist=["x"]))
        self.assertEqual(src.count('"chat_session_id": data.chat_session_id'), 1)
        self.assertEqual(src.count('"chat_session_id": task_data.chat_session_id'), 1)


class TheAgentAttachesItsThreadTests(unittest.TestCase):
    """Custom-LLM-Seite: ohne diesen Teil traegt kein Auftrag den Faden.

    Hier nur ueber den Dateiinhalt geprueft — der Agentenbaum ist aus dieser
    Testsammlung nicht importierbar. Das Verhalten selbst prueft
    ``agent/tests/test_delegation_thread_is_carried.py``.
    """

    AGENT = Path(__file__).resolve().parents[2] / "agent"

    def test_the_consumer_sets_it_per_turn(self):
        src = (self.AGENT / "app/chat_consumer.py").read_text()
        self.assertIn("current_chat_session.set(chat_session_id)", src)

    def test_task_creation_attaches_it(self):
        src = (self.AGENT / "app/tools/api_client.py").read_text()
        # create_task und der Stapel aus delegate_and_wait
        self.assertGreaterEqual(src.count("**_session_field()"), 2)


if __name__ == "__main__":
    unittest.main()
