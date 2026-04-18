"""
Skills Marketplace Loader
=========================
Auto-discovers and loads skills from the agent/skills/ directory.
Each skill is a self-contained directory with skill.json + tools.py.

Adding a skill = adding a directory. No core code changes needed.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import sys
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)

# Registry: tool_name → async callable
_TOOL_REGISTRY: dict[str, Callable[[dict], Coroutine[Any, Any, str]]] = {}

# All tool definitions in OpenAI format, collected from skills
_SKILL_TOOL_DEFINITIONS: list[dict] = []

# Root of the skills directory (relative to this file)
_SKILLS_DIR = Path(__file__).parent.parent / "skills"


def skill_tool(
    name: str,
    description: str,
    parameters: dict,
) -> Callable:
    """
    Decorator that registers a function as an agent tool.

    Usage::

        @skill_tool(
            name="analyze_spreadsheet",
            description="...",
            parameters={"type": "object", "properties": {...}, "required": [...]}
        )
        async def analyze_spreadsheet(params: dict) -> str:
            ...
    """
    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        async def wrapper(params: dict) -> str:
            return await fn(params)

        # Store metadata for later registration
        wrapper._skill_tool_name = name  # type: ignore[attr-defined]
        wrapper._skill_tool_description = description  # type: ignore[attr-defined]
        wrapper._skill_tool_parameters = parameters  # type: ignore[attr-defined]
        return wrapper

    return decorator


def _load_skill(skill_dir: Path) -> tuple[list[dict], dict[str, Callable]]:
    """Load a single skill directory. Returns (tool_definitions, tool_registry)."""
    manifest_path = skill_dir / "skill.json"
    tools_path = skill_dir / "tools.py"

    if not manifest_path.exists():
        logger.warning("Skill %s missing skill.json — skipped", skill_dir.name)
        return [], {}

    if not tools_path.exists():
        logger.warning("Skill %s missing tools.py — skipped", skill_dir.name)
        return [], {}

    # Load manifest
    try:
        manifest = json.loads(manifest_path.read_text())
    except Exception as exc:
        logger.error("Skill %s: invalid skill.json — %s", skill_dir.name, exc)
        return [], {}

    if not manifest.get("enabled", True):
        logger.info("Skill %s is disabled — skipped", skill_dir.name)
        return [], {}

    # Dynamically import tools.py
    module_name = f"skills.{skill_dir.name}.tools"
    spec = importlib.util.spec_from_file_location(module_name, tools_path)
    if spec is None or spec.loader is None:
        logger.error("Skill %s: could not create module spec", skill_dir.name)
        return [], {}

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module

    try:
        spec.loader.exec_module(module)  # type: ignore[union-attr]
    except Exception as exc:
        logger.error("Skill %s: error importing tools.py — %s", skill_dir.name, exc)
        return [], {}

    # Collect decorated tool functions
    definitions: list[dict] = []
    registry: dict[str, Callable] = {}

    for attr_name in dir(module):
        fn = getattr(module, attr_name)
        if not callable(fn):
            continue
        tool_name = getattr(fn, "_skill_tool_name", None)
        if tool_name is None:
            continue

        definitions.append({
            "type": "function",
            "function": {
                "name": tool_name,
                "description": fn._skill_tool_description,
                "parameters": fn._skill_tool_parameters,
            },
        })
        registry[tool_name] = fn
        logger.debug("Skill %s: registered tool '%s'", skill_dir.name, tool_name)

    skill_id = manifest.get("id", skill_dir.name)
    tool_names = [d["function"]["name"] for d in definitions]
    logger.info(
        "Loaded skill '%s' v%s — tools: %s",
        skill_id,
        manifest.get("version", "?"),
        ", ".join(tool_names) or "(none)",
    )
    return definitions, registry


def load_all_skills() -> None:
    """
    Discover and load all skills from the skills/ directory.
    Populates _SKILL_TOOL_DEFINITIONS and _TOOL_REGISTRY.
    Call once at agent startup.
    """
    global _SKILL_TOOL_DEFINITIONS, _TOOL_REGISTRY  # noqa: PLW0603

    if not _SKILLS_DIR.exists():
        logger.info("No skills directory found at %s", _SKILLS_DIR)
        return

    all_definitions: list[dict] = []
    all_registry: dict[str, Callable] = {}

    for skill_dir in sorted(_SKILLS_DIR.iterdir()):
        if not skill_dir.is_dir() or skill_dir.name.startswith((".", "_")):
            continue
        defs, reg = _load_skill(skill_dir)
        all_definitions.extend(defs)
        all_registry.update(reg)

    _SKILL_TOOL_DEFINITIONS = all_definitions
    _TOOL_REGISTRY = all_registry
    logger.info(
        "Skills marketplace: %d skill(s) loaded, %d tool(s) registered",
        sum(1 for d in _SKILLS_DIR.iterdir() if d.is_dir() and not d.name.startswith((".", "_"))),
        len(all_registry),
    )


def get_skill_tool_definitions() -> list[dict]:
    """Return all tool definitions from loaded skills."""
    return list(_SKILL_TOOL_DEFINITIONS)


def get_skill_tool_names() -> set[str]:
    """Return the set of all registered skill tool names."""
    return set(_TOOL_REGISTRY.keys())


async def execute_skill_tool(tool_name: str, params: dict) -> str:
    """Execute a skill tool by name. Raises KeyError if not found."""
    fn = _TOOL_REGISTRY.get(tool_name)
    if fn is None:
        raise KeyError(f"Skill tool '{tool_name}' not registered")
    return await fn(params)


def list_skills() -> list[dict]:
    """Return manifest info for all discovered skills (for catalog/status)."""
    result = []
    if not _SKILLS_DIR.exists():
        return result
    for skill_dir in sorted(_SKILLS_DIR.iterdir()):
        if not skill_dir.is_dir() or skill_dir.name.startswith((".", "_")):
            continue
        manifest_path = skill_dir / "skill.json"
        if not manifest_path.exists():
            continue
        try:
            manifest = json.loads(manifest_path.read_text())
            result.append(manifest)
        except Exception:
            pass
    return result
