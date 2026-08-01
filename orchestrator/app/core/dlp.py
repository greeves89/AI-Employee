"""DLP egress filter (#388) — scan agent-generated text before it leaves the platform.

Two layers:
  * a **pure scanner** (``classify`` / ``mask``) — deterministic, no I/O, fully
    unit-testable. Reuses the credential patterns from ``log_redaction`` for the
    ``secret`` class and adds PII classes (IBAN, credit card w/ Luhn, e-mail,
    11-digit German tax-id style number).
  * a **DB-aware evaluator** (``evaluate_egress``) — resolves the per-class action
    from ``DlpRule`` (agent-specific > global > built-in default), applies it
    (allow/log/mask/block), and writes an ``AuditLog`` record that records the
    matched *classes and counts only* — never the sensitive value.

Opt-in: passthrough (allow) unless the ``dlp_enabled`` setting is true, so existing
deployments are unaffected until an admin turns it on. Fail-open on internal errors
(logs a warning) so a bug can never wedge all outbound messaging.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from app.core.log_redaction import _PATTERNS as _SECRET_PATTERNS
from app.core.log_redaction import redact_logs

logger = logging.getLogger(__name__)

CLASSES = ("secret", "iban", "credit_card", "email", "de_tax_id")

# Built-in default action per class when no DB rule overrides it.
DEFAULT_ACTIONS: dict[str, str] = {
    "secret": "block",       # never leak credentials
    "iban": "mask",
    "credit_card": "mask",
    "de_tax_id": "log",      # 11-digit numbers are false-positive prone -> log only
    "email": "allow",        # e-mails are usually legitimate to send
}
ACTION_SEVERITY = {"allow": 0, "log": 1, "mask": 2, "block": 3}
VALID_ACTIONS = set(ACTION_SEVERITY)

_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Za-z0-9]){11,30}\b")
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
_CC = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_DE_TAX = re.compile(r"\b\d{11}\b")


def _luhn_ok(s: str) -> bool:
    ds = [int(c) for c in s if c.isdigit()]
    if not (13 <= len(ds) <= 19):
        return False
    total, alt = 0, False
    for d in reversed(ds):
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def _has_secret(text: str) -> bool:
    return any(pat.search(text) for pat, _ in _SECRET_PATTERNS)


def classify(text: str) -> dict[str, int]:
    """Return ``{class: match_count}`` for every DLP class present in ``text``."""
    if not text:
        return {}
    hits: dict[str, int] = {}
    if _has_secret(text):
        hits["secret"] = sum(len(pat.findall(text)) for pat, _ in _SECRET_PATTERNS) or 1
    iban = _IBAN.findall(text)
    if iban:
        hits["iban"] = len(iban)
    cc = [m for m in _CC.findall(text) if _luhn_ok(m)]
    if cc:
        hits["credit_card"] = len(cc)
    email = _EMAIL.findall(text)
    if email:
        hits["email"] = len(email)
    tax = _DE_TAX.findall(text)
    if tax:
        hits["de_tax_id"] = len(tax)
    return hits


def mask(text: str, classes: set[str]) -> str:
    """Return ``text`` with the given classes redacted (keeps a ``[REDACTED_*]`` hint)."""
    out = text
    if "secret" in classes:
        out = redact_logs(out)
    if "iban" in classes:
        out = _IBAN.sub("[REDACTED_IBAN]", out)
    if "credit_card" in classes:
        out = _CC.sub(lambda m: "[REDACTED_CC]" if _luhn_ok(m.group()) else m.group(), out)
    if "email" in classes:
        out = _EMAIL.sub("[REDACTED_EMAIL]", out)
    if "de_tax_id" in classes:
        out = _DE_TAX.sub("[REDACTED_ID]", out)
    return out


@dataclass
class DlpVerdict:
    allowed: bool
    output: str
    classes: dict[str, int]           # matched class -> count
    actions: dict[str, str]           # matched class -> resolved action
    effective: str                    # overall (highest-severity) action

    @property
    def blocked(self) -> bool:
        return not self.allowed


def resolve_actions(classes: dict[str, int], rules: list) -> dict[str, str]:
    """Resolve the effective action per matched class from DlpRule rows.

    Precedence: agent-specific rule (already pre-filtered by caller to this agent or
    global) > global rule > built-in default. ``rules`` is a list of DlpRule with
    attributes pii_class, agent_id, action, enabled.
    """
    by_agent: dict[str, str] = {}
    by_global: dict[str, str] = {}
    for r in rules:
        if not getattr(r, "enabled", True):
            continue
        if r.action not in VALID_ACTIONS:
            continue
        if r.agent_id is None:
            by_global[r.pii_class] = r.action
        else:
            by_agent[r.pii_class] = r.action
    out: dict[str, str] = {}
    for cls in classes:
        out[cls] = by_agent.get(cls) or by_global.get(cls) or DEFAULT_ACTIONS.get(cls, "log")
    return out


def decide(text: str, classes: dict[str, int], actions: dict[str, str]) -> DlpVerdict:
    """Apply resolved actions to produce the final verdict (pure)."""
    if not classes:
        return DlpVerdict(True, text, {}, {}, "allow")
    effective = max(actions.values(), key=lambda a: ACTION_SEVERITY.get(a, 0))
    if effective == "block":
        return DlpVerdict(False, "", classes, actions, "block")
    to_mask = {c for c, a in actions.items() if a == "mask"}
    output = mask(text, to_mask) if to_mask else text
    return DlpVerdict(True, output, classes, actions, effective)


_EVENT_BY_ACTION = {"block": "dlp_blocked", "mask": "dlp_masked", "log": "dlp_flagged"}


async def evaluate_egress(text: str, *, agent_id: str | None = None, channel: str = "unknown") -> DlpVerdict:
    """Scan one outbound message and enforce the configured DLP policy.

    Opens its own DB session (safe to call from Redis listeners). Passthrough when
    ``dlp_enabled`` is not true. Fail-open on any internal error.
    """
    if not text:
        return DlpVerdict(True, text, {}, {}, "allow")
    try:
        classes = classify(text)
        if not classes:
            return DlpVerdict(True, text, {}, {}, "allow")

        from sqlalchemy import select

        from app.db.session import async_session_factory
        from app.models.audit_log import AuditLog
        from app.models.dlp_rule import DlpRule
        from app.services.settings_service import SettingsService

        async with async_session_factory() as db:
            svc = SettingsService(db)
            enabled = (await svc.get("dlp_enabled")) in ("true", "1", True)
            if not enabled:
                return DlpVerdict(True, text, classes, {}, "allow")

            rules = (await db.execute(
                select(DlpRule).where(
                    (DlpRule.agent_id == agent_id) | (DlpRule.agent_id.is_(None))
                )
            )).scalars().all()
            actions = resolve_actions(classes, rules)
            verdict = decide(text, classes, actions)

            if verdict.effective in _EVENT_BY_ACTION:
                db.add(AuditLog(
                    agent_id=agent_id or "system",
                    event_type=_EVENT_BY_ACTION[verdict.effective],
                    command=channel,
                    outcome="blocked" if verdict.blocked else "success",
                    meta={"classes": classes, "actions": actions, "channel": channel},
                ))
                await db.commit()
            return verdict
    except Exception as e:  # noqa: BLE001 — never wedge outbound messaging
        logger.warning("DLP evaluate_egress failed (fail-open): %s", e)
        return DlpVerdict(True, text, {}, {}, "allow")
