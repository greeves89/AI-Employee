"""Was braucht wirklich einen Menschen? (#11, Concierge-Umbau)

Die Regeln allein — ohne Datenbank, damit jede für sich prüfbar ist.

Der Anlass: der Concierge war eine Kennzahlen-Kachel, bei der die Ampel den Alarm
tragen musste. Dabei landete „angehalten" in derselben Liste wie „abgestürzt". Die
Lehre steckt in ``verdict_for``: die Ampel wird AUS den Punkten gebildet, nicht
daneben — sonst laufen beide auseinander.
"""

import unittest
from datetime import datetime, timedelta, timezone

from app.core.attention import (
    BROKEN,
    TOKEN_WARN_AHEAD,
    WAITING,
    budget_state,
    item,
    skips_proactive,
    token_state,
    verdict_for,
)

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)


class VerdictTests(unittest.TestCase):
    def test_nothing_to_do_is_quiet(self):
        self.assertEqual(verdict_for([]), "alles ruhig")

    def test_waiting_is_amber_not_red(self):
        self.assertEqual(verdict_for([item("a", WAITING, "t", "d")]), "wartet auf dich")

    def test_one_broken_beats_ten_waiting(self):
        items = [item("a", WAITING, "t", "d") for _ in range(10)]
        items.append(item("b", BROKEN, "t", "d"))
        self.assertEqual(verdict_for(items), "handlungsbedarf")


class TokenTests(unittest.TestCase):
    def test_expired_is_broken(self):
        """Der wertvollste Fall: er scheitert STILL. Nichts wird rot, es hoert
        einfach auf zu funktionieren."""
        self.assertEqual(token_state(NOW - timedelta(hours=1), NOW), BROKEN)

    def test_expiring_soon_is_a_heads_up(self):
        self.assertEqual(token_state(NOW + timedelta(days=1), NOW), WAITING)

    def test_far_future_is_silent(self):
        self.assertIsNone(token_state(NOW + timedelta(days=90), NOW))

    def test_no_expiry_is_not_an_alarm(self):
        """Nicht jeder Zugang hat ein Ablaufdatum. „Unbekannt" als Alarm zu werten
        hiesse, dauerhaft rot zu sein."""
        self.assertIsNone(token_state(None, NOW))

    def test_naive_timestamp_is_treated_as_utc(self):
        """Aus der Datenbank kommen je nach Spalte auch Zeitangaben ohne Zone —
        ein Vergleich damit wuerde sonst mit TypeError abbrechen."""
        self.assertEqual(token_state(datetime(2026, 8, 9, 11, 0), NOW), BROKEN)

    def test_boundary(self):
        self.assertEqual(token_state(NOW + TOKEN_WARN_AHEAD, NOW), WAITING)
        self.assertIsNone(token_state(NOW + TOKEN_WARN_AHEAD + timedelta(seconds=1), NOW))


class BudgetTests(unittest.TestCase):
    def test_no_cap_means_nothing_to_report(self):
        """„Unbegrenzt" ist eine Entscheidung, kein Versaeumnis."""
        self.assertIsNone(budget_state(500.0, None))
        self.assertIsNone(budget_state(500.0, 0))

    def test_exhausted_is_broken(self):
        self.assertEqual(budget_state(100.0, 100.0), BROKEN)
        self.assertEqual(budget_state(120.0, 100.0), BROKEN)

    def test_close_is_a_heads_up(self):
        self.assertEqual(budget_state(95.0, 100.0), WAITING)

    def test_plenty_left_is_silent(self):
        self.assertIsNone(budget_state(50.0, 100.0))

    def test_nothing_spent_yet(self):
        self.assertIsNone(budget_state(None, 100.0))


class ProactiveTests(unittest.TestCase):
    def test_stopped_with_duties_is_the_silent_failure(self):
        """Genau so sammelten beim Kunden zwei Agenten vier Wochen lang
        fehlgeschlagene Laeufe, ohne dass es auffiel."""
        self.assertTrue(skips_proactive(
            {"proactive": {"enabled": True, "responsibilities": ["Buchhaltung"]}}
        ))

    def test_without_duties_nothing_is_missed(self):
        self.assertFalse(skips_proactive({"proactive": {"enabled": True, "responsibilities": []}}))
        self.assertFalse(skips_proactive({"proactive": {"enabled": False, "responsibilities": ["x"]}}))
        self.assertFalse(skips_proactive({}))
        self.assertFalse(skips_proactive(None))


class ItemShapeTests(unittest.TestCase):
    def test_every_item_carries_the_same_fields(self):
        """Die Oberflaeche zeichnet die Liste einheitlich — ein Punkt, dem ein Feld
        fehlt, faellt dort als undefined auf."""
        one = item("kind", BROKEN, "Titel", "Erklärung")
        for field in ("kind", "severity", "title", "detail", "agent_id",
                      "action", "action_label", "link", "count"):
            self.assertIn(field, one)

    def test_action_and_link_may_coexist(self):
        """Manches laesst sich hier erledigen UND anderswo genauer ansehen."""
        one = item("agent_error", BROKEN, "A", "d", action="restart_agent", link="/agents/a")
        self.assertEqual(one["action"], "restart_agent")
        self.assertEqual(one["link"], "/agents/a")


if __name__ == "__main__":
    unittest.main()
