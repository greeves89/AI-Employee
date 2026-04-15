"""Memory Key Schema — central classification of memory keys.

Addresses issue #24 (the "single vs multi" routing pattern lifted from
@m13v's ai-browser-profile, adapted for our multi-agent use case).

A memory key is either:
  - "single": a new value for this key SUPERSEDES the old one. Used for
    state that has exactly one current value per agent (e.g. current
    goal, current task, assigned agent type).
  - "multi": many values for the same key can COEXIST. Used for lists
    of observations (touched files, learned patterns, referenced urls).

Unknown keys default to "multi" — it's the safe choice because it never
accidentally overwrites information.

The table below is the single source of truth. Do not inline these
classifications in memory_save.
"""

from __future__ import annotations

from typing import Literal

KeyKind = Literal["single", "multi"]

KEY_SCHEMA: dict[str, KeyKind] = {
    # --- Agent state / identity (single-value) ---
    "current_goal": "single",
    "current_task_id": "single",
    "current_mode": "single",
    "assigned_agent_type": "single",
    "primary_language": "single",
    "working_directory": "single",

    # --- Learned patterns / observations (multi-value) ---
    "code_pattern": "multi",
    "approach_used": "multi",
    "tool_preference": "multi",
    "anti_pattern": "multi",
    "lesson_learned": "multi",
    "decision_rationale": "multi",

    # --- Project context (multi-value) ---
    "touched_file": "multi",
    "referenced_url": "multi",
    "discovered_issue": "multi",
    "related_project": "multi",
    "known_dependency": "multi",

    # --- Relationships (multi-value) ---
    "collaborates_with": "multi",
    "depends_on_agent": "multi",
    "mentored_by": "multi",

    # --- User preferences (single) ---
    "preferred_style": "single",
    "preferred_model": "single",
    "preferred_language": "single",
}


def classify_key(key: str) -> KeyKind:
    """Return 'single' or 'multi' for the given key. Defaults to 'multi'
    for unknown keys — it's the non-destructive choice.
    """
    return KEY_SCHEMA.get(key, "multi")


# Canonical tag taxonomy. Agents and the UI should only use these tags;
# any legacy tag gets rewritten via TAG_MIGRATION before insert.
CANONICAL_TAGS: set[str] = {
    "task",
    "code",
    "decision",
    "learning",
    "error",
    "correction",
    "pattern",
    "architecture",
    "performance",
    "security",
    "user_preference",
    "meta",
}

TAG_MIGRATION: dict[str, str] = {
    "bug": "error",
    "issue": "error",
    "fix": "correction",
    "insight": "learning",
    "lesson": "learning",
    "idea": "learning",
    "design": "architecture",
    "perf": "performance",
    "slow": "performance",
    "auth": "security",
    "vuln": "security",
    "preference": "user_preference",
    "pref": "user_preference",
    "config": "meta",
    "setting": "meta",
}


def normalize_tag(tag: str) -> str:
    """Canonicalize a tag via the migration map. Returns the tag
    unchanged if already canonical, or the mapped value otherwise.
    Unknown tags pass through.
    """
    t = tag.strip().lower()
    if t in CANONICAL_TAGS:
        return t
    return TAG_MIGRATION.get(t, t)


# --- Cosine similarity thresholds for dedup ---

# Exact-match dedup: auto-supersede the old memory.
COSINE_HARD_DEDUP = 0.92

# Contradiction warning: return a warning to the caller asking whether
# to merge/supersede. Between 0.88 and 0.92, the caller decides.
COSINE_SOFT_WARN = 0.88


# --- Memory tag_type for decay classification ---

# "transient" memories lose relevance quickly (task state, recent errors).
# Uses exponential decay over 30 days.
TAG_TYPE_TRANSIENT = "transient"

# "permanent" memories are learned patterns that should stay accessible
# for months or years. Uses logarithmic decay.
TAG_TYPE_PERMANENT = "permanent"
