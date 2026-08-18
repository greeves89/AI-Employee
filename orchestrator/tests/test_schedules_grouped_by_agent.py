"""Zeitplaene werden nach Agent gruppiert und starten eingeklappt.

Wunsch des Nutzers vom 18.08.2026 zur Uebersicht unter /tasks:
„scheduled Tasks muessen nach Agent gruppiert werden... es muss initial alles
eingeklappt sein!"

Vorher war es eine flache Liste ueber alle Agenten hinweg. Auf der Anlage
laufen sieben Agenten mit je mehreren Zeitplaenen — da sieht man nicht mehr,
WESSEN Plan gerade wann laeuft.
"""

import unittest
from pathlib import Path

SEITE = (Path(__file__).resolve().parents[2] / "frontend/src/app/tasks/page.tsx").read_text()


class GroupedByAgentTests(unittest.TestCase):
    def test_the_schedules_are_grouped(self):
        self.assertIn("const gruppen = useMemo(", SEITE)

    def test_the_group_carries_the_agent_name_not_just_the_id(self):
        """Eine Kennung wie `2ad91565` sagt niemandem etwas."""
        self.assertIn("namen.get(id)", SEITE)

    def test_a_schedule_without_an_agent_still_shows_up(self):
        """Zeitplaene ohne festen Agenten laufen ueber die Lastverteilung —
        ohne eigene Gruppe fielen sie stillschweigend aus der Liste."""
        self.assertIn("Ohne festen Agenten", SEITE)

    def test_the_next_run_comes_first_inside_a_group(self):
        self.assertIn("a.next_run_at.localeCompare(b.next_run_at)", SEITE)

    def test_groups_are_ordered_by_name(self):
        self.assertIn("a.name.localeCompare(b.name)", SEITE)


class EverythingStartsCollapsedTests(unittest.TestCase):
    def test_no_group_is_open_at_the_start(self):
        """Der ausdrueckliche Wunsch: initial alles eingeklappt."""
        stelle = SEITE.split("offeneGruppen, setOffeneGruppen", 1)
        self.assertEqual(len(stelle), 2, "kein Zustand fuer offene Gruppen")
        self.assertIn("useState<Set<string>>(new Set())", stelle[1][:80])

    def test_the_cards_only_render_when_the_group_is_open(self):
        self.assertIn("{offen && gruppe.plaene.map(", SEITE)

    def test_a_closed_group_still_says_when_it_next_runs(self):
        """Sonst muesste man jede Gruppe aufklappen, nur um das zu sehen."""
        self.assertIn("!offen && gruppe.plaene[0]", SEITE)

    def test_there_is_a_way_to_open_everything_at_once(self):
        self.assertIn("Alle aufklappen", SEITE)
        self.assertIn("Alle zuklappen", SEITE)


if __name__ == "__main__":
    unittest.main()
