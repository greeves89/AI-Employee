"""Jeder Auftrag traegt den Gespraechsfaden — egal ueber welches der drei Werkzeuge.

Kundenfall vom 2026-08-13: „????? keine kacheln mehr". In DERSELBEN SEKUNDE
entstanden vier Auftraege desselben Auftraggebers — zwei trugen
``chat_session_id``, zwei nicht. Fuer die einen erschien eine Kachel im Chat,
fuer die anderen nicht.

Grund: Es gibt drei Werkzeuge, die Auftraege anlegen — ``create_task``,
``create_task_batch`` und ``delegate_and_wait`` — und jedes baute seine Nutzlast
selbst zusammen. Als der Faden dazukam, wurde er an zwei von dreien angehaengt.
``create_task_batch`` blieb aussen vor und fiel erst auf, als ein Agent von sich
aus dieses Werkzeug waehlte.

Der Nutzer stellte die richtige Frage: „Wieso gibt es diese drei?" Drei fast
gleiche Stellen zu pflegen heisst, eine davon zu vergessen. Deshalb bauen jetzt
alle drei ueber ``_task_payload``.
"""

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
QUELLE = ROOT / "agent/app/tools/api_client.py"
SRC = QUELLE.read_text()

WERKZEUGE = ("create_task", "create_task_batch", "delegate_and_wait")


def _rumpf(name: str) -> str:
    baum = ast.parse(SRC)
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.AsyncFunctionDef) and knoten.name == name:
            return ast.get_source_segment(SRC, knoten) or ""
    raise AssertionError(f"{name} nicht gefunden")


class EveryToolUsesTheOneBuilderTests(unittest.TestCase):
    def test_all_three_build_through_it(self):
        for name in WERKZEUGE:
            with self.subTest(werkzeug=name):
                self.assertIn("_task_payload(", _rumpf(name))

    def test_none_of_them_hand_rolls_the_payload_anymore(self):
        """Eine eigene Nutzlast ist genau der Weg, auf dem das Feld verloren
        ging — sie darf nicht zurueckkehren."""
        for name in WERKZEUGE:
            with self.subTest(werkzeug=name):
                self.assertNotIn('"prompt": t.get("prompt"', _rumpf(name))


class TheBuilderAlwaysAttachesTheThreadTests(unittest.TestCase):
    def test_the_thread_is_part_of_the_builder(self):
        self.assertIn("**_session_field()", _rumpf("create_task").split(
            "_task_payload", 1)[0] + SRC.split("def _task_payload", 1)[1][:1600])

    def test_outside_a_conversation_nothing_is_attached(self):
        """Ein Zeitplan-Lauf hat keinen Faden — dann darf auch keiner
        erfunden werden, sonst landet die Kachel in einem fremden Chat."""
        from app.tools.api_client import _session_field

        self.assertEqual(_session_field(), {})

    def test_inside_a_conversation_it_is_attached(self):
        from app.tools.api_client import _session_field, current_chat_session

        marke = current_chat_session.set("93340239600f")
        try:
            self.assertEqual(_session_field(), {"chat_session_id": "93340239600f"})
        finally:
            current_chat_session.reset(marke)

    def test_the_builder_carries_the_thread_into_a_batch_entry(self):
        from app.tools.api_client import OrchestratorAPIClient, current_chat_session

        marke = current_chat_session.set("abc123")
        try:
            eintrag = OrchestratorAPIClient._task_payload({"title": "T", "prompt": "p"})
        finally:
            current_chat_session.reset(marke)
        self.assertEqual(eintrag["chat_session_id"], "abc123")

    def test_an_empty_model_is_not_sent(self):
        """``model: None`` ueberschreibt sonst die Wahl des Orchestrators."""
        from app.tools.api_client import OrchestratorAPIClient

        self.assertNotIn("model", OrchestratorAPIClient._task_payload({"title": "T"}))

    def test_a_given_model_is_kept(self):
        from app.tools.api_client import OrchestratorAPIClient

        eintrag = OrchestratorAPIClient._task_payload({"title": "T", "model": "gpt-5"})
        self.assertEqual(eintrag["model"], "gpt-5")


class TheFallbackSurvivesParallelWorkTests(unittest.TestCase):
    """Der Orchestrator kann den Faden notfalls selbst ermitteln — der
    MCP-Werkzeugserver (Claude Code) kennt ihn naemlich gar nicht.

    Bisher las er ``current_task``, und das traegt nur EINE Arbeit. Der Agent
    lief am 2026-08-13 nebenher an einer Zeitplan-Aufgabe; dort stand deren
    Kennung, und der Chat war unsichtbar.
    """

    ROUTER = (ROOT / "orchestrator/app/core/task_router.py").read_text()

    def test_it_also_looks_at_all_running_work(self):
        block = self.ROUTER.split("async def _session_of_running_turn", 1)[1][:2200]
        self.assertIn("active_sessions", block)

    def test_it_refuses_to_guess_between_several_chats(self):
        """Eine Kachel im falschen Gespraech ist schlimmer als keine."""
        block = self.ROUTER.split("async def _session_of_running_turn", 1)[1][:2200]
        self.assertIn("if len(faeden) == 1 else None", block)


if __name__ == "__main__":
    unittest.main()
