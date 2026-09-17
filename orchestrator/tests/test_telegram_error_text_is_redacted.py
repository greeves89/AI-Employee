"""CWE-532, reported responsibly 2026-09-17: raw exception text must never
reach a Telegram log line or chat message unredacted.

``agent_bot.py`` had two separate leak shapes, both fixed alongside the
primary log_redaction.py regex fix:
1. Bare ``print(f"...: {e}")`` calls that bypass the logging module (and
   therefore every redacting handler) entirely — ``bot_manager.py`` already
   wrapped its equivalent lines in ``redact_logs(...)``, this file just
   hadn't been brought in line.
2. ``update.message.reply_text(f"Fehler: {e}")`` /
   ``query.answer(f"Fehler: {e}")`` — a DIFFERENT leak path than logs: if the
   underlying python-telegram-bot/httpx exception ever embeds a request URL
   (the same shape the log leak was about), the raw text goes straight back
   to whichever end user triggered the error, in the chat itself.

Source-scan guard rather than an execution test: fully exercising these
handlers needs a real python-telegram-bot Application/Update/CallbackQuery,
which is heavy to construct for a narrow regression check; the invariant
here is purely textual ("every {e} interpolation site is wrapped"), so a
scan proves it directly.
"""
import re
import unittest
from pathlib import Path

SRC = (Path(__file__).resolve().parents[1] / "app/telegram/agent_bot.py").read_text()

# Sites that interpolate an exception into user- or log-facing text. Each
# entry is the call REGARDLESS of redact_logs(...) wrapping, so a match here
# combined with an unwrapped exact-string check below proves regression.
_INTERPOLATION_SITE = re.compile(
    r'(print|reply_text|query\.answer)\(\s*(redact_logs\()?f"[^"]*\{e\}[^"]*"\)?\s*\)'
)


class TelegramExceptionTextIsRedactedTests(unittest.TestCase):
    def test_every_exception_interpolation_site_is_wrapped_in_redact_logs(self):
        sites = _INTERPOLATION_SITE.findall(SRC)
        self.assertTrue(sites, "kein Fundort gefunden — Regex an den Quelltext angepasst?")
        unwrapped = [call for call, wrapped in sites if not wrapped]
        self.assertEqual(
            unwrapped, [],
            f"Diese Aufrufe interpolieren eine Exception OHNE redact_logs(...): {unwrapped}",
        )

    def test_redact_logs_is_imported(self):
        self.assertIn("from app.core.log_redaction import redact_logs", SRC)

    def test_the_known_five_call_sites_are_all_present_and_wrapped(self):
        # Ein Rueckgang der Anzahl waere kein Fehler (weniger Stellen), ein
        # NEU dazugekommener, unverpackter Aufruf schon -- die erste Pruefung
        # oben faengt das generisch ab; diese haelt zusaetzlich fest, dass die
        # urspruenglich fuenf bekannten Stellen wirklich verpackt sind.
        expected = [
            'print(redact_logs(f"[Telegram] send_telegram delivery failed: {e}"))',
            'print(redact_logs(f"[Telegram] telegram:send listener error: {e}"))',
            'print(redact_logs(f"[Telegram] send_telegram → chat {cid} failed: {e}"))',
            'update.message.reply_text(redact_logs(f"Fehler: {e}"))',
            'update.message.reply_text(redact_logs(f"Fehler beim Senden: {e}"))',
            'query.answer(redact_logs(f"Fehler: {e}"))',
        ]
        for snippet in expected:
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, SRC)


if __name__ == "__main__":
    unittest.main()
