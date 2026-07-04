"""Single source of truth for which models each agent harness (``mode``) runs.

Historically the model lists were hardcoded in three separate frontend files
(create-agent modal, per-agent settings, admin settings) with no backend guard,
so nothing stopped a ``claude_code`` agent from being pointed at a GPT model
(and vice-versa) — exactly the "the claude model is not supported with a ChatGPT
account" failure users hit.

This module is the ONE place that knows the mapping:

    harness (mode)        provider(s)                    model family
    --------------        ---------------------------    ------------
    claude_code           anthropic/bedrock/vertex/…     Claude models
    codex_cli             codex (OpenAI)                 GPT / o-series
    custom_llm            <AI account or llm_config>     anything (not here)

Every gate — POST /agents, PATCH /agents/{id}/model, the container launch in
AgentManager, and the chat model override — validates through
``is_model_allowed_for_mode`` / coerces through ``default_model_for_mode`` so the
rule lives in exactly one spot. The frontend fetches ``catalog_payload`` from
``GET /agents/models`` instead of keeping its own copies.
"""

from __future__ import annotations

# Curated model lists, mirrored from what the UI already offered so nothing
# regresses. Ordered newest-first; the first anthropic/codex entry is the
# natural default for its harness.
_CLAUDE_MODELS: dict[str, list[dict]] = {
    "anthropic": [
        {"value": "claude-opus-4-8", "label": "Opus 4.8 (Latest)", "tier": "Most Powerful"},
        {"value": "claude-sonnet-4-6", "label": "Sonnet 4.6", "tier": "Balanced"},
        {"value": "claude-haiku-4-5", "label": "Haiku 4.5", "tier": "Fast"},
        {"value": "claude-opus-4-7", "label": "Opus 4.7", "tier": "Legacy"},
        {"value": "claude-opus-4-6", "label": "Opus 4.6", "tier": "Legacy"},
        {"value": "claude-sonnet-4-5", "label": "Sonnet 4.5", "tier": "Legacy"},
    ],
    "bedrock": [
        {"value": "anthropic.claude-opus-4-8", "label": "Opus 4.8 (Latest)", "tier": "Most Powerful"},
        {"value": "anthropic.claude-sonnet-4-6", "label": "Sonnet 4.6", "tier": "Balanced"},
        {"value": "anthropic.claude-haiku-4-5-20251001-v1:0", "label": "Haiku 4.5", "tier": "Fast"},
        {"value": "us.anthropic.claude-opus-4-7-v1:0", "label": "Opus 4.7", "tier": "Legacy"},
        {"value": "anthropic.claude-opus-4-6-v1", "label": "Opus 4.6", "tier": "Legacy"},
        {"value": "anthropic.claude-sonnet-4-5-20250929-v1:0", "label": "Sonnet 4.5", "tier": "Legacy"},
    ],
    "vertex": [
        {"value": "claude-opus-4-8", "label": "Opus 4.8 (Latest)", "tier": "Most Powerful"},
        {"value": "claude-sonnet-4-6", "label": "Sonnet 4.6", "tier": "Balanced"},
        {"value": "claude-haiku-4-5@20251001", "label": "Haiku 4.5", "tier": "Fast"},
        {"value": "claude-opus-4-7", "label": "Opus 4.7", "tier": "Legacy"},
        {"value": "claude-opus-4-6", "label": "Opus 4.6", "tier": "Legacy"},
        {"value": "claude-sonnet-4-5@20250929", "label": "Sonnet 4.5", "tier": "Legacy"},
    ],
    "foundry": [
        {"value": "claude-opus-4-8", "label": "Opus 4.8 (Latest)", "tier": "Most Powerful"},
        {"value": "claude-sonnet-4-6", "label": "Sonnet 4.6", "tier": "Balanced"},
        {"value": "claude-haiku-4-5", "label": "Haiku 4.5", "tier": "Fast"},
        {"value": "claude-opus-4-7", "label": "Opus 4.7", "tier": "Legacy"},
        {"value": "claude-opus-4-6", "label": "Opus 4.6", "tier": "Legacy"},
        {"value": "claude-sonnet-4-5", "label": "Sonnet 4.5", "tier": "Legacy"},
    ],
}

