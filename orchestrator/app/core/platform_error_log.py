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
    return level
