"""Feedback und Concierge wohnen im Sidebar-Kopf — nichts schwebt mehr unten rechts.

Wunsch des Nutzers (18.08.2026): Die schwebenden Knoepfe unten rechts haben
Eingabefelder ueberdeckt. Beide Einstiege sitzen jetzt oben im Sidebar-Kopf
neben dem Logo — der Concierge (nur Admins) als Lucide-Icon im selben Stil wie
der Feedback-Knopf. Die FABs sind komplett raus, nicht nur versteckt.

Ersetzt test_feedback_button_matches_concierge.py: dessen Prämisse (zwei FABs
als Paar unten rechts) gibt es nicht mehr.
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CSS = (ROOT / "frontend/src/components/feedback/feedback.css").read_text()
FEEDBACK = (ROOT / "frontend/src/components/feedback/feedback-widget.tsx").read_text()
CONCIERGE = (ROOT / "frontend/src/components/concierge/concierge-widget.tsx").read_text()
SIDEBAR = (ROOT / "frontend/src/components/layout/sidebar.tsx").read_text()


class TheFloatingButtonsAreGoneTests(unittest.TestCase):
    """Ein entfernter Knopf darf nicht als toter Code oder tote CSS-Regel bleiben."""

    def test_the_feedback_fab_is_gone_from_markup_and_css(self):
        self.assertNotIn("fbw-fab", FEEDBACK)
        self.assertNotIn(".fbw-fab", CSS)

    def test_the_concierge_renders_nothing_while_closed(self):
        """Frueher stand hier ``{!open && (<button ...>)}`` — der FAB. Ohne ihn
        gibt es im geschlossenen Zustand nichts mehr zu rendern."""
        self.assertNotIn("!open", CONCIERGE)

    def test_the_concierge_panel_itself_survives(self):
        """Raus sollte nur der Ausloeser — das Panel unten rechts bleibt."""
        self.assertIn("{open && (", CONCIERGE)


class TheSidebarIsTheOnlyWayInTests(unittest.TestCase):
    """Beide Widgets warten auf ihr Fenster-Event; ausgeloest wird es im Sidebar-Kopf."""

    def test_feedback_listens_and_the_sidebar_dispatches(self):
        self.assertIn('addEventListener("feedback-widget:open"', FEEDBACK)
        self.assertIn('new CustomEvent("feedback-widget:open")', SIDEBAR)

    def test_concierge_listens_and_the_sidebar_dispatches(self):
        self.assertIn('addEventListener("concierge-widget:open"', CONCIERGE)
        self.assertIn('new CustomEvent("concierge-widget:open")', SIDEBAR)

    def test_the_concierge_button_is_admin_only(self):
        """Das Widget selbst blendet sich fuer Nicht-Admins aus — aber ein Knopf,
        der fuer alle sichtbar ist und bei den meisten nichts tut, sieht aus wie
        ein Fehler."""
        before = SIDEBAR.split('new CustomEvent("concierge-widget:open")', 1)[0]
        self.assertIn("isAdmin && (", before[-600:])


class TheButtonsReadAsAPairTests(unittest.TestCase):
    """Zwei Nachbarn im selben Stil: gleiche Groesse, Lucide-Icons, benannt."""

    def _header_buttons(self) -> str:
        """Der Sidebar-Kopf zwischen Concierge- und Ende des Feedback-Knopfs."""
        start = SIDEBAR.index('new CustomEvent("concierge-widget:open")')
        end = SIDEBAR.index("MessageSquarePlus className", start)
        return SIDEBAR[start - 600 : end + 200]

    def test_both_use_lucide_icons(self):
        block = self._header_buttons()
        self.assertIn("<LifeBuoy", block)
        self.assertIn("<MessageSquarePlus", block)

    def test_both_share_the_same_size_and_style(self):
        self.assertEqual(self._header_buttons().count("h-7 w-7"), 2)

    def test_both_have_accessible_names(self):
        block = self._header_buttons()
        self.assertIn('aria-label="Concierge öffnen"', block)
        self.assertIn('aria-label="Feedback geben"', block)


if __name__ == "__main__":
    unittest.main()
