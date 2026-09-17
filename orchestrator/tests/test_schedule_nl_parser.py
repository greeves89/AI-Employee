"""Issue #196: natural-language -> cron_expression translation.

The scheduling tools already accept a raw cron_expression; what's covered
here is the missing piece — turning "jeden Montag um 9 Uhr" into one, for
the human typing free text into the web UI (an LLM-driven agent already does
this translation itself as part of normal tool-call reasoning, so this is
specifically for the non-LLM caller: the create-schedule form).
"""
import unittest
from unittest.mock import AsyncMock, patch

from app.services.schedule_nl_parser import (
    parse_deterministic,
    parse_natural_language_schedule,
)


class ParseDeterministicWeekdayTests(unittest.TestCase):
    def test_every_monday_with_time_german(self):
        self.assertEqual(parse_deterministic("jeden Montag um 9 Uhr"), "0 9 * * 1")

    def test_every_monday_with_time_english(self):
        self.assertEqual(parse_deterministic("every Monday at 9am"), "0 9 * * 1")

    def test_weekday_without_explicit_time_defaults_to_nine(self):
        self.assertEqual(parse_deterministic("jeden Freitag"), "0 9 * * 5")

    def test_colon_time_format(self):
        self.assertEqual(parse_deterministic("jeden Mittwoch um 14:30"), "30 14 * * 3")

    def test_sunday_maps_to_zero(self):
        self.assertEqual(parse_deterministic("every Sunday at 6am"), "0 6 * * 0")

    def test_original_issue_example_ignores_the_task_text(self):
        # "melde dich jeden Montag mit dem Stand der offenen Tickets" — der
        # Zeitanteil zaehlt, der Rest ist Sache des separaten Prompt-Felds.
        self.assertEqual(
            parse_deterministic("melde dich jeden Montag mit dem Stand der offenen Tickets"),
            "0 9 * * 1",
        )


class ParseDeterministicFrequencyTests(unittest.TestCase):
    def test_daily_german(self):
        self.assertEqual(parse_deterministic("täglich um 8 Uhr"), "0 8 * * *")

    def test_daily_english(self):
        self.assertEqual(parse_deterministic("every day at 8:15"), "15 8 * * *")

    def test_weekdays_only_german(self):
        self.assertEqual(parse_deterministic("werktags um 7 Uhr"), "0 7 * * 1-5")

    def test_weekdays_only_english(self):
        self.assertEqual(parse_deterministic("every weekday at 7am"), "0 7 * * 1-5")

    def test_hourly(self):
        self.assertEqual(parse_deterministic("stündlich"), "0 * * * *")
        self.assertEqual(parse_deterministic("every hour"), "0 * * * *")

    def test_monthly_on_a_specific_day(self):
        self.assertEqual(parse_deterministic("monatlich am 1. um 9 Uhr"), "0 9 1 * *")

    def test_monthly_without_a_day_defaults_to_the_first(self):
        self.assertEqual(parse_deterministic("jeden monat um 10 Uhr"), "0 10 1 * *")


class ParseDeterministicTimeFormatTests(unittest.TestCase):
    def test_pm_rolls_over_to_24h(self):
        self.assertEqual(parse_deterministic("täglich um 3pm"), "0 15 * * *")

    def test_twelve_pm_is_noon(self):
        self.assertEqual(parse_deterministic("täglich um 12pm"), "0 12 * * *")

    def test_twelve_am_is_midnight(self):
        self.assertEqual(parse_deterministic("täglich um 12am"), "0 0 * * *")

    def test_a_bare_number_without_a_time_marker_is_not_read_as_a_time(self):
        # "3" allein (kein "um"/"uhr"/"am"/"pm"/":..") ist zu unsicher als
        # Uhrzeit-Treffer -- der Standard (9 Uhr) muss greifen, nicht "3 Uhr".
        self.assertEqual(parse_deterministic("täglich 3 mal"), "0 9 * * *")


class ParseDeterministicUnmatchedTests(unittest.TestCase):
    def test_unrecognized_phrasing_returns_none(self):
        self.assertIsNone(parse_deterministic("wenn die Sonne scheint"))

    def test_empty_string_returns_none(self):
        self.assertIsNone(parse_deterministic(""))


class ParseNaturalLanguageScheduleTests(unittest.IsolatedAsyncioTestCase):
    async def test_deterministic_hit_skips_the_llm_entirely(self):
        with patch("app.services.schedule_nl_parser._llm_parse", AsyncMock()) as llm:
            result = await parse_natural_language_schedule("jeden Montag um 9 Uhr")
        llm.assert_not_awaited()
        self.assertEqual(result["cron_expression"], "0 9 * * 1")
        self.assertEqual(result["source"], "regel")
        self.assertIn("Montag", result["explanation"])

    async def test_falls_back_to_the_llm_when_no_rule_matches(self):
        with patch("app.services.schedule_nl_parser._llm_parse",
                    AsyncMock(return_value="0 6 * * 2,4")) as llm:
            result = await parse_natural_language_schedule("dienstags und donnerstags morgens früh")
        llm.assert_awaited_once()
        self.assertEqual(result["cron_expression"], "0 6 * * 2,4")
        self.assertEqual(result["source"], "llm")

    async def test_neither_tier_resolving_raises_value_error(self):
        with patch("app.services.schedule_nl_parser._llm_parse", AsyncMock(return_value=None)):
            with self.assertRaises(ValueError):
                await parse_natural_language_schedule("wenn die Sonne scheint")

    async def test_empty_text_raises_without_calling_the_llm(self):
        with patch("app.services.schedule_nl_parser._llm_parse", AsyncMock()) as llm:
            with self.assertRaises(ValueError):
                await parse_natural_language_schedule("   ")
        llm.assert_not_awaited()


class LlmParseTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_api_key_configured_returns_none_without_raising(self):
        from app.services.schedule_nl_parser import _llm_parse
        with patch("app.config.settings") as settings:
            settings.anthropic_api_key = ""
            result = await _llm_parse("irgendein text")
        self.assertIsNone(result)

    async def test_llm_returning_unbekannt_returns_none(self):
        from app.services.schedule_nl_parser import _llm_parse

        class _FakeResponse:
            status_code = 200
            def json(self):
                return {"content": [{"type": "text", "text": "UNBEKANNT"}]}

        class _FakeClient:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *a):
                return False
            async def post(self, *a, **kw):
                return _FakeResponse()

        with patch("app.config.settings") as settings, \
             patch("httpx.AsyncClient", return_value=_FakeClient()):
            settings.anthropic_api_key = "sk-test"
            result = await _llm_parse("irgendein text")
        self.assertIsNone(result)

    async def test_llm_returning_an_invalid_cron_is_rejected(self):
        from app.services.schedule_nl_parser import _llm_parse

        class _FakeResponse:
            status_code = 200
            def json(self):
                return {"content": [{"type": "text", "text": "not a cron"}]}

        class _FakeClient:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *a):
                return False
            async def post(self, *a, **kw):
                return _FakeResponse()

        with patch("app.config.settings") as settings, \
             patch("httpx.AsyncClient", return_value=_FakeClient()):
            settings.anthropic_api_key = "sk-test"
            result = await _llm_parse("irgendein text")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
