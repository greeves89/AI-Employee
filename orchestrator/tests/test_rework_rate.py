"""Nacharbeitsquote — wird der Agent messbar besser?

Kosten und Laufzahl sagen nichts ueber die Qualitaet: ein Agent kann 500 Laeufe
verbrauchen und nichts zustande gebracht haben. Die Entwicklungs-Karte zeigte schon
Fehlerquote, Bewertungen und Plan-Treue — es fehlte die Frage, wie oft eine Aufgabe
noch einmal angefasst werden musste.

Zwei Signale, beide schon in den Daten:
  * ``metadata.resumed_from_task`` — der Lauf lief nicht in einem Zug durch
  * Bewertung 1 oder 2 — der Mensch hat die Arbeit zurueckgegeben
"""

import re
import unittest
from pathlib import Path

from app.api.analytics import rework_union

REPO = Path(__file__).resolve().parents[2]
ORCH = REPO / "orchestrator"


class UnionTests(unittest.TestCase):
    def test_both_signals_count(self):
        entry = {"resumed": {"a"}, "poor": {"b"}}
        self.assertEqual(rework_union(entry), {"a", "b"})

    def test_a_case_with_both_counts_once(self):
        """Eine fortgesetzte UND schlecht bewertete Aufgabe ist ein Fall, nicht zwei."""
        entry = {"resumed": {"a"}, "poor": {"a"}}
        self.assertEqual(len(rework_union(entry)), 1)

    def test_no_data_is_not_an_error(self):
        self.assertEqual(rework_union(None), set())
        self.assertEqual(rework_union({"resumed": set(), "poor": set()}), set())


class WiringTests(unittest.TestCase):
    """Eine Regel, zwei Anzeigen — die duerfen nicht auseinanderlaufen."""

    def test_rule_lives_in_one_function(self):
        src = (ORCH / "app/api/analytics.py").read_text()
        self.assertEqual(
            len(re.findall(r"""\.get\(["']resumed_from_task["']\)""", src)), 1,
            "Das Fortsetzungs-Signal wird an mehr als einer Stelle ausgewertet.",
        )
        self.assertEqual(
            len(re.findall(r"TaskRating\.rating <= 2", src)), 1,
            "Die Schwelle fuer schlechte Bewertungen steht doppelt im Code.",
        )

    def test_both_endpoints_use_the_helper(self):
        src = (ORCH / "app/api/analytics.py").read_text()
        self.assertGreaterEqual(src.count("rework_task_ids("), 3,
                                "Definition + beide Aufrufer erwartet.")

    def test_development_reports_the_breakdown(self):
        src = (ORCH / "app/api/analytics.py").read_text()
        for key in ('"rate_recent"', '"rate_older"', '"resumed"', '"poorly_rated"'):
            self.assertIn(key, src)

    def test_agent_table_reports_it_too(self):
        src = (ORCH / "app/api/analytics.py").read_text()
        self.assertIn('"rework_rate_pct"', src)

    def test_trend_takes_rework_into_account(self):
        """Weniger Nacharbeit bei gleicher Laufzahl ist Fortschritt — sonst bliebe
        die Kennzahl Zierde und wuerde das Urteil nicht beeinflussen."""
        src = (ORCH / "app/api/analytics.py").read_text()
        trend_block = src.split("trend = \"zu wenig Daten\"")[1].split("config = agent.config")[0]
        self.assertIn("rework_rate_recent", trend_block)
        self.assertIn("rework_rate_older", trend_block)


class UiTests(unittest.TestCase):
    def test_development_card_shows_it(self):
        src = (REPO / "frontend/src/components/agents/development-card.tsx").read_text()
        self.assertIn("Nacharbeit", src)
        self.assertIn("data.rework.rate", src)

    def test_analytics_tab_shows_it(self):
        """Der offene Punkt hiess ausdruecklich 'im Analytics-Tab'."""
        src = (REPO / "frontend/src/app/analytics/page.tsx").read_text()
        self.assertIn("Nacharbeit", src)
        self.assertIn("rework_rate_pct", src)

    def test_type_is_declared(self):
        src = (REPO / "frontend/src/lib/api.ts").read_text()
        self.assertIn("rework:", src)


if __name__ == "__main__":
    unittest.main()
