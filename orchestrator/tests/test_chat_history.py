"""Gespraeche verzweigen, zurueckspulen, zusammenfassen, benennen (#538).

Vier Dinge, die im Chat fehlten, und alle vier greifen auf denselben Gedanken zu:
„die Nachrichten bis hierher". Deshalb liegen sie zusammen statt in vier Ecken.

Der wichtigste Unterschied, den die Tests festhalten: Verzweigen KOPIERT (Original
bleibt), Zurueckspulen LOESCHT (deshalb mit Sicherung).
"""

import unittest
from pathlib import Path
from types import SimpleNamespace

from app.core import chat_history as ch

REPO = Path(__file__).resolve().parents[2]
ORCH = REPO / "orchestrator"


def _msg(mid, role="user", content="x"):
    return SimpleNamespace(message_id=mid, role=role, content=content,
                           tool_calls=None, meta=None, timestamp=None)


class TitleTests(unittest.TestCase):
    def test_greeting_is_stripped(self):
        """Sonst hiessen alle Gespraeche 'Hallo, kannst du…'."""
        self.assertEqual(
            ch.derive_title("Hallo, kannst du bitte den Quartalsbericht zusammenfassen?"),
            "Kannst du bitte den Quartalsbericht zusammenfassen?",
        )

    def test_punctuation_left_by_the_greeting_is_removed(self):
        """Aus 'Moin! Ich brauche …' wurde sonst der Titel '!' — der Satztrenner
        nahm das Ausrufezeichen als ersten Satz."""
        title = ch.derive_title("Moin! Ich brauche eine Auswertung der Kosten.")
        self.assertNotEqual(title, "!")
        self.assertTrue(title.startswith("Ich brauche"))

    def test_first_sentence_wins(self):
        self.assertEqual(
            ch.derive_title("Warum schlägt der Deploy fehl? Ich habe schon neu gestartet."),
            "Warum schlägt der Deploy fehl?",
        )

    def test_long_text_is_cut_at_a_word(self):
        title = ch.derive_title("Wort " * 60)
        self.assertLessEqual(len(title), ch.TITLE_MAX + 1)
        self.assertTrue(title.endswith("…"))

    def test_empty_stays_empty(self):
        """Kein Titel ist besser als ein erfundener."""
        self.assertEqual(ch.derive_title(""), "")
        self.assertEqual(ch.derive_title("   "), "")

    def test_no_language_model_is_used(self):
        """Ein Titel ist es nicht wert, fuer jedes Gespraech ein Modell zu befragen."""
        src = (ORCH / "app/core/chat_history.py").read_text().lower()
        for token in ("anthropic", "openai", "completion", "llm"):
            with self.subTest(token=token):
                self.assertNotIn(token, src)


class SplitTests(unittest.TestCase):
    MSGS = [_msg("a"), _msg("b"), _msg("c"), _msg("d")]

    def test_split_is_inclusive(self):
        keep, drop = ch.split_at(self.MSGS, "b")
        self.assertEqual([m.message_id for m in keep], ["a", "b"])
        self.assertEqual([m.message_id for m in drop], ["c", "d"])

    def test_last_message_drops_nothing(self):
        keep, drop = ch.split_at(self.MSGS, "d")
        self.assertEqual(len(keep), 4)
        self.assertEqual(drop, [])

    def test_unknown_id_keeps_everything(self):
        """Fail-safe: lieber nichts verwerfen als das Falsche."""
        keep, drop = ch.split_at(self.MSGS, "gibtsnicht")
        self.assertEqual(len(keep), 4)
        self.assertEqual(drop, [])


class SummaryTests(unittest.TestCase):
    def test_only_the_conversation_is_kept(self):
        msgs = [_msg("a", "user", "Frage"), _msg("b", "assistant", "Antwort"),
                _msg("c", "system", "intern")]
        out = ch.build_summary(msgs)
        self.assertIn("Du: Frage", out)
        self.assertIn("Agent: Antwort", out)
        self.assertNotIn("intern", out)

    def test_the_end_survives_when_cut(self):
        """Das Ende eines Gespraechs ist der aktuelle Stand — von vorn kuerzen."""
        msgs = [_msg(str(i), "user", f"Nachricht {i} " + "x" * 300) for i in range(40)]
        out = ch.build_summary(msgs, limit=500)
        self.assertIn("gekürzt", out)
        self.assertIn("Nachricht 39", out)

    def test_empty_messages_are_skipped(self):
        self.assertEqual(ch.build_summary([_msg("a", "user", "   ")]), "")


