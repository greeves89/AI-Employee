"""Anzeigewährung und unbefristete Freigaben.

Zwei Kundenwünsche, ein gemeinsamer Nenner: beides sind Zahlen bzw. Fristen, die
lautlos falsch sein können. Ein Kurs von 0 macht jeden Betrag zu „0,00 €", ohne
dass irgendwo etwas kaputtgeht — deshalb wird er an der Grenze abgewiesen und
nicht zurechtgebogen.

Der Float-Test hängt daran: aus der Einstellungs-Ablage kommt IMMER Text. Bools
und Ints wurden umgewandelt, Fliesskommazahlen nicht — der Kurs wäre als
Zeichenkette in der Konfiguration gelandet und hätte beim Multiplizieren die
Zeichenkette vervielfacht statt zu rechnen.
"""

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.api.apps_overview import MAX_PUBLIC_SHARE_DAYS


class RateValidationTests(unittest.IsolatedAsyncioTestCase):
    """Der Kurs darf nur in einem Bereich landen, in dem er Sinn ergibt."""

    async def _patch(self, **fields):
        from app.api.settings import update_settings
        from app.schemas.settings import SettingsUpdate

        data = SettingsUpdate(**fields)
        with patch("app.api.settings.SettingsService") as svc:
            svc.return_value.set = AsyncMock()
            return await update_settings(data, user=SimpleNamespace(id="u1"), db=AsyncMock())

    async def test_zero_is_refused(self):
        """Sonst zeigt die ganze Oberfläche 0,00 € — und niemand merkt es."""
        with self.assertRaises(HTTPException) as cm:
            await self._patch(usd_eur_rate=0)
        self.assertEqual(cm.exception.status_code, 422)

    async def test_negative_is_refused(self):
        with self.assertRaises(HTTPException):
            await self._patch(usd_eur_rate=-1)

    async def test_absurdly_high_is_refused(self):
        with self.assertRaises(HTTPException):
            await self._patch(usd_eur_rate=1000)

    async def test_a_plausible_rate_passes(self):
        await self._patch(usd_eur_rate=0.92)

    async def test_the_edges_are_inclusive(self):
        await self._patch(usd_eur_rate=0.01)
        await self._patch(usd_eur_rate=100)

    async def test_only_known_currencies(self):
        with self.assertRaises(HTTPException):
            await self._patch(display_currency="BTC")
        await self._patch(display_currency="EUR")
        await self._patch(display_currency="USD")

    async def test_leaving_it_out_changes_nothing(self):
        """Ein PATCH ohne Währungsfelder darf nicht an der Prüfung scheitern."""
        await self._patch(max_turns=50)


class FloatSettingRoundTripTests(unittest.IsolatedAsyncioTestCase):
    """Aus der Ablage kommt Text — eine Zahl muss wieder eine Zahl werden."""

    async def _load(self, stored: dict):
        from app.services.settings_service import SettingsService

        svc = SettingsService(AsyncMock())
        with patch.object(SettingsService, "get_all", new=AsyncMock(return_value=stored)):
            await svc.load_into_config()

    async def test_a_float_survives_the_round_trip(self):
        from app.config import settings

        before = settings.usd_eur_rate
        try:
            await self._load({"usd_eur_rate": "0.87"})
            self.assertIsInstance(settings.usd_eur_rate, float)
            self.assertAlmostEqual(settings.usd_eur_rate, 0.87)
        finally:
            settings.usd_eur_rate = before

    async def test_garbage_leaves_the_default_standing(self):
        """Lieber der Vorgabewert als eine Zeichenkette, die erst beim Rechnen
        auffällt — dann weit weg von hier."""
        from app.config import settings

        before = settings.usd_eur_rate
        try:
            await self._load({"usd_eur_rate": "keine Zahl"})
            self.assertEqual(settings.usd_eur_rate, before)
        finally:
            settings.usd_eur_rate = before

    async def test_a_bool_is_still_a_bool_not_an_int(self):
        """In Python IST ein bool ein int — die Reihenfolge der Prüfung entscheidet."""
        from app.config import settings

        before = settings.registration_open
        try:
            await self._load({"registration_open": "false"})
            self.assertIs(settings.registration_open, False)
        finally:
            settings.registration_open = before


class PublicShareValidityTests(unittest.TestCase):
    """0 Tage = unbefristet. Ausdrücklich gewollt, deshalb festgehalten."""

    def _expiry(self, days):
        """Dieselbe Rechnung wie in ``create_app_share`` — die Fallunterscheidung
        ist das Verhalten, das hier geschützt wird."""
        d = 7 if days is None else days
        if d and (d < 1 or d > MAX_PUBLIC_SHARE_DAYS):
            raise ValueError("out of range")
        return datetime.now(timezone.utc) + timedelta(days=d) if d else None

    def test_zero_means_no_expiry(self):
        self.assertIsNone(self._expiry(0))

    def test_omitted_still_defaults_to_a_week(self):
        """Unbefristet ist die Ausnahme, nicht die Vorgabe."""
        self.assertIsNotNone(self._expiry(None))

    def test_the_upper_bound_still_holds(self):
        with self.assertRaises(ValueError):
            self._expiry(MAX_PUBLIC_SHARE_DAYS + 1)

    def test_negative_is_not_a_sneaky_infinity(self):
        with self.assertRaises(ValueError):
            self._expiry(-1)

    def test_an_unexpired_share_without_a_date_stays_valid(self):
        from app.models.app_share import AppShare

        s = AppShare(id="aps_x", project="p", agent_id="a", scope="public",
                     expires_at=None, created_at=datetime.now(timezone.utc))
        self.assertFalse(s.is_expired(datetime.now(timezone.utc) + timedelta(days=3650)))


if __name__ == "__main__":
    unittest.main()
