"""Regression test for #572: create_schedule returned 500 for every input.

ScheduleCreate.validate_timing() unconditionally ran ZoneInfo(self.timezone)
via _validate_timezone(). Agents are told (by design, see the tool docstring)
to OMIT timezone unless overriding the server-side agent-timezone default —
so self.timezone was None on virtually every real call. ZoneInfo(None) raises
a plain TypeError, which the validator's except clause (ZoneInfoNotFoundError,
ValueError, KeyError) does not catch, so it escaped as an unhandled 500
instead of a clean 422 — reproduced identically for cron, interval_seconds,
and run_in_seconds inputs.
"""

import unittest

from pydantic import ValidationError

from app.schemas.schedule import ScheduleCreate


class ScheduleCreateNoTimezoneTests(unittest.TestCase):
    def test_cron_without_timezone_does_not_raise(self):
        data = ScheduleCreate(name="Test", prompt="Test.", cron_expression="0 18 * * 5")
        self.assertIsNone(data.timezone)

    def test_interval_without_timezone_does_not_raise(self):
        data = ScheduleCreate(name="Test", prompt="Test.", interval_seconds=604800)
        self.assertIsNone(data.timezone)

    def test_run_in_seconds_without_timezone_does_not_raise(self):
        data = ScheduleCreate(name="Test", prompt="Test.", run_in_seconds=1800)
        self.assertIsNone(data.timezone)

    def test_explicit_valid_timezone_is_kept(self):
        data = ScheduleCreate(
            name="Test", prompt="Test.", cron_expression="0 6 * * *", timezone="Europe/Berlin"
        )
        self.assertEqual(data.timezone, "Europe/Berlin")

    def test_explicit_invalid_timezone_still_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            ScheduleCreate(
                name="Test", prompt="Test.", cron_expression="0 6 * * *", timezone="Mars/Olympus"
            )


if __name__ == "__main__":
    unittest.main()
