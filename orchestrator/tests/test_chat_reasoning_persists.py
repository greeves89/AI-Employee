"""Denktiefe pro Gespraech (v1.234.0).

Der Reasoning-Selector existierte schon, lebte aber nur im React-State — jeder
Remount setzte ihn auf „Auto" zurueck. Jetzt wird das Level in chat_sessions
persistiert (Vorbild: title/pinned). Die Tests halten die drei Stellen zusammen,
an denen dieselbe Werteliste gilt: Modell-Konstante, ws-Whitelist, Frontend.
"""

import re
import unittest
from pathlib import Path

from app.models.chat_session import REASONING_LEVELS, ChatSession

REPO = Path(__file__).resolve().parents[2]
ORCH = REPO / "orchestrator"


class LevelsTests(unittest.TestCase):
    def test_the_canonical_levels(self):
        """Genau diese sechs. „max" kam mit v1.234.0 dazu und MEINTE bis
        1.312.x das, was die Anbieter „xhigh" nennen; seit 1.313.0 sind es zwei
        getrennte Stufen, weil die GPT-5.6-Familie oberhalb von xhigh noch ein
        echtes „max" kennt."""
        self.assertEqual(REASONING_LEVELS,
                         ("off", "low", "medium", "high", "xhigh", "max"))

    def test_ws_whitelist_uses_the_shared_constant(self):
        """Die Whitelist in ws.py war frueher ein eigenes Tuple — genau so ging
        „max" beim ersten Anlauf verloren. Jetzt muss sie importieren."""
        src = (ORCH / "app/api/ws.py").read_text()
        self.assertIn("from app.models.chat_session import REASONING_LEVELS", src)
        self.assertIn("if reasoning not in REASONING_LEVELS", src)
        # Kein zweites, hartkodiertes Level-Tuple mehr im Sende-Pfad:
        self.assertNotIn('("off", "low", "medium", "high")', src)

    def test_frontend_offers_exactly_the_backend_levels(self):
        """UI und Whitelist duerfen nicht auseinanderlaufen — ein Level, das nur
        das Frontend kennt, wuerde serverseitig still verschluckt."""
        src = (REPO / "frontend/src/components/agents/chat.tsx").read_text()
        block = src.split("const REASONING_OPTIONS")[1].split("];")[0]
        values = set(re.findall(r'value: "([a-z]*)"', block)) - {""}
        self.assertEqual(values, set(REASONING_LEVELS))


class ModelTests(unittest.TestCase):
    def test_column_exists(self):
        self.assertIn("reasoning_level", ChatSession.__table__.columns)
        self.assertTrue(ChatSession.__table__.columns["reasoning_level"].nullable)

    def test_startup_ddl_covers_existing_databases(self):
        """Alembic ist mehrkoepfig und tot — ohne den Startup-Ensure bekaeme eine
        bestehende DB die Spalte nie und jeder PATCH liefe auf einen 500."""
        src = (ORCH / "app/main.py").read_text()
        self.assertIn(
            "ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS reasoning_level", src
        )


class PatchTests(unittest.TestCase):
    def test_update_accepts_reasoning(self):
        from app.api.agents import ChatSessionUpdate

        self.assertIn("reasoning", ChatSessionUpdate.model_fields)
        # None muss „unangetastet" bleiben — sonst loescht jedes Umbenennen das Level.
        self.assertIsNone(ChatSessionUpdate(title="x").reasoning)

    def test_unknown_level_is_rejected(self):
        """422 statt still speichern: was hier durchrutscht, landet spaeter als
        CLI-Flag bzw. Request-Body-Wert beim Agenten."""
        src = (ORCH / "app/api/agents.py").read_text()
        block = src.split("async def update_chat_session")[1].split("\n@router")[0]
        self.assertIn("REASONING_LEVELS", block)
        self.assertIn("422", block)
        # "" raeumt zurueck auf Auto (NULL), kein eigener Sentinel-Wert:
        self.assertIn("lvl or None", block)

    def test_session_list_ships_the_level(self):
        """Ohne das Feld in der Liste kann das Frontend nichts wiederherstellen."""
        src = (ORCH / "app/api/agents.py").read_text()
        block = src.split("async def get_chat_sessions")[1].split("\n@router")[0]
        self.assertIn("reasoning_level", block)
        self.assertIn('"reasoning"', block)


class InheritanceTests(unittest.TestCase):
    """Abzweig und Fortsetzung sind dasselbe Gespraech in neu — gleiche Denktiefe.
    Die Ruecklauf-Sicherung (rewind) ist ein Archiv und bleibt bewusst ohne."""

    SRC = (ORCH / "app/core/chat_history.py").read_text()

    def test_fork_inherits(self):
        block = self.SRC.split("async def fork(")[1].split("async def rewind(")[0]
        self.assertIn("reasoning_level", block)

    def test_summarize_inherits(self):
        block = self.SRC.split("async def summarize_to_new_session(")[1]
        self.assertIn("reasoning_level", block)

    def test_rewind_backup_stays_plain(self):
        block = self.SRC.split("async def rewind(")[1].split("def build_summary(")[0]
        self.assertNotIn("reasoning_level", block)


class UiTests(unittest.TestCase):
    CHAT = (REPO / "frontend/src/components/agents/chat.tsx").read_text()

    def test_pick_persists(self):
        """Die Wahl muss den Server erreichen — sonst ist nach dem naechsten
        Remount wieder „Auto" zu sehen, der urspruengliche Fehler."""
        block = self.CHAT.split("const pickReasoning")[1][:800]
        self.assertIn("updateChatSession", block)

    def test_switching_sessions_restores_the_level(self):
        block = self.CHAT.split("const switchSession")[1][:600]
        self.assertIn("setReasoning", block)

    def test_send_callback_sees_the_current_level(self):
        """Die useCallback-Deps liessen `reasoning` aus — ein frisch gewaehltes
        Level ohne weiteren Tastendruck wurde veraltet mitgesendet."""
        deps = re.search(
            r"\}, \[input, pendingImages, pendingFiles, activeSessionId, agentId(.*?)\]\);",
            self.CHAT,
        )
        self.assertIsNotNone(deps)
        self.assertIn("reasoning", deps.group(1))


if __name__ == "__main__":
    unittest.main()
