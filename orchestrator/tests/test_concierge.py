"""Admin-Concierge (#11) — „laeuft alles?" in einer Antwort.

Die Zahlen gab es alle schon, nur auf fuenf verschiedenen Seiten. Die haeufigsten
Fragen eines Administrators beantwortete keine davon direkt.

Zwei Entscheidungen, die hier festgenagelt werden:
kein Sprachmodell dahinter (ein Concierge, der eine Zahl halluziniert, ist schlimmer
als gar keiner) und eine kurze, serverseitig geprueft feste Aktionsliste.
"""

import unittest
from pathlib import Path

from app.api import concierge

REPO = Path(__file__).resolve().parents[2]
ORCH = REPO / "orchestrator"


class ActionAllowlistTests(unittest.TestCase):
    def test_nothing_destructive_is_offered(self):
        """Ein Widget in der Ecke ist der falsche Ort, um einen Agenten zu loeschen."""
        for forbidden in ("delete", "remove", "reset", "drop", "wipe", "purge"):
            for action in concierge.SAFE_ACTIONS:
                with self.subTest(action=action, forbidden=forbidden):
                    self.assertNotIn(forbidden, action.lower())

    def test_list_stays_short(self):
        self.assertLessEqual(len(concierge.SAFE_ACTIONS), 6)

    def test_every_action_has_a_german_label(self):
        for action, label in concierge.SAFE_ACTIONS.items():
            with self.subTest(action=action):
                self.assertTrue(label.strip())
                self.assertNotEqual(label, action)

    def test_allowlist_is_enforced_server_side(self):
        """Ein Widget, das nur die sicheren Knoepfe zeigt, ist keine Absicherung —
        jeder kann den Aufruf direkt schicken."""
        src = (ORCH / "app/api/concierge.py").read_text()
        block = src.split("async def concierge_action")[1]
        self.assertIn("SAFE_ACTIONS", block)
        self.assertIn("Aktion nicht erlaubt", block)


class AccessTests(unittest.TestCase):
    SRC = ORCH / "app/api/concierge.py"

    def test_both_endpoints_are_admin_only(self):
        src = self.SRC.read_text()
        self.assertEqual(src.count("Depends(require_admin)"), 2)
        self.assertNotIn("require_auth)", src.replace("require_auth_or_agent", ""))


class NoHallucinationTests(unittest.TestCase):
    SRC = ORCH / "app/api/concierge.py"

    def test_no_language_model_is_involved(self):
        """Bewusst: der Concierge setzt Abfragen zusammen, er formuliert nicht."""
        src = self.SRC.read_text().lower()
        for token in ("anthropic", "openai", "llm", "completion", "prompt"):
            with self.subTest(token=token):
                self.assertNotIn(token, src)

    def test_stale_threshold_is_shared_with_the_watchdog(self):
        """Sonst steht hier eine andere Zahl als in der Aufgabenliste."""
        src = self.SRC.read_text()
        self.assertIn("_STALE_TASK_THRESHOLD", src)

    def test_verdict_is_derived_not_invented(self):
        src = self.SRC.read_text()
        for verdict in ("handlungsbedarf", "wartet auf dich", "alles ruhig"):
            with self.subTest(verdict=verdict):
                self.assertIn(verdict, src)


class WiringTests(unittest.TestCase):
    def test_router_is_registered(self):
        from app.api.router import api_router

        paths = {r.path for r in api_router.routes}
        self.assertIn("/concierge/overview", paths)
        self.assertIn("/concierge/action", paths)

    def test_widget_hides_itself_for_non_admins(self):
        src = (REPO / "frontend/src/components/concierge/concierge-widget.tsx").read_text()
        self.assertIn("isAdmin", src)
        self.assertIn("return null", src)

    def test_actions_ask_before_acting(self):
        src = (REPO / "frontend/src/components/concierge/concierge-widget.tsx").read_text()
        self.assertIn("confirm(", src)

    def test_widget_is_mounted_globally(self):
        src = (REPO / "frontend/src/app/layout.tsx").read_text()
        self.assertIn("ConciergeWidget", src)


if __name__ == "__main__":
    unittest.main()