class SemanticsTests(unittest.TestCase):
    """Der Unterschied, auf den es ankommt."""

    SRC = (ORCH / "app/core/chat_history.py").read_text()

    def test_fork_copies_and_keeps_the_original(self):
        block = self.SRC.split("async def fork(")[1].split("async def rewind(")[0]
        self.assertNotIn("db.delete", block)

    def test_rewind_deletes_but_keeps_a_backup(self):
        """Ein Fehlklick in einer Nachrichtenliste ist zu leicht passiert."""
        block = self.SRC.split("async def rewind(")[1].split("def build_summary(")[0]
        self.assertIn("db.delete", block)
        self.assertIn("backup", block)

    def test_forked_messages_get_new_ids(self):
        """Die alte Kennung steckt in Antwort-Zuordnungen und Kanal-Kontexten —
        zweimal dieselbe waere eine Verwechslung mit Ansage."""
        block = self.SRC.split("async def fork(")[1].split("async def rewind(")[0]
        self.assertIn("uuid.uuid4()", block)

    def test_summarize_does_not_touch_the_source(self):
        block = self.SRC.split("async def summarize_to_new_session(")[1]
        self.assertNotIn("db.delete", block)

    def test_manual_title_is_never_overwritten(self):
        """Sonst verliert jemand seine Benennung, weil er noch etwas geschrieben hat."""
        block = self.SRC.split("async def ensure_title(")[1].split("async def fork(")[0]
        self.assertIn("return row.title", block)


class WiringTests(unittest.TestCase):
    def test_endpoints_exist(self):
        from app.api import agents

        paths = {r.path for r in agents.router.routes}
        for suffix in ("fork", "rewind", "summarize"):
            with self.subTest(endpoint=suffix):
                self.assertIn(
                    f"/agents/{{agent_id}}/chat/sessions/{{session_id}}/{suffix}", paths
                )

    def test_endpoints_check_ownership(self):
        src = (ORCH / "app/api/agents.py").read_text()
        for name in ("fork_chat_session", "rewind_chat_session", "summarize_chat_session"):
            with self.subTest(endpoint=name):
                block = src.split(f"async def {name}")[1].split("\n@router")[0]
                self.assertIn("_check_owner", block)

    def test_title_is_set_on_the_first_user_message(self):
        src = (ORCH / "app/api/ws.py").read_text()
        self.assertIn("ensure_title", src)
        block = src.split("ensure_title")[0][-400:]
        self.assertIn('role == "user"', block)

    def test_a_failing_title_never_breaks_the_chat(self):
        src = (ORCH / "app/api/ws.py").read_text()
        block = src.split("from app.core.chat_history import ensure_title")[1][:400]
        self.assertIn("except Exception", block)


class UiTests(unittest.TestCase):
    CHAT = REPO / "frontend/src/components/agents/chat.tsx"

    def test_message_actions_exist(self):
        src = self.CHAT.read_text()
        self.assertIn("function MessageActions", src)
        self.assertIn("GitBranch", src)
        self.assertIn("Undo2", src)

    def test_actions_appear_on_hover_only(self):
        """Dauerhaft sichtbare Knoepfe an jeder Nachricht machen einen langen
        Verlauf unruhig."""
        block = self.CHAT.read_text().split("function MessageActions")[1][:1200]
        self.assertIn("group-hover:opacity-100", block)

    def test_rewind_asks_first(self):
        src = self.CHAT.read_text()
        block = src.split("const rewindTo")[1][:800]
        self.assertIn("chatConfirm", block)
        self.assertIn("destructive", block)

    def test_fork_does_not_ask(self):
        """Verzweigen nimmt nichts weg — eine Rueckfrage waere nur im Weg."""
        block = self.CHAT.read_text().split("const forkFrom")[1][:600]
        self.assertNotIn("chatConfirm", block)

    def test_summarize_button_exists(self):
        src = self.CHAT.read_text()
        self.assertIn("summarizeToNew", src)

    def test_api_bindings(self):
        src = (REPO / "frontend/src/lib/api.ts").read_text()
        for fn in ("forkChatSession", "rewindChatSession", "summarizeChatSession"):
            with self.subTest(fn=fn):
                self.assertIn(fn, src)


if __name__ == "__main__":
    unittest.main()
