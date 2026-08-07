"""Abends planen, morgens nachschaerfen — fuer JEDEN Agenten, nicht nur fuer den einen.

Ein Agent hatte den Rhythmus, weil er ihn sich im Chat selbst eingerichtet hatte. Der
Montag der anderen blieb leer, weil sonntags niemand plante. Hier wird geprueft, dass
der Rhythmus aus dem Code kommt, sich an der Dienstzeit des Agenten orientiert, am
Wochenende NICHT aussetzt und in allen Wegen dieselbe Planungsanweisung benutzt.
"""

import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from app.core import plan_rhythm as rhythm

REPO = Path(__file__).resolve().parents[2]
ORCH = REPO / "orchestrator"
UTC = timezone.utc


def _agent(**config):
    return SimpleNamespace(id="a1", name="Testi", user_id="u1", state="running", config=config)


class TimesTests(unittest.TestCase):
    def test_without_working_hours_the_defaults_apply(self):
        t = rhythm.rhythm_times(_agent())
        self.assertEqual(t["evening"], rhythm.DEFAULT_EVENING)
        self.assertEqual(t["morning"], rhythm.DEFAULT_MORNING)

    def test_times_follow_his_own_duty_hours(self):
        """Wer bis 17:00 Dienst hat, plant um 16:30 — nicht um halb zehn abends."""
        t = rhythm.rhythm_times(_agent(working_hours={"start": "09:00", "end": "17:00",
                                                      "timezone": "Europe/Berlin"}))
        self.assertEqual(t["evening"], "16:30")
        self.assertEqual(t["morning"], "09:00")
        self.assertEqual(t["timezone"], "Europe/Berlin")

    def test_night_shift_falls_back_instead_of_planning_nonsense(self):
        t = rhythm.rhythm_times(_agent(working_hours={"start": "22:00", "end": "06:00"}))
        self.assertEqual(t["evening"], rhythm.DEFAULT_EVENING)
        self.assertEqual(t["morning"], rhythm.DEFAULT_MORNING)

    def test_contact_timezone_wins_over_duty_timezone(self):
        a = _agent(
            working_hours={"start": "08:00", "end": "17:00", "timezone": "UTC"},
            proactive={"contact_hours": {"timezone": "Europe/Berlin"}},
        )
        self.assertEqual(rhythm.timezone_name(a.config), "Europe/Berlin")

    def test_broken_timezone_does_not_kill_the_run(self):
        self.assertIsNotNone(rhythm.tzinfo({"working_hours": {"timezone": "Mars/Olympus"}}))


class WeekendTests(unittest.TestCase):
    def test_seven_days_by_default(self):
        """Der Nutzer war ausdruecklich: der soll auch am Wochenende was machen."""
        crons = rhythm.cron_expressions(_agent())
        self.assertTrue(crons["evening"].endswith(" * * *"), crons["evening"])
        self.assertTrue(crons["morning"].endswith(" * * *"), crons["morning"])

    def test_weekdays_only_is_respected_when_the_user_set_it(self):
        crons = rhythm.cron_expressions(_agent(
            working_hours={"start": "08:00", "end": "17:00", "weekdays_only": True}
        ))
        self.assertTrue(crons["evening"].endswith("1-5"), crons["evening"])

    def test_cron_matches_the_configured_times(self):
        crons = rhythm.cron_expressions(_agent(
            working_hours={"start": "09:00", "end": "17:00"}
        ))
        self.assertEqual(crons["evening"], "30 16 * * *")
        self.assertEqual(crons["morning"], "0 9 * * *")


class ManualMorningTests(unittest.TestCase):
    """„Tagesplanung am Morgen" gab es vorher — sie darf keinen ZWEITEN Lauf erzeugen."""

    def test_manual_time_steers_the_morning_check(self):
        a = _agent(proactive={"morning_planning": {"time": "06:15"}})
        self.assertEqual(rhythm.rhythm_times(a)["morning"], "06:15")

    def test_manual_time_beats_the_duty_start(self):
        a = _agent(
            working_hours={"start": "09:00", "end": "17:00"},
            proactive={"morning_planning": {"time": "06:15"}},
        )
        times = rhythm.rhythm_times(a)
        self.assertEqual(times["morning"], "06:15")
        self.assertEqual(times["evening"], "16:30")

    def test_manual_weekdays_only_is_respected(self):
        a = _agent(proactive={"morning_planning": {"time": "06:15", "weekdays_only": True}})
        self.assertTrue(rhythm.cron_expressions(a)["evening"].endswith("1-5"))

    def test_the_old_separate_schedule_is_retired(self):
        api = (ORCH / "app/api/agents.py").read_text()
        self.assertNotIn('name=f"[Proactive] {agent.name} — Tagesplanung"', api)
        sched = (ORCH / "app/services/scheduler_service.py").read_text()
        self.assertIn('Schedule.name.like("%— Tagesplanung")', sched)


