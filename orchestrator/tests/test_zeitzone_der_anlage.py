"""Ohne eigene Zeitzone rechnet ein Agent in der Zeitzone der Anlage, nicht in UTC.

27.09.2026: „schick mir jeden Tag um 6 Uhr einen Newsletter". Der Agent legte
``0 6 * * *`` an und bestaetigte „06:00" — gerechnet wurde in UTC, weil er keine
eigene Zeitzone hatte. Der Lauf waere um 8 Uhr Ortszeit gekommen. Die Anlage
kennt ihre Zeitzone (``TZ`` im Compose, standardmaessig Europe/Berlin).
"""
import os
import unittest
from unittest.mock import patch

from app.core import agent_duty
from app.core.plan_rhythm import timezone_name


class ZeitzoneTests(unittest.TestCase):
    def test_ohne_angabe_gilt_die_anlage(self):
        with patch.dict(os.environ, {"TZ": "Europe/Berlin"}):
            self.assertEqual(timezone_name({}), "Europe/Berlin")
            self.assertEqual(timezone_name(None), "Europe/Berlin")

    def test_eigene_angabe_geht_vor(self):
        with patch.dict(os.environ, {"TZ": "Europe/Berlin"}):
            cfg = {"working_hours": {"timezone": "America/New_York"}}
            self.assertEqual(timezone_name(cfg), "America/New_York")

    def test_ohne_tz_bleibt_utc(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(timezone_name({}), "UTC")

    def test_dienstzeit_ohne_zeitzone(self):
        """Dieselbe Luecke in der Dienstzeit-Pruefung (agent_duty)."""
        with patch.dict(os.environ, {"TZ": "Europe/Berlin"}):
            self.assertEqual(agent_duty.anlagen_zeitzone(), "Europe/Berlin")


if __name__ == "__main__":
    unittest.main()
