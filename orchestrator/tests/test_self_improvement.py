"""Self-Improvement sichtbar machen (#13).

Die Mechanik lief laengst: die Nachtschicht schreibt Skill-Entwuerfe, der
Verbesserungs-Motor ueberarbeitet schlecht bewertete Skills, aus Gespraechen entstehen
dauerhafte Erinnerungen. Nur sah das niemand — es gab keine Flaeche, auf der steht, was
dabei herauskommt. Laut der eigenen Strategie-Roadmap des Nutzers der beste Hebel,
weil nichts Neues gebaut, sondern Vorhandenes gezeigt wird.
"""

import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ORCH = REPO / "orchestrator"


class EndpointTests(unittest.TestCase):
    SRC = ORCH / "app/api/analytics.py"

    def test_endpoint_exists(self):
        self.assertIn('@router.get("/self-improvement")', self.SRC.read_text())

    def test_nothing_is_collected_additionally(self):
        """Es werden nur vorhandene Tabellen gelesen — kein neues Mitschreiben."""
        block = self.SRC.read_text().split("async def self_improvement")[1]
        self.assertNotIn("db.add(", block)
        self.assertNotIn("CREATE TABLE", block)

    def test_drafts_come_first(self):
        """Entwuerfe sind das, wo ein Mensch etwas tun soll."""
        block = self.SRC.read_text().split("async def self_improvement")[1]
        self.assertLess(block.index('"awaiting_review"'), block.index('"learned"'))

    def test_origin_is_classified(self):
        block = self.SRC.read_text().split("async def self_improvement")[1]
        for origin in ("nachtschicht", "agent", "import", "mensch"):
            with self.subTest(origin=origin):
                self.assertIn(origin, block)

    def test_missing_source_column_does_not_break_the_page(self):
        """Ohne die Spalte bleibt der Rest aussagekraeftig."""
        block = self.SRC.read_text().split("async def self_improvement")[1]
        self.assertIn("except Exception", block)


class UiTests(unittest.TestCase):
    PAGE = REPO / "frontend/src/app/learning/page.tsx"

    def test_page_exists(self):
        self.assertTrue(self.PAGE.exists())

    def test_reachable_from_the_navigation(self):
        """Eine Flaeche, die man nicht findet, macht nichts sichtbar."""
        nav = (REPO / "frontend/src/components/layout/sidebar.tsx").read_text()
        self.assertIn('href: "/learning"', nav)

    def test_shows_what_needs_a_human(self):
        src = self.PAGE.read_text()
        self.assertIn("Durchsicht", src)
        self.assertIn("awaiting_review", src)

    def test_reverted_improvements_are_shown_too(self):
        """Dass eine Nachbesserung zurueckgenommen wurde, ist die ehrlichere Zahl."""
        src = self.PAGE.read_text()
        self.assertIn("improvements_reverted", src)
        self.assertIn("verworfen", src)

    def test_api_binding_exists(self):
        api = (REPO / "frontend/src/lib/api.ts").read_text()
        self.assertIn("getSelfImprovement", api)
        self.assertIn("analytics/self-improvement", api)


if __name__ == "__main__":
    unittest.main()