_CODEX_MODELS: dict[str, list[dict]] = {
    "codex": [
        {"value": "gpt-5.5", "label": "GPT-5.5 (Latest)", "tier": "Most Powerful"},
        {"value": "gpt-5.4", "label": "GPT-5.4", "tier": "Balanced"},
    ],
}

# mode -> harness metadata. "default_provider"/"default_model" is what a fresh
# agent of that mode gets when the caller doesn't (or can't) pick one.
MODEL_CATALOG: dict[str, dict] = {
    "claude_code": {
        "label": "Claude Code",
        "providers": _CLAUDE_MODELS,
        "default_provider": "anthropic",
        "default_model": "claude-sonnet-4-6",
    },
    "codex_cli": {
        "label": "Codex CLI",
        "providers": _CODEX_MODELS,
        "default_provider": "codex",
        "default_model": "gpt-5.5",
    },
}

# Modes whose model is validated here. custom_llm is intentionally excluded —
# its model is whatever the linked AI account / inline llm_config provides.
_GUARDED_MODES = frozenset(MODEL_CATALOG.keys())

# Models that classify into a guarded harness by family but cannot actually run
# there. ``gpt-5-codex`` is an API-key-only model; our codex_cli harness always
# authenticates via a ChatGPT/Codex account (see agent/app/codex_runner.py),
# which rejects it at runtime ("The 'gpt-5-codex' model is not supported when
# using Codex with a ChatGPT account"). Treating it as not-allowed makes the
# guard reject it and coercion route it to the harness default instead.
_UNSUPPORTED_FOR_MODE: dict[str, frozenset[str]] = {
    "codex_cli": frozenset({"gpt-5-codex"}),
}


def model_family(model: str | None) -> str | None:
    """Classify a model string by the harness that can execute it.

    Robust to provider prefixes/suffixes (Bedrock ARNs like
    ``anthropic.claude-opus-4-8``, Vertex ``claude-…@date``) — it keys on the
    substring, not an exact match. Returns ``"claude_code"``, ``"codex_cli"`` or
    ``None`` (unknown / custom territory such as gemini/llama).
    """
    m = (model or "").strip().lower()
    if not m:
        return None
    if "claude" in m:
        return "claude_code"
    if "gpt" in m or "codex" in m or m.startswith(("o1", "o3", "o4")):
        return "codex_cli"
    return None


def is_model_allowed_for_mode(mode: str, model: str | None) -> bool:
    """True when ``model`` may run under harness ``mode``.

    ``custom_llm`` accepts any model (the account/config is the authority).
    For the guarded harnesses the model's family must match the mode.
    """
    if mode not in _GUARDED_MODES:
        return True
    fam = model_family(model)
    if fam is None:
        return False
    if (model or "").strip().lower() in _UNSUPPORTED_FOR_MODE.get(mode, frozenset()):
        return False
    return fam == mode


def default_model_for_mode(mode: str) -> str | None:
    """Fallback model for a harness when none/incompatible was supplied."""
    entry = MODEL_CATALOG.get(mode)
    return entry["default_model"] if entry else None


def coerce_model_for_mode(mode: str, model: str | None) -> str | None:
    """Return a model guaranteed valid for ``mode`` (last-line defense).

    Keeps a compatible model as-is; replaces a missing/incompatible one with the
    harness default. Non-guarded modes (custom_llm) are returned untouched.
    """
    if mode not in _GUARDED_MODES:
        return model
    if model and is_model_allowed_for_mode(mode, model):
        return model
    return default_model_for_mode(mode)


def catalog_payload() -> dict:
    """Serialisable catalog for ``GET /agents/models`` — the frontend renders
    provider + model dropdowns straight from this, no hardcoded lists."""
    return {
        "modes": [
            {
                "mode": mode,
                "label": entry["label"],
                "default_provider": entry["default_provider"],
                "default_model": entry["default_model"],
                "providers": [
                    {"provider": prov, "models": models}
                    for prov, models in entry["providers"].items()
                ],
            }
            for mode, entry in MODEL_CATALOG.items()
        ]
    }
