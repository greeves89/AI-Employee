"""Verantwortungsbereiche — der Dauerauftrag eines Agenten.

Bisher konnte ein Agent nur abarbeiten, was jemand als Todo angelegt hat; er wusste
nicht, wofuer er dauerhaft zustaendig ist, und stand nach der letzten Aufgabe still.
Bereiche sind eine Ebene UEBER den Todos: der proaktive Lauf leitet daraus die Aufgaben
des Tages ab.

Geprueft wird die Validierung (ein Bereich, der still verschluckt wird, waere schlimmer
als keiner) und dass der Lauf sie tatsaechlich zu sehen bekommt.
"""

import unittest

from fastapi import HTTPException

from app.core.responsibilities import (
    MAX_RESPONSIBILITIES,
    RESPONSIBILITY_PRIORITIES,
    RESPONSIBILITY_RHYTHMS,
    validated_responsibilities as _validated_responsibilities,
)
from app.core.responsibilities import responsibilities_note as _responsibilities_note


class ValidationTests(unittest.TestCase):
    def test_defaults_are_filled_in(self):
        (out,) = _validated_responsibilities([{"title": "Posteingang sichten"}])
        self.assertEqual(out["title"], "Posteingang sichten")
        self.assertEqual(out["rhythm"], "daily")     # taeglich ist die sinnvolle Annahme
        self.assertEqual(out["priority"], "normal")
        self.assertEqual(out["notes"], "")

    def test_empty_and_none_yield_empty_list(self):
        self.assertEqual(_validated_responsibilities(None), [])
        self.assertEqual(_validated_responsibilities([]), [])

    def test_title_is_required(self):
        for bad in ([{"title": ""}], [{"title": "   "}], [{}]):
            with self.subTest(bad=bad), self.assertRaises(HTTPException) as cm:
                _validated_responsibilities(bad)
            self.assertEqual(cm.exception.status_code, 422)

    def test_rejects_unknown_rhythm_and_priority(self):
        with self.assertRaises(HTTPException):
            _validated_responsibilities([{"title": "X", "rhythm": "hourly"}])
        with self.assertRaises(HTTPException):
            _validated_responsibilities([{"title": "X", "priority": "urgent"}])

    def test_accepts_every_documented_value(self):
        for rhythm in RESPONSIBILITY_RHYTHMS:
            for priority in RESPONSIBILITY_PRIORITIES:
                (out,) = _validated_responsibilities(
                    [{"title": "X", "rhythm": rhythm, "priority": priority}]
                )
                self.assertEqual((out["rhythm"], out["priority"]), (rhythm, priority))

    def test_rejects_duplicates_case_insensitively(self):
        with self.assertRaises(HTTPException) as cm:
            _validated_responsibilities([{"title": "Postfach"}, {"title": "postfach"}])
        self.assertEqual(cm.exception.status_code, 422)

    def test_caps_the_number_of_duties(self):
        many = [{"title": f"Bereich {i}"} for i in range(MAX_RESPONSIBILITIES + 1)]
        with self.assertRaises(HTTPException):
            _validated_responsibilities(many)
        ok = [{"title": f"Bereich {i}"} for i in range(MAX_RESPONSIBILITIES)]
        self.assertEqual(len(_validated_responsibilities(ok)), MAX_RESPONSIBILITIES)

    def test_notes_are_trimmed_not_rejected(self):
        (out,) = _validated_responsibilities([{"title": "X", "notes": " y " + "z" * 800}])
        self.assertLessEqual(len(out["notes"]), 500)

    def test_non_object_entry_is_refused(self):
        with self.assertRaises(HTTPException):
            _validated_responsibilities(["Posteingang sichten"])


class PromptWiringTests(unittest.TestCase):
    """Der Bereich muss im proaktiven Lauf ankommen — sonst ist er Dekoration."""

    def test_note_lists_every_duty_with_rhythm_and_priority(self):
        note = _responsibilities_note({"responsibilities": [
            {"title": "Posteingang sichten", "rhythm": "daily", "priority": "high", "notes": "vor 9 Uhr"},
            {"title": "Wiki pflegen", "rhythm": "weekly", "priority": "low", "notes": ""},
        ]})
        self.assertIn("Posteingang sichten", note)
        self.assertIn("täglich", note)
        self.assertIn("hoch", note)
        self.assertIn("vor 9 Uhr", note)
        self.assertIn("Wiki pflegen", note)
        self.assertIn("wöchentlich", note)
        # Und die Regel, dass ein Bereich nie fertig wird.
        self.assertIn("nie fertig", note.lower())

    def test_no_duties_means_no_block(self):
        self.assertEqual(_responsibilities_note({}), "")
        self.assertEqual(_responsibilities_note({"responsibilities": []}), "")

    def test_scheduler_appends_the_block_to_the_proactive_prompt(self):
        """Quelltext-Pruefung: der Bereichs-Block haengt am selben Punkt wie die
        Erreichbarkeit — damit kein spaeterer Umbau ihn still fallen laesst."""
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / "app/services/scheduler_service.py").read_text()
        self.assertIn("responsibilities_note(proactive_config)", src)
        self.assertIn("prompt = prompt + \"\\n\\n\" + duties_note", src)

    def test_base_prompt_tells_the_agent_to_plan_from_them(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / "app/core/agent_manager.py").read_text()
        step1 = src.split("## STEP 1: SURVEY AND PLAN THE RUN", 1)[1].split("## STEP 2", 1)[0]
        self.assertIn("Verantwortungsbereiche", step1)
        self.assertIn("update_todos", step1)


if __name__ == "__main__":
    unittest.main()


class MorningPlanningTests(unittest.TestCase):
    """Der feste Planungstermin am Morgen — die Mechanik gab es, nur den Klick nicht."""

    @staticmethod
    def _src() -> str:
        from pathlib import Path
        return (Path(__file__).resolve().parents[1] / "app/api/agents.py").read_text()

    def test_creates_a_proactive_schedule_so_the_base_prompt_applies(self):
        """Der Planungslauf muss '[Proactive]' heissen — sonst behandelt ihn der
        Scheduler als gewoehnlichen Zeitplan und der Basis-Prompt (samt Bereichen
        und Tagesplan) greift nicht."""
        src = self._src()
        self.assertIn('f"[Proactive] {agent.name} — Tagesplanung"', src)

    def test_cron_respects_weekdays_only(self):
        self.assertIn("'1-5' if weekdays_only else '*'", self._src())

    def test_time_format_is_validated(self):
        self.assertIn("Planungszeit muss HH:MM sein", self._src())

    def test_disabling_removes_the_schedule(self):
        """Abgewaehlt heisst weg — nicht 'liegt deaktiviert herum und feuert irgendwann'."""
        self.assertIn("await db.delete(morning_schedule)", self._src())

    def test_follows_the_proactive_switch(self):
        self.assertIn("morning_schedule.enabled = body.enabled", self._src())
