"""Conditional Skill Activation — issue #37.

Skills in /workspace/.claude/skills/ can declare optional path-activation
via frontmatter in their SKILL.md file. Before each task runs, this
module inspects the task's working directory and the current file
context, then moves non-matching skills to /workspace/.claude/skills_disabled/
so Claude Code's automatic skill loader only sees the relevant ones.

Frontmatter format (YAML block at top of SKILL.md):

    ---
    name: database-migrations
    paths:
      - "**/alembic/**"
      - "**/migrations/**"
      - "**/models/*.py"
    ---

If the `paths` list is missing or empty, the skill is considered GLOBAL
and always loaded.

The activation is additive: each task starts from a clean slate (all
skills restored), then the filter runs again. This avoids stale state
between tasks.

Call sites:
  - agent/app/main.py runs `activate_skills_for(task_prompt, working_dir)`
    just before invoking the Claude Code CLI for each task.
  - `deactivate_all()` is called at agent shutdown to restore state.
"""

from __future__ import annotations

import fnmatch
import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

SKILLS_DIR = "/workspace/.claude/skills"
DISABLED_DIR = "/workspace/.claude/skills_disabled"


def _read_frontmatter(skill_md_path: Path) -> dict:
    """Return the parsed YAML frontmatter dict. Empty dict on any error.

    We don't depend on pyyaml — this is a tiny hand-parser that handles
    the simple key/value + list cases used in skill frontmatter.
    """
    try:
        content = skill_md_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return {}

    if not content.startswith("---"):
        return {}
    end = content.find("\n---", 3)
    if end < 0:
        return {}
    block = content[3:end].strip("\n")

    result: dict = {}
    current_list_key: str | None = None
    for raw in block.splitlines():
        if not raw.strip():
            continue
        if raw.startswith("  - ") and current_list_key:
            val = raw[4:].strip().strip('"').strip("'")
            result[current_list_key].append(val)
            continue
        if ":" in raw:
            key, _, value = raw.partition(":")
            key = key.strip()
            value = value.strip()
            if value:
                result[key] = value.strip('"').strip("'")
                current_list_key = None
            else:
                result[key] = []
                current_list_key = key
    return result


def _list_skills() -> list[Path]:
    """Return all skill directories currently in the skills folder."""
    base = Path(SKILLS_DIR)
    if not base.is_dir():
        return []
    return [p for p in base.iterdir() if p.is_dir() and not p.name.startswith(".")]


def _skill_matches(skill_dir: Path, context_paths: list[str]) -> bool:
    """Decide whether a skill should be active for the given context.

    context_paths is a list of path-like strings (cwd, referenced files,
    file snippets from the prompt). A skill matches when any of its
    declared glob patterns matches any context path.

    Skills without a `paths` declaration are always considered matching.
    """
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return True  # conservative: load unknown skills
    meta = _read_frontmatter(skill_md)
    patterns = meta.get("paths") or []
    if not patterns:
        return True
    for p in patterns:
        for ctx in context_paths:
            if fnmatch.fnmatch(ctx, p):
                return True
    return False


def _extract_context_paths(task_prompt: str, working_dir: str) -> list[str]:
    """Build a list of path-like strings from the task prompt."""
    paths: list[str] = [working_dir, f"{working_dir}/"]
    # Add cwd walk (one level deep)
    try:
        for entry in os.listdir(working_dir):
            paths.append(f"{working_dir}/{entry}")
    except Exception:
        pass
    # Extract any path-looking substrings from the prompt
    import re
    for m in re.finditer(r"[\w./-]+\.[a-zA-Z0-9]{1,6}", task_prompt):
        paths.append(m.group(0))
    return paths


def activate_skills_for(task_prompt: str, working_dir: str = "/workspace") -> dict:
    """Apply conditional activation. Returns a summary dict."""
    deactivate_all()  # always start clean

    context = _extract_context_paths(task_prompt, working_dir)
    skills = _list_skills()

    kept: list[str] = []
    hidden: list[str] = []

    os.makedirs(DISABLED_DIR, exist_ok=True)
    for skill_dir in skills:
        if _skill_matches(skill_dir, context):
            kept.append(skill_dir.name)
        else:
            target = Path(DISABLED_DIR) / skill_dir.name
            try:
                if target.exists():
                    shutil.rmtree(target)
                shutil.move(str(skill_dir), str(target))
                hidden.append(skill_dir.name)
            except Exception as e:
                logger.warning(f"[ConditionalSkills] could not move {skill_dir.name}: {e}")
                kept.append(skill_dir.name)

    logger.info(
        f"[ConditionalSkills] task-scoped skills: {len(kept)} active, "
        f"{len(hidden)} hidden (hidden: {hidden})"
    )
    return {"active": kept, "hidden": hidden}


def deactivate_all() -> None:
    """Restore every disabled skill back into the active folder."""
    disabled = Path(DISABLED_DIR)
    if not disabled.is_dir():
        return
    os.makedirs(SKILLS_DIR, exist_ok=True)
    for entry in disabled.iterdir():
        if not entry.is_dir():
            continue
        target = Path(SKILLS_DIR) / entry.name
        try:
            if target.exists():
                # Target already has this skill — drop the disabled copy.
                shutil.rmtree(entry)
                continue
            shutil.move(str(entry), str(target))
        except Exception as e:
            logger.warning(f"[ConditionalSkills] restore failed for {entry.name}: {e}")
