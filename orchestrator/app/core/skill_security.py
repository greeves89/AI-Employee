"""Static install-time security gate for marketplace skills (issue #192).

The "postinstall dropper" attack adds a single lifecycle script to a
``package.json`` (or ships a ``setup.sh`` / ``postinstall.js`` hook) that runs
arbitrary code the moment a bundle is installed — in ~1 second, never touching
source review. This module scans a skill's content and file attachments at
import/upload time and rejects any bundle that ships such a hook.

Scope: this is the STATIC half of #192. The runtime network allow-list
(deny-by-default egress per skill) and the admin review-queue UI are follow-ups.
An admin can allow-list a specific skill name via the ``SKILL_HOOK_ALLOWLIST``
env var (comma-separated) to bypass the gate for a trusted, reviewed skill.
"""

import json
import os
import re

# npm/yarn/pnpm lifecycle scripts that a package manager runs automatically on
# install/start — the actual execution vector for the dropper attack.
_DANGEROUS_LIFECYCLE_KEYS = frozenset({
    "preinstall", "install", "postinstall",
    "preuninstall", "postuninstall",
    "prepare", "prepublish", "prepublishonly",
    "prestart", "poststart",
})

# Filenames that common tooling executes as a setup/lifecycle hook.
_DANGEROUS_HOOK_FILENAME = re.compile(
    r"^(pre|post)?(install|start|setup)\.(sh|bash|zsh|py|js|cjs|mjs|ts)$",
    re.IGNORECASE,
)

# An embedded package.json "scripts" block carrying a dangerous lifecycle key,
# e.g. inside a fenced code block in the skill's markdown content.
_EMBEDDED_SCRIPTS_RE = re.compile(
    r'"scripts"\s*:\s*\{[^{}]*?"(' + "|".join(sorted(_DANGEROUS_LIFECYCLE_KEYS)) + r')"\s*:',
    re.IGNORECASE | re.DOTALL,
)


class SkillSecurityError(ValueError):
    """Raised when a skill fails the static install-time security gate."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def is_allowlisted(skill_name: str | None) -> bool:
    """True if an admin has explicitly allow-listed this skill name via env."""
    raw = os.environ.get("SKILL_HOOK_ALLOWLIST", "")
    allowed = {n.strip() for n in raw.split(",") if n.strip()}
    return bool(skill_name) and skill_name in allowed


def _scan_package_json(raw: str) -> str | None:
    """Reason string if *raw* parses as a package.json with a dangerous
    lifecycle script, else None. Non-JSON / non-object input returns None."""
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    scripts = data.get("scripts")
    if not isinstance(scripts, dict):
        return None
    found = sorted(k for k in scripts if isinstance(k, str) and k.lower() in _DANGEROUS_LIFECYCLE_KEYS)
    if found:
        return f"package.json declares lifecycle script(s): {', '.join(found)}"
    return None


def check_skill_content(content: str | None) -> None:
    """Reject skill content that embeds a package.json lifecycle-script block."""
    if not content:
        return
    if _EMBEDDED_SCRIPTS_RE.search(content):
        raise SkillSecurityError(
            "Skill content embeds a package.json lifecycle script "
            "(preinstall/postinstall/prestart/…), which is blocked as a "
            "post-install execution vector. Remove the hook or ask an admin to "
            "allow-list this skill."
        )


def check_skill_file(filename: str, data: bytes) -> None:
    """Reject a skill file attachment that is a setup/lifecycle hook or a
    package.json declaring a dangerous lifecycle script."""
    name = os.path.basename(filename or "")
    if _DANGEROUS_HOOK_FILENAME.match(name):
        raise SkillSecurityError(
            f"File '{name}' is a setup/lifecycle hook, which is blocked as a "
            "post-install execution vector."
        )
    if name.lower() == "package.json":
        try:
            text = data.decode("utf-8")
        except (UnicodeDecodeError, AttributeError):
            return
        reason = _scan_package_json(text)
        if reason:
            raise SkillSecurityError(f"Blocked: {reason}.")
