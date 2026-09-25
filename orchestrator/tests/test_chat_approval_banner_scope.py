"""Chat-Freigabe-Banner: nicht an fremde Hintergrund-Jobs gebunden, nicht ans
Streaming-Ende geknuepft (live gemeldet 2026-09-18).

``CommandApproval`` hat kein Chat-/Session-Feld -- das Banner in einem
Agenten-Chat konnte deshalb eine voellig unbeteiligte Freigabe zeigen (z.B.
die naechtliche Nachtschicht-Reflexion, die einen Wissenseintrag schreibt),
und verschwand wieder, sobald der aktuelle Chat mit Streamen fertig war,
UNABHAENGIG davon, ob die Freigabe serverseitig noch offen stand. Reiner
Quelltext-Scan wie bei den uebrigen Frontend-Vertraegen in diesem Baum
(siehe test_autonomy_sudo_coupling.py) -- kein JS-Test-Runner vorhanden.
"""
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CHAT = REPO / "frontend/src/components/agents/chat.tsx"


class ApprovalBannerScopeTests(unittest.TestCase):
    def setUp(self):
        self.src = CHAT.read_text()

    def test_reflection_change_is_excluded_from_the_chat_banner(self):
        """Nachtschicht-Freigaben duerfen nie im Chat-Banner landen -- die
        Approvals-Seite bucket sie schon separat ueber dasselbe tool-Feld."""
        self.assertIn('a.tool !== "reflection_change"', self.src)

    def test_the_banner_no_longer_clears_on_stream_end(self):
        """Der fruehere Bug: `!isWaiting` wischte pendingApproval hart weg,
        egal ob die Freigabe noch offen war. Diese Zeile darf nicht
        zurueckkommen."""
        self.assertNotIn(
            'if (!isWaiting) { setPendingApproval(null); return; }', self.src,
            "Banner wird wieder beim Streaming-Ende geloescht statt bei "
            "tatsaechlicher Aufloesung.",
        )

    def test_polling_is_not_gated_on_isWaiting_anymore(self):
        """Die Freigabe-Abfrage soll laufen, solange der Chat offen ist --
        nicht nur waehrend der Agent gerade schreibt."""
        block = self.src.split("Poll for approvals that need this chat", 1)[1]
        block = block[:2000]
        self.assertNotIn("[isWaiting, agentId]", block)
        self.assertNotIn("isWaiting]", block)
        # Seit 24.09.: zusaetzlich hasPendingApproval (enger Takt, solange ein
        # Banner steht) — entscheidend bleibt: kein isWaiting in den Abhaengigkeiten.
        self.assertIn("[agentId, hasPendingApproval]", block)


if __name__ == "__main__":
    unittest.main()
