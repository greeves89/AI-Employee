"""Frontmatter-Parsing im Skill-Crawler (#371) — echtes YAML statt Zeile-fuer-Zeile.

Ein SKILL.md aus einer fremden, unkontrollierten Quelle darf mit einem YAML-
Block-Skalar ("description: |-", gefolgt von eingerueckten Zeilen) nicht dazu
fuehren, dass genau dieses "|-" als Beschreibung im Marktplatz auftaucht — das
war live sichtbar, weil der alte Parser jede Zeile isoliert am ersten ":"
aufgeteilt hat, statt die Folgezeilen als Fortsetzung zu erkennen.
"""

import unittest

from app.services.skill_crawler import _parse_frontmatter


class SkillCrawlerFrontmatterTest(unittest.TestCase):
    def test_plain_string_values_are_parsed(self):
        content = '---\nname: my-skill\ndescription: Does one thing well\n---\nBody text.'
        fm = _parse_frontmatter(content)
        self.assertEqual(fm["name"], "my-skill")
        self.assertEqual(fm["description"], "Does one thing well")

    def test_block_scalar_description_is_resolved_not_left_as_literal_marker(self):
        content = (
            "---\n"
            "name: claude-api\n"
            "description: |-\n"
            "  Line one of the description.\n"
            "  Line two continues it.\n"
            "---\n"
            "Body text.\n"
        )
        fm = _parse_frontmatter(content)
        self.assertNotEqual(fm["description"], "|-")
        self.assertIn("Line one of the description.", fm["description"])
        self.assertIn("Line two continues it.", fm["description"])

    def test_malformed_yaml_falls_back_to_no_frontmatter(self):
        content = "---\nname: [unterminated\n---\nBody text.\n"
        fm = _parse_frontmatter(content)
        self.assertEqual(fm, {})

    def test_non_mapping_frontmatter_falls_back_to_no_frontmatter(self):
        content = "---\n- just\n- a\n- list\n---\nBody text.\n"
        fm = _parse_frontmatter(content)
        self.assertEqual(fm, {})

    def test_missing_frontmatter_block_returns_empty(self):
        fm = _parse_frontmatter("No frontmatter here, just body text.")
        self.assertEqual(fm, {})

    def test_non_string_scalar_values_are_stringified(self):
        content = "---\nname: 42\ndescription: true\n---\nBody.\n"
        fm = _parse_frontmatter(content)
        self.assertEqual(fm["name"], "42")
        self.assertEqual(fm["description"], "True")

    def test_nested_mapping_or_list_values_are_dropped_not_stringified(self):
        content = "---\nname: my-skill\ntools:\n  - a\n  - b\n---\nBody.\n"
        fm = _parse_frontmatter(content)
        self.assertEqual(fm["name"], "my-skill")
        self.assertNotIn("tools", fm)


if __name__ == "__main__":
    unittest.main()
