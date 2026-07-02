"""Autonomy capability matrix — single source of truth.

A 3-state matrix (allow / ask / deny) over a fixed set of capabilities, grouped
into "own container" (sandboxed, low risk) and "external tools" (leaves the box).
The L1–L4 levels are just PRESETS that fill the matrix; after applying one the
user can fine-tune individual cells (→ "custom").

The matrix is the authoritative config: it is stored on the agent
(``agent.config["autonomy_matrix"]``) and rendered into the per-task prompt via
``matrix_to_prompt`` — the platform's approval enforcement is prompt-driven, and
``request_approval`` / bash-approval remain the server-side gates for asks.
"""

from __future__ import annotations

GROUP_CONTAINER = "container"   # Eigener Container (sandboxed)
GROUP_EXTERNAL = "external"     # Externe Tools (Außenwirkung)

# state values
ALLOW = "allow"   # do without asking
ASK = "ask"       # call request_approval first
DENY = "deny"     # never; refuse

STATES = (ALLOW, ASK, DENY)

# Fixed capability rows. `legacy_category` keeps a mapping to the old
# ApprovalRule categories so nothing else in the system has to change.
CAPABILITIES: list[dict] = [
    {"key": "file_read", "group": GROUP_CONTAINER, "label": "Dateien lesen",
     "description": "Dateien/Verzeichnisse lesen, durchsuchen, analysieren.", "legacy_category": "file_read"},
    {"key": "file_write", "group": GROUP_CONTAINER, "label": "Dateien schreiben",
     "description": "Dateien in /workspace erstellen, bearbeiten, löschen.", "legacy_category": "file_write"},
    {"key": "shell_exec", "group": GROUP_CONTAINER, "label": "Shell / Befehle",
     "description": "Bash-Befehle ausführen, auch systemverändernd (im Container).", "legacy_category": "shell_exec"},
    {"key": "system_config", "group": GROUP_CONTAINER, "label": "Pakete / Config",
     "description": "Software installieren, Container-Konfiguration anpassen.", "legacy_category": "system_config"},
    {"key": "web", "group": GROUP_EXTERNAL, "label": "Web-Recherche (lesen)",
     "description": "Im Web suchen und öffentliche Seiten abrufen (nur lesen).", "legacy_category": "web_search"},
    {"key": "email_m365", "group": GROUP_EXTERNAL, "label": "E-Mail / M365 senden",
     "description": "E-Mails/Teams/Kalender/OneDrive/SharePoint schreiben & senden.", "legacy_category": "custom"},
    {"key": "external_api", "group": GROUP_EXTERNAL, "label": "Externe API / Webhooks",
     "description": "Ausgehende API-Calls/Webhooks an Dritt-Systeme.", "legacy_category": "custom"},
    {"key": "messaging", "group": GROUP_EXTERNAL, "label": "Chat / Telegram senden",
     "description": "Nachrichten nach außen an Nutzer/Kanäle senden.", "legacy_category": "custom"},
    {"key": "git_push", "group": GROUP_EXTERNAL, "label": "Git push / PR",
     "description": "Commits nach außen pushen, Pull Requests erstellen.", "legacy_category": "custom"},
    {"key": "purchases", "group": GROUP_EXTERNAL, "label": "Käufe / Zahlungen",
     "description": "Bestellungen auslösen, Zahlungen tätigen.", "legacy_category": "purchase"},
]

CAPABILITY_KEYS = [c["key"] for c in CAPABILITIES]
_CAP_BY_KEY = {c["key"]: c for c in CAPABILITIES}

# L1–L4 presets as full matrices. Anything not "allow" defaults to "ask" — deny
# is reserved for manual hardening (e.g. "purchases: never"), so no preset uses it.
_ALLOW_BY_LEVEL: dict[str, set[str]] = {
    "l1": {"file_read", "web"},
    "l2": {"file_read", "web", "file_write"},
    "l3": {"file_read", "file_write", "shell_exec", "system_config", "web"},
    "l4": set(CAPABILITY_KEYS),
}

LEVEL_LABELS = {
    "l1": "L1 — Nur lesen",
    "l2": "L2 — Empfehlungen",
    "l3": "L3 — Ausführen mit Freigabe",
    "l4": "L4 — Vollständig autonom",
}


def matrix_for_level(level: str) -> dict[str, str]:
    """Full {capability: state} matrix for an L1–L4 preset."""
    allow = _ALLOW_BY_LEVEL.get((level or "l3").lower(), _ALLOW_BY_LEVEL["l3"])
    return {k: (ALLOW if k in allow else ASK) for k in CAPABILITY_KEYS}


def normalize_matrix(raw: dict | None, level: str = "l3") -> dict[str, str]:
    """Return a complete, valid matrix. Missing keys are filled from the level
    preset; invalid states fall back to ``ask``."""
    base = matrix_for_level(level)
    if not isinstance(raw, dict):
        return base
    out = dict(base)
    for k in CAPABILITY_KEYS:
        v = raw.get(k)
        if v in STATES:
            out[k] = v
    return out


def is_unrestricted(matrix: dict[str, str]) -> bool:
    """True when every capability is ``allow`` (L4-equivalent → no approvals)."""
    return all(matrix.get(k) == ALLOW for k in CAPABILITY_KEYS)


def matrix_to_prompt(matrix: dict[str, str]) -> str:
    """Render the matrix as an authoritative autonomy block for the agent prompt."""
    if is_unrestricted(matrix):
        return (
            "\n=== AUTONOMY: FULL ===\n"
            "You are FULLY AUTONOMOUS. Perform ALL actions WITHOUT asking — files, "
            "shell, external communication (email/M365/Teams/OneDrive), web/API calls, "
            "git push, purchases, system changes. Do NOT call `request_approval`; just "
            "do the task end-to-end. Only stop for something destructive AND "
            "irreversible AND clearly outside what the user asked for.\n"
            "=== END AUTONOMY ===\n"
        )
    allow = [k for k in CAPABILITY_KEYS if matrix.get(k) == ALLOW]
    ask = [k for k in CAPABILITY_KEYS if matrix.get(k) == ASK]
    deny = [k for k in CAPABILITY_KEYS if matrix.get(k) == DENY]

    def _labels(keys: list[str]) -> str:
        return ", ".join(_CAP_BY_KEY[k]["label"] for k in keys) or "—"

    lines = [
        "",
        "=== AUTONOMY MATRIX (MANDATORY) ===",
        f"ALLOWED without asking: {_labels(allow)}.",
        f"Requires approval first (call `request_approval` BEFORE acting): {_labels(ask)}.",
    ]
    if deny:
        lines.append(f"FORBIDDEN — never do these, refuse and tell the user: {_labels(deny)}.")
    lines.extend([
        "For anything not clearly covered, call `request_approval`. Never do a FORBIDDEN action.",
        "=== END AUTONOMY MATRIX ===",
        "",
    ])
    return "\n".join(lines)


def taxonomy_payload() -> dict:
    """Capability taxonomy + presets for the frontend matrix editor."""
    return {
        "groups": [
            {"key": GROUP_CONTAINER, "label": "Eigener Container"},
            {"key": GROUP_EXTERNAL, "label": "Externe Tools"},
        ],
        "states": list(STATES),
        "capabilities": CAPABILITIES,
        "presets": {lvl: {"label": LEVEL_LABELS[lvl], "matrix": matrix_for_level(lvl)}
                    for lvl in ("l1", "l2", "l3", "l4")},
    }
