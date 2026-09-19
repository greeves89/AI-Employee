"""Issue #787: 7 Admin-Unterfunktionen existierten an je zwei URLs
gleichzeitig (Standalone-Seite + eingebetteter Reiter in der Admin-Konsole,
zwei Mount-Punkte derselben Komponente). Nur 5 der 7 hatten ueberhaupt eine
eigene Standalone-Seite (second-brains und web-search waren schon
Admin-Konsole-only). Von den 5: `/settings` ist NICHT admin-only (andere
Nutzer sehen dort ihre eigenen KI-Zugaenge) und bleibt eine echte Seite; die
uebrigen vier (ai-accounts, secrets, health, audit) leiten jetzt auf den
jeweiligen Admin-Konsole-Reiter um -- schliesst nebenbei eine Luecke: diese
vier Standalone-Seiten hatten KEINE eigene Rollenpruefung, waehrend derselbe
Reiter in der Admin-Konsole laengst admin-only ist.
"""
import unittest
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[2] / "frontend/src/app"


class RedirectingPagesTests(unittest.TestCase):
    REDIRECTS = {
        "ai-accounts": "ai-accounts",
        "secrets": "secrets",
        "health": "health",
        "audit": "audit",
    }

    def test_each_redirects_to_its_admin_tab(self):
        for route, tab in self.REDIRECTS.items():
            with self.subTest(route=route):
                src = (FRONTEND / route / "page.tsx").read_text()
                self.assertIn(f'router.replace("/admin?tab={tab}")', src)

    def test_the_underlying_view_component_is_untouched(self):
        """Nur die Route wird umgeleitet -- die View-Komponente (view.tsx),
        die die Admin-Konsole weiter einbindet, bleibt bestehen."""
        for route in self.REDIRECTS:
            with self.subTest(route=route):
                self.assertTrue((FRONTEND / route / "view.tsx").exists())


class SettingsStaysARealPageTests(unittest.TestCase):
    def test_settings_page_does_not_redirect(self):
        """/settings ist NICHT admin-only (eigene KI-Zugaenge fuer alle
        Nutzer) -- darf nie zur Admin-Konsole umgeleitet werden, sonst
        verlieren normale Nutzer den Zugang zu ihren eigenen Einstellungen."""
        src = (FRONTEND / "settings/page.tsx").read_text()
        self.assertNotIn("router.replace", src)
        self.assertNotIn("/admin?tab=", src)


class DownstreamLinksPointAtTheAdminTabDirectlyTests(unittest.TestCase):
    """Direkte Links auf die vier umgeleiteten Routen wurden auf den
    Admin-Reiter umgebogen -- kein unnoetiger Redirect-Hop."""

    def test_ai_accounts_link_in_agent_page_is_direct(self):
        src = (FRONTEND / "agents/[id]/page.tsx").read_text()
        self.assertIn('href="/admin?tab=ai-accounts"', src)
        self.assertNotIn('href="/ai-accounts"', src)

    def test_secrets_links_in_integration_selector_are_direct(self):
        src = (Path(__file__).resolve().parents[2]
               / "frontend/src/components/agents/integration-selector.tsx").read_text()
        self.assertEqual(src.count('href="/admin?tab=secrets"'), 2)
        self.assertNotIn('href="/secrets"', src)


if __name__ == "__main__":
    unittest.main()
