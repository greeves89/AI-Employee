"""Issue #371 phase 1: skill-crawler source repos are configurable via
settings.skill_repos (env SKILL_REPOS) without a code change/release.

_configured_repos() must union the built-in DEFAULT_SKILL_REPOS with any
comma-separated entries from settings, de-duplicated, order-preserving, and
whitespace-tolerant. Empty config must reproduce the built-in list exactly so
existing deployments are unaffected.
"""

import unittest
from unittest.mock import patch

from app.services import skill_crawler
from app.services.skill_crawler import DEFAULT_SKILL_REPOS, _configured_repos


class _Settings:
    def __init__(self, skill_repos=""):
        self.skill_repos = skill_repos


class SkillReposConfigTest(unittest.TestCase):
    def _run(self, skill_repos):
        # _configured_repos does `from app.config import settings` at call time,
        # so patching the attribute on app.config is what takes effect.
        with patch("app.config.settings", _Settings(skill_repos)):
            return _configured_repos()

    def test_empty_config_matches_defaults(self):
        self.assertEqual(self._run(""), list(DEFAULT_SKILL_REPOS))

    def test_none_config_matches_defaults(self):
        self.assertEqual(self._run(None), list(DEFAULT_SKILL_REPOS))

    def test_extra_repo_is_appended(self):
        result = self._run("myorg/internal-skills")
        self.assertEqual(result[: len(DEFAULT_SKILL_REPOS)], list(DEFAULT_SKILL_REPOS))
        self.assertEqual(result[-1], "myorg/internal-skills")

    def test_multiple_and_whitespace(self):
        result = self._run("  myorg/a , myorg/b ,, myorg/c ")
        self.assertEqual(result[-3:], ["myorg/a", "myorg/b", "myorg/c"])

    def test_duplicates_are_dropped(self):
        existing = DEFAULT_SKILL_REPOS[0]
        result = self._run(f"{existing}, myorg/new, myorg/new")
        # built-in duplicate not re-added; second myorg/new not duplicated
        self.assertEqual(result.count(existing), 1)
        self.assertEqual(result.count("myorg/new"), 1)

    def test_backwards_compat_alias(self):
        # Older imports referenced SKILL_REPOS directly; keep it pointing at the defaults.
        self.assertIs(skill_crawler.SKILL_REPOS, DEFAULT_SKILL_REPOS)


if __name__ == "__main__":
    unittest.main()
