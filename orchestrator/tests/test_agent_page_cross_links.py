"""Issue #787: Namenskollisionen und fehlende Querverlinkung zwischen
Oberflaechen, die auf den ersten Blick dieselbe Sache zu sein scheinen, es
aber nicht sind (Telegram-Bot pro Agent vs. pro Konto, "Integrations" an
drei Stellen, "Apps" an zwei Stellen, Skill Store global vs. pro Agent, drei
Agent-Detail-Seiten ohne Verweis aufeinander).

Reiner Quelltext-Scan wie bei den uebrigen Frontend-Vertraegen in diesem
Baum -- kein JS-Test-Runner vorhanden.
"""
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FRONTEND = REPO / "frontend/src"


class TelegramCrossLinkTests(unittest.TestCase):
    def test_per_agent_section_points_at_the_personal_settings(self):
        src = (FRONTEND / "app/agents/[id]/page.tsx").read_text()
        self.assertIn('href="/settings"', src)
        self.assertIn("NUR fuer diesen Agenten", src)

    def test_global_settings_clarifies_it_is_the_personal_bot(self):
        src = (FRONTEND / "app/settings/view.tsx").read_text()
        self.assertIn("Telegram Bot (dein Konto)", src)


class IntegrationsCrossLinkTests(unittest.TestCase):
    def test_per_agent_selector_points_at_the_global_registry(self):
        src = (FRONTEND / "components/agents/integration-selector.tsx").read_text()
        self.assertIn('href="/integrations"', src)

    def test_settings_notifications_tab_points_at_the_global_registry(self):
        src = (FRONTEND / "app/settings/view.tsx").read_text()
        self.assertIn('href="/integrations"', src)


class AppsScopeClarityTests(unittest.TestCase):
    def test_per_agent_apps_tab_says_it_is_scoped_to_this_agent(self):
        src = (FRONTEND / "components/agents/docker-apps-tab.tsx").read_text()
        self.assertIn("nur dieser Agent", src)


class SkillStoreCrossLinkTests(unittest.TestCase):
    def test_per_agent_skills_tab_points_at_the_global_marketplace(self):
        src = (FRONTEND / "components/agents/skills-tab.tsx").read_text()
        self.assertIn('href="/skills"', src)


class AgentDetailPagesCrossLinkTests(unittest.TestCase):
    def test_main_page_links_to_the_admin_view_for_admins_only(self):
        src = (FRONTEND / "app/agents/[id]/page.tsx").read_text()
        self.assertIn('href={`/admin/agents/${agentId}`}', src)
        self.assertIn("isAdminUser", src)

    def test_admin_view_links_back_to_the_full_agent_page(self):
        src = (FRONTEND / "app/admin/agents/[id]/page.tsx").read_text()
        self.assertIn('router.push(`/agents/${agentId}`)', src)


if __name__ == "__main__":
    unittest.main()
