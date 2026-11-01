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


def _ungeschuetzt(pfad: str) -> bool:
    """Darf ausser root jemand die Datei oder ihren Ordner aendern? Ein Symlink
    zaehlt als ungeschuetzt — sein Ziel koennte woanders liegen."""
    if os.path.islink(pfad):
        return True
    for teil in (pfad, os.path.dirname(pfad)):
        stat = os.stat(teil)
        if stat.st_uid != EIGENTUEMER or stat.st_mode & 0o022:
            return True
    return False


def fehlt() -> str | None:
    """Warum der Hook nicht sicher wirkt — ``None``, wenn alles da ist."""
    for pfad in (HOOK, *DATEIEN):
        try:
            if not os.path.isfile(pfad):
                return f"{pfad} fehlt"
            if _ungeschuetzt(pfad):
                return f"{pfad} ist nicht schreibgeschützt"
            if pfad != HOOK:
                with open(pfad, encoding="utf-8") as fh:
                    if HOOK not in fh.read():
                        return f"{pfad} trägt den Prüf-Hook nicht ein"
        except OSError:
            return f"{pfad} fehlt"
    return None


def sicherstellen() -> None:
    """Vor jedem CLI-Start: wirft, wenn der Hook nicht sicher wirkt."""
    grund = fehlt()
    if grund:
        raise RuntimeError(f"Prüf-Hook nicht aktiv ({grund}) — Lauf aus Sicherheitsgründen abgelehnt.")


#: prctl(2): Prozess fuer ptrace und /proc/<pid>/mem unzugaenglich machen.
_PR_SET_DUMPABLE = 4


def pruefserver_abschirmen() -> bool:
    """Den Agenten-Prozess (er traegt den Pruefserver des Hooks) vor der eigenen
    Shell abschirmen.

    Shell und Pruefserver laufen unter derselben Kennung; ohne Yama durfte die
    Shell den Prozess per ptrace anhaengen und seinen Speicher lesen und
    schreiben — also auch die Entscheidungen des Pruefservers (live auf dem Pi
    gemessen, 02.11.2026). Nicht "dumpbar" verweigert der Kernel beides jedem
    Prozess ohne CAP_SYS_PTRACE. Gestartete CLIs erben es nicht: ``exec`` setzt
    den Wert zurueck. Rueckgabe: ob es gewirkt hat (nur unter Linux moeglich).
    """
    import ctypes
    import logging

    try:
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(_PR_SET_DUMPABLE, 0, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), os.strerror(ctypes.get_errno()))
    except (OSError, AttributeError) as e:
        logging.getLogger(__name__).warning("Pruefserver nicht abschirmbar: %s", e)
        return False
    return True

