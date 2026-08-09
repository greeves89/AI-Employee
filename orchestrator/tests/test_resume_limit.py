"""Eine Fortsetzung darf sich nicht endlos selbst fortsetzen.

Wird ein laufender Agenten-Task von einem Neustart unterbrochen, nimmt die Plattform
ihn als neue Aufgabe wieder auf (#282). Die Fortsetzung kann aber selbst unterbrochen
werden — und ihre Fortsetzung wieder. Jeder Anlauf faengt bei null an und kostet voll.

Real passiert am 2026-08-07: waehrend mehrerer Deployments hintereinander lief EIN
Plan-Block fuenfmal komplett durch (16:33 → 16:37 → 16:42 → 16:45 → 16:51), rund 14 USD
statt knapp vier. Niemand hat es gemerkt, weil jede Fortsetzung wie ein normaler Lauf
aussah.
"""

import unittest
from pathlib import Path

MAIN = (Path(__file__).resolve().parents[1] / "app/main.py").read_text()
RESUME = MAIN.split("async def _resume_agent_task", 1)[1].split("\n    try:", 1)[0]


class ResumeLimitTests(unittest.TestCase):
    def test_there_is_a_limit_at_all(self):
        self.assertIn("_MAX_RESUMES = 3", MAIN)
        self.assertIn("if resume_count > _MAX_RESUMES:", RESUME)

    def test_the_counter_is_carried_forward(self):
        """Ohne Weitergabe faengt jede Fortsetzung wieder bei eins an — die Bremse
        griffe nie."""
        self.assertIn('.get("resume_count") or 0) + 1', RESUME)
        self.assertIn('"resume_count": resume_count', RESUME)

    def test_the_owner_is_told_instead_of_silently_giving_up(self):
        self.assertIn("Aufgabe bricht immer wieder ab", RESUME)
        self.assertIn('priority="high"', RESUME)
        self.assertIn('"reason": "resume_limit"', RESUME)

    def test_the_job_is_dropped_so_it_cannot_come_back(self):
        stop = RESUME.split("if resume_count > _MAX_RESUMES:", 1)[1].split("# Retire", 1)[0]
        self.assertIn("delete_job(db, job.id)", stop)

    def test_a_completed_original_is_still_skipped_first(self):
        """Die bestehende Abkuerzung darf nicht verloren gehen: was fertig ist, wird
        gar nicht erst fortgesetzt."""
        self.assertIn("already completed — skipping", RESUME)

    def test_the_calendar_shows_a_continuation_as_such(self):
        api = (Path(__file__).resolve().parents[1] / "app/api/activity.py").read_text()
        self.assertIn('"resumed": bool((t.metadata_ or {}).get("resumed_from_task"))', api)


if __name__ == "__main__":
    unittest.main()
