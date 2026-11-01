"""Der verwaltete Pruef-Hook muss wirken, bevor eine CLI startet (P1-8).

Claude Code und Codex lesen den PreToolUse-Hook aus root-eigenen Dateien im
Abbild (``agent/richtlinie/``). Fehlt eine davon, ist sie beschreibbar oder steht
der Hook nicht darin, liefe die CLI ohne jede Pruefung — beide werden mit
``--dangerously-skip-permissions`` bzw. ``--dangerously-bypass-approvals-and-sandbox``
gestartet. Dann startet sie gar nicht.
"""

from __future__ import annotations

import os

#: Hook-Skript und die Dateien, die es fuer die jeweilige CLI eintragen.
HOOK = "/opt/mcp/pretooluse-hook.mjs"
DATEIEN = ("/etc/claude-code/managed-settings.json", "/etc/codex/requirements.toml")
#: Nur root darf sie aendern — der Agent laeuft ohne sudo.
EIGENTUEMER = 0


def fehlt() -> str | None:
    """Warum der Hook nicht sicher wirkt — ``None``, wenn alles da ist."""
    if not os.path.isfile(HOOK):
        return f"{HOOK} fehlt"
    for pfad in DATEIEN:
        try:
            stat = os.stat(pfad)
            with open(pfad, encoding="utf-8") as fh:
                inhalt = fh.read()
        except OSError:
            return f"{pfad} fehlt"
        if stat.st_uid != EIGENTUEMER or stat.st_mode & 0o022:
            return f"{pfad} ist nicht schreibgeschützt"
        if HOOK not in inhalt:
            return f"{pfad} trägt den Prüf-Hook nicht ein"
    return None


def sicherstellen() -> None:
    """Vor jedem CLI-Start: wirft, wenn der Hook nicht sicher wirkt."""
    grund = fehlt()
    if grund:
        raise RuntimeError(f"Prüf-Hook nicht aktiv ({grund}) — Lauf aus Sicherheitsgründen abgelehnt.")
