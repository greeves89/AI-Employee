"""Kontaktformular der Landingpage (#Landing): oeffentlich, aber nicht naiv.

Der Endpunkt ist ohne Anmeldung erreichbar — Honeypot, IP-Drossel und feste
Empfaengeradresse muessen deshalb halten, und ohne SMTP-Konfiguration darf
er nichts anderes tun als hoeflich ablehnen.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.api.contact import ContactRequest, submit_contact
from app.config import settings


def _request(redis_client=None, ip="203.0.113.7"):
    app = SimpleNamespace(state=SimpleNamespace(redis=SimpleNamespace(client=redis_client)))
    return SimpleNamespace(app=app, client=SimpleNamespace(host=ip))


class _Redis:
    def __init__(self):
        self.values = {}

    async def incr(self, key):
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    async def expire(self, key, seconds):
        return True


def _body(**kw):
    base = dict(name="Grevvy", email="grevvy@example.com",
                message="Ich haette gern Zugang zur Beta, bitte.", website="")
    base.update(kw)
    return ContactRequest(**base)


class ContactFormTests(unittest.IsolatedAsyncioTestCase):
    async def test_honeypot_pretends_success_and_sends_nothing(self):
        with patch("app.api.contact._send_mail") as send:
            out = await submit_contact(_body(website="http://spam.example"), _request())
        self.assertEqual(out, {"status": "ok"})
        send.assert_not_called()

    async def test_unconfigured_installation_says_so(self):
        with patch.object(settings, "contact_smtp_user", ""):
            with self.assertRaises(HTTPException) as ctx:
                await submit_contact(_body(), _request())
        self.assertEqual(ctx.exception.status_code, 503)

    async def test_invalid_email_is_rejected_before_anything_else(self):
        with self.assertRaises(HTTPException) as ctx:
            await submit_contact(_body(email="keine-adresse"), _request())
        self.assertEqual(ctx.exception.status_code, 422)

    async def test_sixth_message_in_an_hour_hits_the_throttle(self):
        redis = _Redis()
        with patch.object(settings, "contact_smtp_user", "u"), \
             patch.object(settings, "contact_smtp_password", "p"), \
             patch.object(settings, "contact_to", "an@example.com"), \
             patch("app.api.contact._send_mail"):
            for _ in range(5):
                out = await submit_contact(_body(), _request(redis))
                self.assertEqual(out, {"status": "sent"})
            with self.assertRaises(HTTPException) as ctx:
                await submit_contact(_body(), _request(redis))
        self.assertEqual(ctx.exception.status_code, 429)

    async def test_smtp_failure_becomes_a_clean_502(self):
        with patch.object(settings, "contact_smtp_user", "u"), \
             patch.object(settings, "contact_smtp_password", "p"), \
             patch.object(settings, "contact_to", "an@example.com"), \
             patch("app.api.contact._send_mail", side_effect=RuntimeError("smtp kaputt")):
            with self.assertRaises(HTTPException) as ctx:
                await submit_contact(_body(), _request())
        self.assertEqual(ctx.exception.status_code, 502)

    async def test_mail_carries_reply_to_and_fixed_recipient(self):
        captured = {}
        def fake_send(body):
            captured["body"] = body
        with patch.object(settings, "contact_smtp_user", "u"), \
             patch.object(settings, "contact_smtp_password", "p"), \
             patch.object(settings, "contact_to", "an@example.com"), \
             patch("app.api.contact._send_mail", side_effect=fake_send):
            out = await submit_contact(_body(), _request())
        self.assertEqual(out, {"status": "sent"})
        self.assertEqual(captured["body"].email, "grevvy@example.com")


if __name__ == "__main__":
    unittest.main()