class PhaseTests(unittest.TestCase):
    def _a(self):
        return _agent(working_hours={"start": "08:00", "end": "17:00", "timezone": "UTC"})

    def test_evening_run_plans_tomorrow(self):
        now = datetime(2026, 8, 7, 16, 40, tzinfo=UTC)
        self.assertEqual(rhythm.phase(self._a(), now), rhythm.EVENING)
        self.assertEqual(rhythm.target_date(self._a(), now), date(2026, 8, 8))

    def test_morning_run_works_on_today(self):
        now = datetime(2026, 8, 7, 8, 10, tzinfo=UTC)
        self.assertEqual(rhythm.phase(self._a(), now), rhythm.MORNING)
        self.assertEqual(rhythm.target_date(self._a(), now), date(2026, 8, 7))

    def test_midday_is_plain_working_time(self):
        self.assertEqual(rhythm.phase(self._a(), datetime(2026, 8, 7, 13, 0, tzinfo=UTC)),
                         rhythm.DAY)

    def test_phase_is_read_in_the_agents_timezone(self):
        a = _agent(working_hours={"start": "08:00", "end": "17:00", "timezone": "Europe/Berlin"})
        # 14:40 UTC = 16:40 Berlin → Feierabendplanung
        self.assertEqual(rhythm.phase(a, datetime(2026, 8, 7, 14, 40, tzinfo=UTC)), rhythm.EVENING)
        self.assertEqual(rhythm.phase(a, datetime(2026, 8, 7, 16, 40, tzinfo=UTC)), rhythm.EVENING)


class PromptTests(unittest.TestCase):
    def test_planning_instruction_demands_time_and_a_minimum(self):
        text = rhythm.planning_instruction(date(2026, 8, 8))
        self.assertIn("planned_start", text)
        self.assertIn("mindestens 15", text)
        self.assertIn("2026-08-08", text)

    def test_evening_prompt_plans_and_does_not_work(self):
        text = rhythm.evening_prompt(_agent(), date(2026, 8, 8))
        self.assertIn("2026-08-08", text)
        self.assertIn("KEINE Aufgaben ab", text)
        self.assertIn("planned_start", text)     # dieselbe Anweisung, nicht neu erfunden

    def test_morning_prompt_carries_the_night(self):
        text = rhythm.morning_prompt(
            _agent(), date(2026, 8, 7),
            [{"title": "Nachtlauf Backup", "status": "failed"}],
        )
        self.assertIn("Nachtlauf Backup", text)
        self.assertIn("GESCHEITERT", text)

    def test_empty_night_is_said_out_loud(self):
        text = rhythm.morning_prompt(_agent(), date(2026, 8, 7), [])
        self.assertIn("nichts gelaufen", text)

    def test_note_names_the_times_and_the_weekend(self):
        note = rhythm.rhythm_note(_agent(), datetime(2026, 8, 7, 12, 0, tzinfo=UTC))
        self.assertIn(rhythm.DEFAULT_EVENING, note)
        self.assertIn("auch am Wochenende", note)

    def test_spoken_note_uses_the_tool_the_voice_actually_has(self):
        note = rhythm.rhythm_note(
            _agent(), datetime(2026, 8, 7, 22, 0, tzinfo=UTC), spoken=True,
        )
        self.assertIn("plan_my_day", note)
        self.assertNotIn("`plan_day`", note)


