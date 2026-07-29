"""Guard test for issue #358: the background loop services listed below must
open their DB sessions through ``resilient_session`` (connect-retry) instead of
a bare ``async_session_factory``, so a brief DB blip doesn't kill a whole sweep
tick (same failure mode as #356).

This is a source-level guard: it asserts every converted call site routes
through ``resilient_session`` and that no bare ``async_session_factory()`` call
remains, catching any future regression that reintroduces the unprotected
pattern.
"""

import unittest
from pathlib import Path

# Services converted in PR for #358 (were still on bare async_session_factory).
_SERVICES_DIR = Path(__file__).resolve().parent.parent / "app" / "services"
_CONVERTED = [
    "reflection_service",
    "trend_service",
    "knowledge_feed_service",
    "embedding_backfill",
    "improvement_engine",
    "self_test_service",
    "skill_crawler",
]


class TestResilientSessionWiring(unittest.TestCase):
    def _source(self, name: str) -> str:
        return (_SERVICES_DIR / f"{name}.py").read_text()

    def test_services_use_resilient_session(self):
        for name in _CONVERTED:
            src = self._source(name)
            with self.subTest(service=name):
                self.assertIn(
                    "resilient_session", src,
                    f"{name} should open sessions via resilient_session (#358)",
                )

    def test_no_bare_session_factory_call_remains(self):
        for name in _CONVERTED:
            src = self._source(name)
            with self.subTest(service=name):
                self.assertNotIn(
                    "async_session_factory(", src,
                    f"{name} still calls async_session_factory() directly — "
                    f"route it through resilient_session (#358)",
                )


if __name__ == "__main__":
    unittest.main()
