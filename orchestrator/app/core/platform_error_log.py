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
import re
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


_MAX_REPAIR_BYTES = 8_000_000

# The handler's backupCount AND the repair loop's range read this one value, so
# the two can never drift apart: a second, hand-maintained copy of "3" is
# exactly how a rotated generation ends up outside the repaired set again.
_BACKUP_COUNT = 3


def strip_nul_bytes(path: str, max_bytes: int = _MAX_REPAIR_BYTES) -> int:
    """Repair a log file that an unclean shutdown left with a run of NUL bytes.

    A file being appended to can come back after a hard kill with NUL bytes
    where the last writes were never flushed to disk. That looks harmless and
    is not: **one** NUL byte anywhere makes the WHOLE file count as binary for
    grep, so ``grep PATTERN /shared/platform-errors.log`` prints nothing and
    exits 1 — indistinguishable from an honest "no match". An agent triaging a
    recurring platform error then concludes the error class is gone when in
    truth its own measurement never read the file.

    That is not a hypothetical: on a live installation three of the four log
    generations carried such holes (174 / 393 / 0 / 3219 NUL bytes), i.e. the
    grep-based triage every agent is pointed at was silently blind most of the
    time. Startup is the right moment to repair it, because the hole is left
    behind by exactly the unclean stop that this startup follows.

    Rewrites IN PLACE (same inode, no ``os.replace``) so a file descriptor that
    some other handler already holds keeps writing to the file we just fixed.
    A file with no NUL byte is not written to at all and stays byte-identical.

    Returns the number of NUL bytes removed; 0 if the file was clean, missing,
    too large to repair, or not readable.
    """
    try:
        if os.path.getsize(path) > max_bytes:
            return 0
        with open(path, "r+b") as fh:
            data = fh.read()
            removed = data.count(b"\x00")
            if removed == 0:
                return 0
            fh.seek(0)
            fh.write(data.replace(b"\x00", b""))
            fh.truncate()
        return removed
    except OSError:
        return 0


def log_generations(path: str, backup_count: int = _BACKUP_COUNT) -> list[str]:
    """The active log plus EVERY rotated generation that exists next to it.

    ``RotatingFileHandler`` moves the active file to ``<path>.1`` and shifts the
    older ones up, so a NUL hole that is not repaired before the next rotation
    wanders into a backup file and stays there forever: ``strip_nul_bytes`` was
    only ever called with the active path, and nothing else ever touches ``.1``
    to ``.3``. Measured live on 2026-10-17: ``.1`` (393 NUL bytes, 07.09.) and
    ``.3`` (3.219 NUL bytes, 06.09.) carried holes months old, in the files an
    honest triage reads next to the active one.

    How much that costs depends on WHERE the hole sits, and that is worth being
    precise about (measured 2026-10-18 against the grep in the agent image,
    ugrep 7.8.4): it declares the whole file binary — no output, exit 1, i.e.
    indistinguishable from an honest "no match" — exactly when the first NUL
    byte lies below offset 262144 (bracketed: 262143 silent, 262144 readable).
    Of the live files only the ACTIVE one was over that line (first NUL at
    211665); ``.1`` (481753) and ``.3`` (979821) still answered. A rotated
    generation is nevertheless exposed, for two reasons: the hole position is
    wherever the unclean stop happened, so roughly every eighth hole in a 2 MB
    generation falls inside that window; and GNU grep — the grep most hosts
    carry — goes binary on ANY NUL byte, as does every tool that decodes the
    file as text. Repairing all generations removes the question entirely.

    Returns the active path first, then the rotated ones in generation order.
    Besides ``.1`` to ``backup_count`` this also picks up numerically suffixed
    siblings ABOVE that range: lowering ``backupCount`` orphans the generations
    that a previous, larger setting had already written, and the handler never
    looks at them again. Only ``<basename>.<digits>`` regular files count — a
    ``.log.gz``, a ``.log.bak`` or another log's generation is never rewritten.
    """
    paths = [path]
    # Keyed on the normalised path: the range below and the directory scan can
    # spell the SAME file two ways ("foo.log.1" vs "./foo.log.1"), and without
    # this the file would be visited twice.
    seen = {os.path.normpath(path)}
    directory = os.path.dirname(path) or "."
    stem = os.path.basename(path)
    found: list[tuple[int, str]] = []
    for generation in range(1, backup_count + 1):
        found.append((generation, f"{path}.{generation}"))
    try:
        entries = os.listdir(directory)
    except OSError:
        entries = []
    for name in entries:
        match = re.fullmatch(re.escape(stem) + r"\.(\d+)", name)
        if match:
            found.append((int(match.group(1)), os.path.join(directory, name)))
    for _generation, candidate in sorted(found):
        key = os.path.normpath(candidate)
        if key in seen:
            continue
        seen.add(key)
        # A directory or a symlink under this name is not a log generation of
        # ours; rewriting it in place would corrupt whatever it really is.
        if os.path.isfile(candidate) and not os.path.islink(candidate):
            paths.append(candidate)
    return paths


def setup_platform_error_log(path: str | None = None, level: int = logging.WARNING) -> bool:
    """Attach a rotating, secret-redacted WARNING+ file handler to the root logger.

    Returns True if installed, False if the target directory isn't writable (e.g.
    local dev without the shared volume) — platform logging is then simply skipped
    and nothing breaks.
    """
    target = path or os.environ.get("PLATFORM_ERROR_LOG_PATH", _DEFAULT_PATH)
    # Repair NUL holes an unclean stop may have left behind BEFORE appending to
    # the file again — otherwise a single such byte makes every agent's
    # `grep` on this log return "no match" for the whole file (see
    # strip_nul_bytes).
    #
    # Over ALL generations, not just the active file: a hole that appears after
    # startup is carried into `<path>.1` by the next rotation, and a repair that
    # only ever sees the active path can then never reach it again (see
    # log_generations). One oversized or unreadable generation must not stop the
    # others from being repaired, hence the plain loop.
    removed = 0
    repaired_files = 0
    for generation_path in log_generations(target):
        generation_removed = strip_nul_bytes(generation_path)
        if generation_removed:
            removed += generation_removed
            repaired_files += 1
    try:
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        handler = RotatingFileHandler(
            target, maxBytes=2_000_000, backupCount=_BACKUP_COUNT, encoding="utf-8"
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
    if removed:
        logging.getLogger(__name__).warning(
            "Platform error log carried %d NUL byte(s) from an unclean stop in "
            "%d of its generation(s) and was repaired — grep would have reported "
            "those whole files as having no matches until now",
            removed,
            repaired_files,
        )
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
