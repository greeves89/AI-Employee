"""Dry-Run / Simulation (#386) — Task model helper properties."""
import unittest
from app.models.task import Task


class DryRunPropertyTests(unittest.TestCase):
    def test_defaults_false_when_no_metadata(self):
        t = Task(id="t1", title="x", prompt="p")
        t.metadata_ = None
        self.assertFalse(t.dry_run)
        self.assertIsNone(t.original_prompt)

    def test_reads_from_metadata(self):
        t = Task(id="t2", title="x", prompt="wrapped")
        t.metadata_ = {"dry_run": True, "original_prompt": "die echte Aufgabe"}
        self.assertTrue(t.dry_run)
        self.assertEqual(t.original_prompt, "die echte Aufgabe")

    def test_non_dry_run_metadata(self):
        t = Task(id="t3", title="x", prompt="p")
        t.metadata_ = {"source": "meeting"}
        self.assertFalse(t.dry_run)
        self.assertIsNone(t.original_prompt)


if __name__ == "__main__":
    unittest.main()