class WiringTests(unittest.TestCase):
    """Ohne Verdrahtung ist der Rhythmus eine huebsche Datei."""

    @classmethod
    def setUpClass(cls):
        cls.sched = (ORCH / "app/services/scheduler_service.py").read_text()
        cls.voice = (ORCH / "app/services/realtime_voice_session.py").read_text()
        cls.mgr = (ORCH / "app/core/agent_manager.py").read_text()

    def test_scheduler_ensures_the_two_schedules(self):
        self.assertIn("_ensure_planning_rhythm", self.sched)
        self.assertIn("EVENING_SCHEDULE_NAME", self.sched)
        self.assertIn("MORNING_SCHEDULE_NAME", self.sched)

    def test_rhythm_runs_are_built_from_code_like_proactive_runs(self):
        self.assertIn("is_rhythm = schedule.name.startswith(plan_rhythm.SCHEDULE_PREFIX)", self.sched)
        self.assertIn("plan_rhythm.evening_prompt", self.sched)
        self.assertIn("plan_rhythm.morning_prompt", self.sched)

    def test_every_proactive_run_learns_its_phase(self):
        self.assertIn("plan_rhythm.rhythm_note(_agent, now)", self.sched)

    def test_night_results_are_handed_over_not_guessed(self):
        self.assertIn("_night_runs", self.sched)

    def test_voice_uses_the_same_planning_instruction(self):
        self.assertIn("plan_rhythm.planning_instruction", self.voice)
        self.assertIn("plan_rhythm.target_date", self.voice)
        self.assertIn("rhythm_note(agent, spoken=True)", self.voice)

    def test_voice_timezone_has_one_definition(self):
        self.assertIn("plan_rhythm.tzinfo(getattr(self, \"_agent_config\", None))", self.voice)

    def test_shared_instructions_carry_the_rhythm(self):
        self.assertIn("## Dein Arbeitsrhythmus", self.mgr)
        self.assertIn("[Rhythmus] Abendplanung", self.mgr)


class ScheduleDescriptionTests(unittest.TestCase):
    """Der Kalender soll den Takt zeigen, nicht nur eine Uhrzeit."""

    def _sched(self, cron=None, interval=0, name="X", prompt=""):
        return SimpleNamespace(cron_expression=cron, interval_seconds=interval,
                               name=name, prompt=prompt)

    def setUp(self):
        from app.core.plan_rhythm import describe_schedule
        self.describe = describe_schedule

    def test_daily_cron(self):
        self.assertEqual(self.describe(self._sched(cron="0 22 * * *")), "täglich 22:00")

    def test_weekday_cron(self):
        self.assertEqual(self.describe(self._sched(cron="30 7 * * 1-5")), "Mo–Fr 07:30")

    def test_single_weekday(self):
        self.assertEqual(self.describe(self._sched(cron="0 9 * * 1")), "Mo 09:00")

    def test_interval(self):
        self.assertEqual(self.describe(self._sched(interval=1800)), "alle 30 Min")
        self.assertEqual(self.describe(self._sched(interval=7200)), "alle 2 Std")

    def test_one_shot(self):
        self.assertEqual(self.describe(self._sched(interval=0)), "einmalig")

    def test_exotic_cron_is_shown_raw_instead_of_wrong(self):
        self.assertEqual(self.describe(self._sched(cron="*/5 * * * *")), "Cron */5 * * * *")


if __name__ == "__main__":
    unittest.main()


class ScheduleTimezoneTests(unittest.TestCase):
    """Eine Uhrzeit ohne Zone meint die Zone DES AGENTEN — nicht UTC.

    Der Agent nannte seinen Zeitplan „🌅 Täglicher Morgen-Report (07:00)" und trug
    `0 7 * * *` ein. Der Server rechnete in UTC, im Kalender stand 09:00. Der Name log
    also — und im Sommer zwei Stunden daneben ist keine Kleinigkeit, wenn daran ein
    Bericht fuer den Nutzer haengt.
    """

    def test_schema_has_no_utc_default_anymore(self):
        src = (ORCH / "app/schemas/schedule.py").read_text()
        self.assertIn("timezone: str | None = None", src)
        self.assertNotIn('timezone: str = "UTC"', src)

    def test_api_falls_back_to_the_agents_zone(self):
        api = (ORCH / "app/api/schedules.py").read_text()
        self.assertIn("from app.core.plan_rhythm import timezone_name", api)
        self.assertIn('data.timezone = tz_name or "UTC"', api)

    def test_every_harness_says_the_same(self):
        """Harness-Paritaet: das Werkzeug muss ueberall dieselbe Regel nennen."""
        mcp = (REPO / "agent/mcp/orchestrator-server.mjs").read_text()
        self.assertIn("LEAVE EMPTY", mcp)
        defs = (REPO / "agent/app/tools/definitions.py").read_text()
        self.assertIn('"timezone"', defs)
        self.assertIn("LEAVE EMPTY", defs)
        client = (REPO / "agent/app/tools/api_client.py").read_text()
        self.assertIn('body["timezone"] = params["timezone"]', client)

    def test_agents_are_told_not_to_build_their_own_planner(self):
        mgr = (ORCH / "app/core/agent_manager.py").read_text()
        self.assertIn("KEINEN eigenen Morgen- oder Abendplaner", mgr)
