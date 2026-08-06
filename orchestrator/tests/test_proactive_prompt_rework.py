"""Schritt 1 aus HANDOVER.md: PROACTIVE_PROMPT ist auf Entwicklerarbeit
zugeschnitten gewesen ("STEP 1: CHECK GITHUB ISSUES", SCOPE RULES zu
Repo-Besitz) — jeder nicht-technische Agent (Reise, Marketing, ...) las das bei
JEDEM proaktiven Lauf und konnte nichts damit anfangen. Diese Tests halten fest:
GitHub-Spezifisches ist raus aus dem Kern, die Tag-Planungs-Struktur ist drin,
und trigger_*/is_checkin sind tatsaechlich erwaehnt (nicht nur behauptet — siehe
HANDOVER's falsche Annahme, trigger_* sei schon verdrahtet gewesen).
"""

import unittest

from app.core.agent_manager import PROACTIVE_PROMPT


class NoGithubSpecificsInCoreTests(unittest.TestCase):
    def test_no_github_cli_instructions(self):
        for term in ("gh issue list", "gh pr create", "gh repo view", "git checkout -b"):
            self.assertNotIn(term, PROACTIVE_PROMPT, f"GitHub-Spezifisches noch im Kern: {term!r}")

    def test_no_repo_ownership_scope_rules(self):
        self.assertNotIn("SCOPE RULES", PROACTIVE_PROMPT)
        self.assertNotIn("repos YOU own", PROACTIVE_PROMPT)


class DayPlanningStructureTests(unittest.TestCase):
    def test_survey_and_plan_step_exists(self):
        self.assertIn("SURVEY AND PLAN THE RUN", PROACTIVE_PROMPT)

    def test_pulling_forward_finished_early_is_covered(self):
        self.assertIn("pull the next item", PROACTIVE_PROMPT)
        self.assertIn("finish an item faster than expected", PROACTIVE_PROMPT)

    def test_propose_dont_ask_rule_exists(self):
        self.assertIn("PROPOSE, DON'T ASK", PROACTIVE_PROMPT)
        self.assertIn("soll ich", PROACTIVE_PROMPT.lower())

    def test_day_night_rule_exists(self):
        self.assertIn("DAY/NIGHT RULE", PROACTIVE_PROMPT)

    def test_self_organize_step_mentions_the_new_tools(self):
        self.assertIn("SELF-ORGANIZE", PROACTIVE_PROMPT)
        self.assertIn("create_schedule", PROACTIVE_PROMPT)
        self.assertIn("trigger_create", PROACTIVE_PROMPT)
        self.assertIn("trigger_list", PROACTIVE_PROMPT)
        self.assertIn("trigger_toggle", PROACTIVE_PROMPT)
        self.assertIn("trigger_delete", PROACTIVE_PROMPT)

    def test_checkin_cooldown_is_documented(self):
        self.assertIn("is_checkin: true", PROACTIVE_PROMPT)
        self.assertIn("12h", PROACTIVE_PROMPT)

    def test_memory_maintenance_step_survived_the_rewrite(self):
        """War vorher STEP 4 (Review & Update Knowledge) — durfte beim Umbau
        nicht stillschweigend wegfallen."""
        self.assertIn("knowledge.md", PROACTIVE_PROMPT)
        self.assertIn("memory_list", PROACTIVE_PROMPT)


if __name__ == "__main__":
    unittest.main()
