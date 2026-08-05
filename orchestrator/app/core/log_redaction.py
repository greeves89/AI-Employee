"""Redact secrets from container logs before they are handed to an agent.

The self-improvement flow lets an agent read its own container logs to diagnose
failures. Logs routinely contain bearer tokens, API keys and env dumps, so every
line is scrubbed here before it leaves the orchestrator. Fail-closed by design:
patterns are broad and we'd rather over-redact than leak a credential.
"""

from __future__ import annotations

import re

# Ordered (pattern, replacement). Each keeps a hint of what was removed so the
# log stays readable for debugging without exposing the value.
_PATTERNS: list[tuple[re.Pattern, str]] = [
    # PEM / private key blocks (multiline).
    (re.compile(r"-----BEGIN [^-]+-----.*?-----END [^-]+-----", re.DOTALL), "[REDACTED_KEY_BLOCK]"),
    # Authorization: Bearer <token> / Basic <token>.
    (re.compile(r"(?i)\b(authorization|proxy-authorization)\b\s*[:=]\s*(bearer|basic)\s+\S+"), r"\1: \2 [REDACTED]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{12,}"), "Bearer [REDACTED]"),
    # Provider API keys with recognisable prefixes (OpenAI, Anthropic, Google, GitHub, Slack…).
    (re.compile(r"\b(sk|pk|rk)-[A-Za-z0-9._\-]{16,}"), "[REDACTED_API_KEY]"),
    (re.compile(r"\b(gh[pousr]|github_pat)_[A-Za-z0-9_]{20,}"), "[REDACTED_GH_TOKEN]"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}"), "[REDACTED_SLACK_TOKEN]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED_AWS_KEY]"),
    # JWTs (three base64url segments).
    (re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"), "[REDACTED_JWT]"),
    # Telegram bot tokens: <bot_id>:<secret> (e.g. from InvalidToken error text
    # "The token 123456:AA... was rejected by the server"). Not a bearer/JWT, so
    # it needs its own rule.
    (re.compile(r"\b\d{6,}:[A-Za-z0-9_\-]{30,}"), "[REDACTED_TELEGRAM_TOKEN]"),
    # KEY=VALUE / "key": "value" for anything that smells sensitive. The
    # separator swallows an optional closing quote of the key so JSON-style
    # `"password": "…"` is caught as well as env-style `PASSWORD=…`.
    (re.compile(
        r'(?i)\b([A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|API_?KEY|ENCRYPTION_KEY|PRIVATE_KEY|ACCESS_KEY|CLIENT_SECRET|WEBHOOK|DATABASE_URL|DSN)[A-Z0-9_]*)'
        r'("?\s*[:=]\s*)("?)([^\s"\']{4,})(\3)'),
        r"\1\2\3[REDACTED]\5"),
]


def redact_logs(text: str) -> str:
    """Return ``text`` with credential-like substrings masked."""
    if not text:
        return text
    out = text
    for pattern, repl in _PATTERNS:
        out = pattern.sub(repl, out)
    return out


# C0/C1 control chars *except* tab (\x09) — CR/LF are removed explicitly first
# (that ``.replace`` is the barrier CodeQL recognises for log-injection), then
# this drops NULs and terminal-escape bytes that could still corrupt a log line.
# U+2028/U+2029 (LINE/PARAGRAPH SEPARATOR) are stripped too: some JSON/JS log
# viewers treat them as line breaks, so they are a line-forging bypass candidate.
_LOG_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f  ]")


def scrub_log(value: object) -> str:
    """Return ``value`` as a single-line string safe to interpolate into a log
    record.

    User-controlled input (agent/session ids, paths, request fields) can carry
    ``\\r``/``\\n`` and forge or split log entries (CWE-117 log injection). This
    removes line breaks and other control characters so a logged value can never
    start a new, attacker-shaped log record. Use it to wrap any request-derived
    value before it reaches ``logger.*``.
    """
    text = str(value).replace("\r", "").replace("\n", "")
    return _LOG_CONTROL_CHARS.sub("", text)
