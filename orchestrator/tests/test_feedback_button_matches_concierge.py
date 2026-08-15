"""Der Feedback-Knopf sieht aus wie der Concierge-Knopf daneben.

Wunsch des Nutzers (15.08.2026): „Kannst du den Feedback Button rund machen wie
den concierge Button?" Zwei Schaltflaechen nebeneinander, von denen eine eine
Pille mit Text und die andere ein Kreis ist, lesen sich wie zwei verschiedene
Bausaetze.

Geprueft werden die ZAHLEN, nicht „sieht aehnlich aus": beide 44px, beide
vollrund, beide 20px vom unteren Rand. Der Feedback-Knopf sitzt 76px von rechts
— das ist 20 + 44 + 12, also genau eine Knopfbreite plus Luecke neben dem
Concierge. Waere das nicht so, ueberdeckten sich beide.

Und die Beschriftung: sie faellt optisch weg, bleibt aber im Markup. Ein Knopf,
der nur fuer Sehende beschriftet ist, ist fuer alle anderen ein leerer Kreis.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CSS = (ROOT / "frontend/src/components/feedback/feedback.css").read_text()
TSX = (ROOT / "frontend/src/components/feedback/feedback-widget.tsx").read_text()
CONCIERGE = (ROOT / "frontend/src/components/concierge/concierge-widget.tsx").read_text()


def _fab() -> str:
    """Nur die Regel des Knopfes, nicht die ganze Datei."""
    return CSS.split(".fbw-fab {", 1)[1].split("}", 1)[0]


class ItIsRoundTests(unittest.TestCase):
    def test_it_is_a_circle_not_a_pill(self):
        self.assertIn("border-radius: 9999px", _fab())

    def test_it_has_no_padding_left_over_from_the_pill(self):
        """Ohne das bliebe der Kreis oval, sobald das Symbol schmaler ist."""
        self.assertIn("padding: 0", _fab())

    def test_the_icon_is_centred(self):
        block = _fab()
        self.assertIn("align-items: center", block)
        self.assertIn("justify-content: center", block)


class ItMatchesTheConciergeTests(unittest.TestCase):
    """Die Masse stammen aus concierge-widget.tsx (h-11 w-11 = 44px,
    bottom-5 = 20px). Aendert sich der eine, muss der andere mitgehen — genau
    dafuer steht dieser Test hier."""

    def test_the_concierge_is_still_the_reference(self):
        self.assertIn("h-11 w-11", CONCIERGE)
        self.assertIn("rounded-full", CONCIERGE)
        self.assertIn("bottom-5 right-5", CONCIERGE)

    def test_same_size(self):
        block = _fab()
        self.assertIn("width: 44px", block)
        self.assertIn("height: 44px", block)

    def test_same_distance_from_the_bottom(self):
        self.assertIn("bottom: 20px", _fab())

    def test_it_sits_exactly_one_button_to_the_left(self):
        """20 (Rand) + 44 (Concierge) + 12 (Luecke) = 76. Sonst ueberdecken sie
        sich oder stehen schief."""
        self.assertIn("right: 76px", _fab())

    def test_it_grows_on_hover_like_the_concierge(self):
        """Der Concierge waechst (hover:scale-105) statt aufzuhellen — als Paar
        muessen sie sich gleich verhalten."""
        self.assertIn("hover:scale-105", CONCIERGE)
        # Nur die Regel DIESES Knopfes — `filter: brightness` gehoert noch zum
        # Senden-Knopf im Dialog und ist dort richtig.
        hover = CSS.split(".fbw-fab:hover {", 1)[1].split("}", 1)[0]
        self.assertIn("transform: scale(1.05)", hover)
        self.assertNotIn("filter: brightness", hover)


class TheLabelSurvivesForScreenReadersTests(unittest.TestCase):
    """Ein Kreis ohne Text ist fuer Sehende erkennbar, fuer andere nicht."""

    def test_the_button_has_an_accessible_name(self):
        self.assertIn('aria-label="Feedback geben"', TSX)

    def test_the_text_is_hidden_visually_not_removed(self):
        self.assertIn("fbw-fab-label", TSX)
        self.assertIn("Feedback</span>", TSX)

    def test_the_hiding_is_the_screen_reader_safe_kind(self):
        """``display: none`` wuerde den Text auch fuer Screenreader entfernen —
        deshalb ausblenden statt entfernen."""
        block = CSS.split(".fbw-fab .fbw-fab-label {", 1)[1].split("}", 1)[0]
        self.assertIn("clip-path: inset(50%)", block)
        self.assertNotIn("display: none", block)


if __name__ == "__main__":
    unittest.main()
