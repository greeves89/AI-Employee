"""Schreibschutz fuer Werkzeugaufrufe — EINE Regel fuer alle Laufzeiten (#781).

Claude Code und Codex fragen den PreToolUse-Hook (``pretooluse_hook.decide``),
die custom_llm-Laufzeit prueft im ``ToolExecutor`` selbst. Beide rufen
``geschuetzte_kategorie`` auf, damit die Regel nicht an zwei Stellen gepflegt
wird und auseinanderlaeuft (bis v1.365.5 galt sie nur im Hook).

Ein Schreibzugriff braucht statt "Dateien schreiben" (file_write) die Faehigkeit
"Pakete / Config" (system_config), wenn er
  1. die Konfiguration einer Laufzeit trifft, ueber die sich Befehle starten
     lassen (``_LAUFZEIT_KONFIG``), oder
  2. ausserhalb der Arbeitsbereiche liegt (``_ARBEITSBEREICHE``).
"""
from __future__ import annotations

import os
import re

# Konfiguration der Laufzeit selbst — ALLES unter .claude/ und .codex/ sowie
# .mcp.json: dort stehen Hooks (bei Claude Code auch genau dieser Gate-Hook,
# /workspace/.claude/settings.json), Codex-Konfiguration und -Hooks (live
# geprueft: Codex fuehrt /workspace/.codex/hooks.json aus) und Projekt-MCP-Server
# (.mcp.json startet Programme). Auch Skills, Befehle und Subagenten unter
# .claude/ zaehlen dazu: ihr Kopf kann eigene Hooks tragen, Befehle koennen per
# !`…` Shell ausfuehren. Wer dort schreiben darf, kann das Gate umgehen.
# Schreiben dort ist deshalb "Pakete / Config" (system_config). Den sicheren Weg
# fuer eigene Skills bietet create_skill (Datenbank, Sicherheitspruefung).
# Dazu ~/.claude.json: die Benutzerkonfiguration von Claude Code traegt dort
# ebenfalls "mcpServers" (Programme, die beim Start laufen) — liegt aber NICHT
# unter .claude/, sondern daneben (im Agenten-Container nachgesehen).
# Und Git: core.fsmonitor in .git/config bzw. ~/.gitconfig startet bei JEDEM
# `git status` ein Programm (Claude Code ruft es beim Sitzungsstart selbst auf,
# git_status ist immer erlaubt), .git/hooks/* laufen bei Git-Befehlen.
_LAUFZEIT_KONFIG = re.compile(
    r"(^|[\\/])("
    r"\.(claude|codex)([\\/]|$)"
    r"|\.mcp\.json$|\.claude\.json$"
    r"|\.gitconfig$|\.git[\\/](config$|hooks([\\/]|$))"
    r")"
)
_PATCH_ZIEL = re.compile(r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+)$", re.M)
_SCHREIBWERKZEUGE = frozenset({
    "Write", "Edit", "MultiEdit", "NotebookEdit", "apply_patch",
    "write_file", "edit_file", "multi_edit",
})


# Wo ein Agent mit "Dateien schreiben" schreiben darf: sein Arbeitsordner, der
# gemeinsame Ordner, eingebundene Ordner (Brains, Freigaben) und /tmp. ALLES
# andere — Home-Verzeichnis (Shell-Konfiguration, die Claude Code beim
# Sitzungsstart einliest), site-packages (.pth/sitecustomize laufen in jedem
# Python), Systempfade — ist Konfiguration der Umgebung und braucht
# system_config. Bewusst eine Erlaubnis-Liste: eine Sperrliste ausfuehrbarer
# Orte wird nie vollstaendig.
_ARBEITSBEREICHE = ("/workspace", "/shared", "/mnt", "/tmp")


def _aufgeloest(pfad: str) -> str:
    """Absoluter, kanonischer Pfad — ``..`` und vorhandene Symlinks aufgeloest,
    damit ``/workspace/../home/...`` oder ein Link nicht am Pruefen vorbeifuehrt."""
    p = os.path.expanduser(pfad)
    if not os.path.isabs(p):
        p = os.path.join(os.environ.get("WORKSPACE_DIR", "/workspace"), p)
    return os.path.realpath(p)


def _ausserhalb_der_arbeitsbereiche(aufgeloest: str) -> bool:
    wurzeln = {w for w in _ARBEITSBEREICHE} | {os.path.realpath(w) for w in _ARBEITSBEREICHE}
    return not any(aufgeloest == w or aufgeloest.startswith(w.rstrip("/") + "/") for w in wurzeln)


def _schreibziele(tool_name: str, tool_input: dict | None) -> list[str]:
    """Pfade, die ein Schreibwerkzeug anfassen wuerde."""
    eingabe = tool_input or {}
    if tool_name == "apply_patch":
        return [z.strip() for z in _PATCH_ZIEL.findall(str(eingabe.get("command") or eingabe.get("input") or ""))]
    return [str(eingabe[k]) for k in ("file_path", "notebook_path", "path") if eingabe.get(k)]


def _bare(tool_name: str) -> str:
    if tool_name.startswith("mcp__"):
        teile = tool_name.split("__", 2)
        return teile[2] if len(teile) == 3 else ""
    return tool_name


def geschuetzte_kategorie(tool_name: str, tool_input: dict | None) -> tuple[str, str] | None:
    """``("system_config", Grund)``, wenn dieser Schreibzugriff mehr braucht als
    "Dateien schreiben" — sonst ``None`` (auch fuer Nicht-Schreibwerkzeuge)."""
    name = _bare(tool_name)
    if name not in _SCHREIBWERKZEUGE:
        return None
    for ziel in _schreibziele(name, tool_input):
        echt = _aufgeloest(ziel)
        if _LAUFZEIT_KONFIG.search(ziel) or _LAUFZEIT_KONFIG.search(echt):
            return "system_config", "Laufzeit-Konfiguration"
        if _ausserhalb_der_arbeitsbereiche(echt):
            return "system_config", "außerhalb der Arbeitsbereiche"
    return None
