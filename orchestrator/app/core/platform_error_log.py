"""Mirror the orchestrator's WARNING+ logs (secret-redacted) into a file on the
shared volume so agents can read platform errors and help fix the platform.

The shared volume ``ai-employee-shared`` is already mounted at ``/shared`` in both
the orchestrator and every agent container, so NO docker access is needed — an
agent simply reads ``/shared/platform-errors.log`` with its normal file tools.
Every line is run through app.core.log_redaction so credentials never land in the
file. The handler rotates, so the file can never grow unbounded.
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from app.core.log_redaction import redact_logs

_DEFAULT_PATH = "/shared/platform-errors.log"


class _RedactingFormatter(logging.Formatter):
    """Formats the record normally, then redacts secrets from the final string."""

    def format(self, record: logging.LogRecord) -> str:
        return redact_logs(super().format(record))


class _RedactingWrapperFormatter(logging.Formatter):
    """Wraps an EXISTING formatter instance and redacts its output.

    Used for uvicorn's access logger (see ``_harden_uvicorn_access_logging``),
    whose ``AccessFormatter`` populates custom record fields (``levelprefix``,
    colour codes) that only its own ``formatMessage`` knows how to fill in —
    reimplementing that format string with a plain ``_RedactingFormatter``
    would either drop the colour/level handling or raise ``KeyError`` on the
    fields it doesn't set. Delegating to the original formatter and redacting
    its already-rendered string keeps that logic untouched.
    """

    def __init__(self, inner: logging.Formatter):
        super().__init__()
        self._inner = inner

    def format(self, record: logging.LogRecord) -> str:
        return redact_logs(self._inner.format(record))


def _harden_uvicorn_access_logging() -> None:
    """Route uvicorn's access log through the same redacting formatter as
    everything else (CWE-532, reported responsibly 2026-09-17).

    ``uvicorn.access`` ships with ``propagate=False`` and its own handler
    (uvicorn's default logging config, applied before this module ever
    runs) — access log lines never reach the root logger's redacting
    handlers above, so a token in a query string (a webhook bearer
    fallback, the legacy WebSocket ``?token=``, the computer-use bridge's
    ``?token=``) would be written completely in the clear. Wrapping each
    existing handler's formatter (rather than replacing it) keeps uvicorn's
    own field/colour handling intact; a request line with no secret in it
    is unaffected.
    """
    access_logger = logging.getLogger("uvicorn.access")
    for handler in access_logger.handlers:
        inner = handler.formatter
        if inner is not None and not isinstance(inner, _RedactingWrapperFormatter):
            handler.setFormatter(_RedactingWrapperFormatter(inner))


def setup_platform_error_log(path: str | None = None, level: int = logging.WARNING) -> bool:
    """Attach a rotating, secret-redacted WARNING+ file handler to the root logger.

    Returns True if installed, False if the target directory isn't writable (e.g.
    local dev without the shared volume) — platform logging is then simply skipped
    and nothing breaks.
    """
    target = path or os.environ.get("PLATFORM_ERROR_LOG_PATH", _DEFAULT_PATH)
    try:
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        handler = RotatingFileHandler(
            target, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
        )
    except OSError:
        return False

    handler.setLevel(level)
    handler.setFormatter(
        _RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )

    root = logging.getLogger()
    # Idempotent: don't stack a second handler on repeated startups.
    for h in root.handlers:
        if isinstance(h, RotatingFileHandler) and getattr(h, "baseFilename", "") == handler.baseFilename:
            return True
    root.addHandler(handler)
    # Ensure WARNING+ records actually reach the handler.
    if root.level == logging.NOTSET or root.level > level:
        root.setLevel(level)
    return True


def setup_console_logging(level: int | None = None) -> int:
    """Anwendungs-Logs sichtbar machen — im Container-Log.

    Ohne das landete **nichts** unterhalb von WARNING irgendwo: die Datei
    ``/shared/platform-errors.log`` nimmt erst ab WARNING an, und einen
    Ausgabe-Handler hatte der Wurzel-Logger gar nicht. Sichtbar war nur, was
    jemand mit ``print`` geschrieben hat, plus das Zugriffsprotokoll von uvicorn.

    Das ist teuer geworden: Am 2026-08-13 liess sich nicht feststellen, ob ein
    Rueckmeldeweg ueberhaupt ausgeloest hatte — die ``logger.info``-Zeile, die
    genau das beantwortet haette, existierte im Code und nirgends sonst. Aus
    fehlenden Log-Zeilen laesst sich dann nichts schliessen, und die Diagnose
    faellt auf Raten zurueck.

    Stufe ueber ``LOG_LEVEL`` einstellbar; Vorgabe INFO.
    """
    if level is None:
        raw = (os.environ.get("LOG_LEVEL") or "INFO").upper()
        level = getattr(logging, raw, logging.INFO)

    root = logging.getLogger()
    for h in root.handlers:
        if isinstance(h, logging.StreamHandler) and getattr(h, "_ai_employee_console", False):
            return level

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    # Dieselbe Schwaerzung wie in der Datei: ein Zugang, der im Container-Log
    # steht, ist genauso offen wie einer in einer Datei.
    handler.setFormatter(
        _RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    handler._ai_employee_console = True   # noqa: SLF001
    root.addHandler(handler)
    if root.level == logging.NOTSET or root.level > level:
        root.setLevel(level)

    # Defense in depth (CWE-532, reported responsibly 2026-09-17): httpx logs
    # every outbound request at INFO with the full URL, and the Telegram Bot
    # API carries the bot token in the URL PATH, not a header — there is no
    # way to redact "the URL" without redacting the whole line. The primary
    # fix is the log_redaction.py regex (it must never leak regardless of
    # logger level, since other libraries could log the same way tomorrow),
    # but there is no reason for these two loggers' routine request/response
    # lines to reach INFO at all — WARNING+ (connection failures etc.) still
    # gets through. Silences most of the platform's log noise as a side
    # effect (was ~93% of orchestrator log volume on the reporter's instance).
    for _noisy in ("httpx", "httpcore"):
        logging.getLogger(_noisy).setLevel(logging.WARNING)

    _harden_uvicorn_access_logging()

    return level
