"""Issue reported responsibly 2026-09-17 (CWE-532): secrets in container logs.

Covers the two defense-in-depth pieces added alongside the log_redaction.py
regex fix (see test_log_redaction.py for that):
1. httpx/httpcore silenced to WARNING — their INFO request/response lines
   carried the Telegram Bot API URL (token in the path) straight past the
   redacting formatter's own logger-level gate.
2. uvicorn.access wrapped in the same redacting formatter — it ships with
   propagate=False and its own handler, so a token in a query string
   (webhook bearer fallback, legacy WS ?token=, computer-use bridge token)
   would otherwise never reach ANY redacting handler at all.
"""
import logging
import unittest

from app.core.log_redaction import redact_logs
from app.core.platform_error_log import (
    _RedactingWrapperFormatter,
    _harden_uvicorn_access_logging,
    setup_console_logging,
)


class RedactingWrapperFormatterTests(unittest.TestCase):
    def test_redacts_the_inner_formatters_output(self):
        inner = logging.Formatter("%(message)s")
        wrapper = _RedactingWrapperFormatter(inner)
        record = logging.LogRecord(
            "uvicorn.access", logging.INFO, __file__, 1,
            'GET /callback?token=deadbeefcafebabe0123456789 HTTP/1.1', (), None,
        )
        out = wrapper.format(record)
        self.assertNotIn("deadbeefcafebabe0123456789", out)

    def test_benign_output_survives_unchanged(self):
        inner = logging.Formatter("%(message)s")
        wrapper = _RedactingWrapperFormatter(inner)
        record = logging.LogRecord(
            "uvicorn.access", logging.INFO, __file__, 1,
            'GET /api/v1/health HTTP/1.1" 200', (), None,
        )
        self.assertEqual(wrapper.format(record), inner.format(record))

    def test_delegates_field_handling_to_the_inner_formatter(self):
        # A stand-in for uvicorn's AccessFormatter, which populates custom
        # fields (levelprefix, colours) a plain logging.Formatter can't --
        # the wrapper must not try to reimplement that, only post-process
        # whatever string the inner formatter already produced.
        class _FakeAccessFormatter(logging.Formatter):
            def format(self, record):
                return f"[custom] {record.getMessage()}"

        wrapper = _RedactingWrapperFormatter(_FakeAccessFormatter())
        record = logging.LogRecord(
            "uvicorn.access", logging.INFO, __file__, 1, "hello", (), None,
        )
        self.assertEqual(wrapper.format(record), "[custom] hello")


class HardenUvicornAccessLoggingTests(unittest.TestCase):
    def setUp(self):
        self.access_logger = logging.getLogger("uvicorn.access")
        self._original_handlers = list(self.access_logger.handlers)
        self.access_logger.handlers = []

    def tearDown(self):
        self.access_logger.handlers = self._original_handlers

    def test_wraps_every_existing_handlers_formatter(self):
        inner = logging.Formatter("%(message)s")
        handler = logging.StreamHandler()
        handler.setFormatter(inner)
        self.access_logger.addHandler(handler)

        _harden_uvicorn_access_logging()

        self.assertIsInstance(handler.formatter, _RedactingWrapperFormatter)
        self.assertIs(handler.formatter._inner, inner)

    def test_a_handler_with_no_formatter_yet_is_left_alone(self):
        handler = logging.StreamHandler()
        handler.setFormatter(None)
        self.access_logger.addHandler(handler)

        _harden_uvicorn_access_logging()  # must not raise

        self.assertIsNone(handler.formatter)

    def test_calling_twice_does_not_double_wrap(self):
        inner = logging.Formatter("%(message)s")
        handler = logging.StreamHandler()
        handler.setFormatter(inner)
        self.access_logger.addHandler(handler)

        _harden_uvicorn_access_logging()
        _harden_uvicorn_access_logging()

        self.assertIsInstance(handler.formatter, _RedactingWrapperFormatter)
        self.assertIs(handler.formatter._inner, inner)  # not wrapped-in-a-wrapper


class SetupConsoleLoggingHardeningTests(unittest.TestCase):
    def setUp(self):
        self.root = logging.getLogger()
        self._original_root_handlers = list(self.root.handlers)
        self._original_root_level = self.root.level
        self._original_httpx_level = logging.getLogger("httpx").level
        self._original_httpcore_level = logging.getLogger("httpcore").level
        self.access_logger = logging.getLogger("uvicorn.access")
        self._original_access_handlers = list(self.access_logger.handlers)
        # setup_console_logging is idempotent per-handler-instance, not
        # per-process -- start from a clean slate so it actually runs.
        self.root.handlers = [
            h for h in self.root.handlers if not getattr(h, "_ai_employee_console", False)
        ]

    def tearDown(self):
        self.root.handlers = self._original_root_handlers
        self.root.setLevel(self._original_root_level)
        logging.getLogger("httpx").setLevel(self._original_httpx_level)
        logging.getLogger("httpcore").setLevel(self._original_httpcore_level)
        self.access_logger.handlers = self._original_access_handlers

    def test_httpx_and_httpcore_are_silenced_to_warning(self):
        setup_console_logging(level=logging.INFO)
        self.assertEqual(logging.getLogger("httpx").level, logging.WARNING)
        self.assertEqual(logging.getLogger("httpcore").level, logging.WARNING)

    def test_setup_console_logging_also_wires_up_the_uvicorn_access_hardening(self):
        # Regression guard: a dropped call to _harden_uvicorn_access_logging()
        # inside setup_console_logging would leave this suite green (the two
        # pieces are tested in isolation above) while the real leak path
        # stayed wide open -- this proves the wiring, not just the parts.
        inner = logging.Formatter("%(message)s")
        handler = logging.StreamHandler()
        handler.setFormatter(inner)
        self.access_logger.handlers = [handler]

        setup_console_logging(level=logging.INFO)

        self.assertIsInstance(handler.formatter, _RedactingWrapperFormatter)
        self.assertIs(handler.formatter._inner, inner)


if __name__ == "__main__":
    unittest.main()
