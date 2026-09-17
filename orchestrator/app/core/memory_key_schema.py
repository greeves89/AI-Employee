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
    # "current_task" fehlte hier, obwohl der Reflexions-Prompt es den Agenten seit jeher
    # als „single-value — auto-supersedes the old one" ankuendigt und der Kopf dieser Datei
    # „current task" als Musterfall nennt. Unbekannte Schluessel fallen auf "multi" zurueck,
    # also loeste nie etwas ab: bei einem Agenten im Betrieb lagen am 07.09.2026 1.327
    # current_task-Zeilen (519 davon aktiv, ueber 205 Raeume) — der mit Abstand groesste
    # Schluessel seines Gedaechtnisses und ein Treiber des ueberlaufenden Preloads.
    "current_task": "single",
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
    # Vom Reflexions-Prompt als Mehrfach-Schluessel angekuendigt. Sie standen bisher nur
    # nicht in der Tabelle und lagen allein durch den "multi"-Rueckfall richtig — was das
    # Versprechen dieser Datei, einzige Quelle der Wahrheit zu sein, stillschweigend brach.
    "capability_gained": "multi",
    "working_pipeline": "multi",

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


# Issue #716: single-value keys that carry pure RUN STATE — what an agent is
# doing right now — rather than a fact or preference a human might want to
# review. Deliberately a narrow subset of the "single" keys above: "current_task"
# is the motivating case (single-value only since this same release), and
# "current_task_id"/"current_mode" are the same kind of transient status.
# "current_goal", "assigned_agent_type", "preferred_style" etc. stay OUT of
# this set on purpose — those represent an actual decision, and a hybrid-mode
# agent superseding one is exactly the kind of change that should still need
# approval.
RUN_STATE_KEYS: set[str] = {"current_task", "current_task_id", "current_mode"}


def is_run_state_key(key: str) -> bool:
    """Whether ``key`` tracks pure run status rather than durable knowledge.

    Used by ``save_memory_core`` to exempt these keys from the hybrid-mode
    approval gate: every run superseding its own previous ``current_task`` is
    not a conflict a human should vote on, it is simply the agent moving on.
    """
    return key in RUN_STATE_KEYS


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
