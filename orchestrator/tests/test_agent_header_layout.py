"""Kompakter Kopfbereich der Agentenseite (#537).

Auf einem kleinen Laptop assen drei feste Bloecke ueber dem Chat die Hoehe, sodass
das wichtigste Bedienelement — das Chatfenster — in die untere Haelfte gedraengt wurde
und die Seite eingebettet statt responsiv wirkte:

1. die Beschreibung umbrach mehrzeilig ohne Kuerzung,
2. Umbenennen/Neustart/Status sassen am rechten Rand, ohne sichtbaren Bezug zum Namen,
3. das Monatsbudget war eine vollbreite Karte mit waagerechtem Balken.
"""

import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HEADER = REPO / "frontend/src/components/layout/header.tsx"
PAGE = REPO / "frontend/src/app/agents/[id]/page.tsx"


class SubtitleTests(unittest.TestCase):
    def test_subtitle_is_one_line(self):
        """Die Beschreibung eines Agenten kann mehrere Saetze lang sein; ungekuerzt
        schob sie den Chat nach unten."""
        src = HEADER.read_text()
        block = src.split("{subtitle && (")[1].split(")}")[0]
        self.assertIn("truncate", block)

    def test_full_text_stays_reachable(self):
        """Kuerzen darf keine Information verschlucken — der volle Text im Tooltip."""
        block = HEADER.read_text().split("{subtitle && (")[1].split(")}")[0]
        self.assertIn("title={subtitle}", block)


class TitleAdornmentTests(unittest.TestCase):
    def test_header_offers_a_slot_next_to_the_title(self):
        src = HEADER.read_text()
        self.assertIn("titleAdornment", src)
        # Der Slot steht IM Titel-Block, nicht bei den actions am rechten Rand.
        title_block = src.split('<h2 className=')[1].split("</div>")[0]
        self.assertIn("titleAdornment", title_block)

    def test_rename_moved_out_of_the_right_hand_actions(self):
        """Am rechten Rand war der Bezug zum Namen nicht erkennbar."""
        src = PAGE.read_text()
        self.assertIn("titleAdornment=", src)
        actions = src.split("actions={")[1].split("titleAdornment")[0] if "actions={" in src else ""
        self.assertNotIn("Agent umbenennen", actions)

    def test_rename_is_icon_only(self):
        """Neben dem Namen kostet die Beschriftung Breite und sagt nichts, was
        Symbol plus Tooltip nicht schon sagen."""
        src = PAGE.read_text()
        block = src.split('title="Agent umbenennen"')[1][:400]
        self.assertNotIn("Umbenennen\n", block)
        self.assertIn("Edit3", block)


class BudgetTests(unittest.TestCase):
    SRC = PAGE.read_text()

    def test_budget_is_one_row_not_a_card(self):
        block = self.SRC.split("function BudgetBar")[1].split("\n}")[0]
        self.assertNotIn("rounded-xl", block)
        self.assertIn("flex items-center", block)

    def test_the_number_survives(self):
        """Kompakter heisst nicht: Information weg. Verbrauch und Grenze bleiben."""
        block = self.SRC.split("function BudgetBar")[1].split("\n}")[0]
        self.assertIn("spent.toFixed(2)", block)
        self.assertIn("budget.toFixed(2)", block)

    def test_utilisation_stays_visible(self):
        block = self.SRC.split("function BudgetBar")[1].split("\n}")[0]
        self.assertIn("width: `${pct}%`", block)

    def test_the_action_is_still_explained(self):
        """Was bei Erreichen des Budgets passiert, war vorher Fliesstext in der
        Karte — jetzt im Tooltip, aber nicht verschwunden."""
        block = self.SRC.split("function BudgetBar")[1].split("\n}")[0]
        self.assertIn("title=", block)
        self.assertIn("anhalten", block)


class HeaderHeightTests(unittest.TestCase):
    def test_vertical_padding_was_reduced(self):
        """py-4 auf beiden Seiten waren 32px, die dem Chat fehlten."""
        src = HEADER.read_text()
        self.assertIn("py-2.5", src)
        self.assertNotIn("py-4", src)

    def test_title_scales_down_on_small_screens(self):
        src = HEADER.read_text()
        self.assertIn("text-xl", src)
        self.assertIn("sm:text-2xl", src)


if __name__ == "__main__":
    unittest.main()
