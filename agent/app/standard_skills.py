"""Mitgelieferte Standard-Skills aus dem Abbild in den Skill-Ordner des Agenten legen.

Quelle ist ``/opt/skills-standard/<name>/`` (im Repo ``agent/skills_standard/``,
root-eigen im Abbild): geprüfte Kopien mit festem Stand, ganze Pakete samt Kit und
Skripten, ohne Netz beim Start. Jede Laufzeit bekommt sie dort, wo sie Skills sucht —
Claude Code in ``.claude/skills``, Codex in ``.agents/skills`` (den liest auch der
Skill-Index für eigene Modelle, ``runner_hooks``).

Nachgezogen wird nur, was von hier stammt (Marke ``.standard-stand`` im Ziel) und dessen
Stand im Abbild sich geändert hat. Ein gleichnamiger Skill anderer Herkunft (vom Agenten
oder Nutzer angelegt) bleibt unangetastet — wie bei ``core/mitgelieferte_skills`` im
Orchestrator.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

QUELLE = Path("/opt/skills-standard")
#: Skill-Ordner je Laufzeit, relativ zum Arbeitsbereich.
ZIELE = {"claude_code": ".claude/skills", "codex_cli": ".agents/skills", "custom_llm": ".agents/skills"}
MARKE = ".standard-stand"


def _stand(ordner: Path) -> str:
    try:
        return (ordner / "STAND").read_text().strip()
    except OSError:
        return ""


def bereitstellen(laufzeit: str, *, quelle: Path = QUELLE, workspace: Path | None = None) -> list[str]:
    """Standard-Skills für ``laufzeit`` bereitstellen. Gibt die neu gelegten Namen zurück."""
    if workspace is None:
        from app.config import settings
        workspace = Path(settings.workspace_dir)
    ziel_ordner = workspace / ZIELE.get(laufzeit, ZIELE["claude_code"])
    if not quelle.is_dir():
        return []
    gelegt: list[str] = []
    for skill in sorted(p for p in quelle.iterdir() if p.is_dir() and (p / "SKILL.md").is_file()):
        stand = _stand(skill) or "ohne-stand"
        ziel = ziel_ordner / skill.name
        if ziel.is_symlink():
            print(f"[Agent] Standard-Skill {skill.name}: Ziel ist ein Verweis — nicht angefasst")
            continue
        if ziel.exists():
            try:
                bisher = (ziel / MARKE).read_text().strip()
            except OSError:
                continue  # eigener Skill gleichen Namens: nie überschreiben
            if bisher == stand:
                continue
            shutil.rmtree(ziel)
        try:
            ziel_ordner.mkdir(parents=True, exist_ok=True)
            # symlinks=True: ein Verweis im Paket bleibt Verweis, statt Fremdes mitzukopieren
            shutil.copytree(skill, ziel, symlinks=True)
            (ziel / MARKE).write_text(stand + "\n")
            os.chmod(ziel, 0o700)  # nur der Agent selbst
            gelegt.append(skill.name)
        except OSError as e:
            print(f"[Agent] Standard-Skill {skill.name} nicht bereitgestellt: {e}")
    return gelegt
