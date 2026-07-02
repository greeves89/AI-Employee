"""Plateau detection for the A/B skill-improvement loop (issue #210).

The improvement engine keeps rewriting low-rated skills, but real loops get
stuck at a mediocre rating (e.g. daily-standup skill: 6 rewrites all at 2.4/5).
The engine never noticed the rewrites were ineffective and kept changing the
*same* kind of thing (more rules, more examples, more templates).

This module is pure, dependency-light detection logic so it can be unit-tested
without the DB/LLM/redis import chain. The engine owns persistence + alerting;
this module owns the "are we plateaued, and what should we try instead?"
decision.
"""

# Improvement dimensions we recognise. Split into two opposing clusters: piling
# on more "control" (structure/rules/examples) vs. shifting to "expressive"
# levers (tone, audience adaptation, brevity). The observed failure mode is
# hammering the control cluster forever, so once that is exhausted we steer the
# next attempt to the expressive cluster.
_CONTROL_DIMENSIONS = ("examples", "rules", "templates", "structure", "length")
_EXPRESSIVE_DIMENSIONS = ("tone", "audience_adaptation", "brevity", "data_sources")
ALL_DIMENSIONS = _CONTROL_DIMENSIONS + _EXPRESSIVE_DIMENSIONS

# A rating gain smaller than this over the plateau window counts as "no
# significant improvement".
_MIN_IMPROVEMENT_DELTA = 0.2
# Number of consecutive versions inspected for a plateau.
_PLATEAU_WINDOW = 3


def is_plateaued(
    helpfulness_history: list[float],
    window: int = _PLATEAU_WINDOW,
    min_delta: float = _MIN_IMPROVEMENT_DELTA,
) -> bool:
    """True when the last `window` versions produced no net improvement.

    `helpfulness_history` is the chronological list of a skill's per-version
    avg helpfulness. We look at the most recent `window` snapshots and treat it
    as a plateau when the net gain (newest − oldest in the window) is below
    `min_delta`. Regressions (negative gain) also count — they are not
    improvements either.
    """
    clean = [h for h in helpfulness_history if h is not None]
    if len(clean) < window:
        return False
    recent = clean[-window:]
    return (recent[-1] - recent[0]) < min_delta


def is_single_dimension_change(dimensions: list[str]) -> bool:
    """Enforce the single-change constraint: a version may touch ≤1 dimension.

    Multivariate rewrites make it impossible to attribute a rating shift to a
    specific change, so each new version should vary exactly one dimension.
    """
    return len({d for d in dimensions if d}) <= 1


def recommend_alternative_strategy(changed_dimensions: list[str]) -> dict:
    """Suggest a different dimension to try after a plateau.

    Given the dimensions that were changed across the plateaued versions, pick
    an untried dimension — and if the loop has only ever pushed the "control"
    cluster, explicitly steer it to the opposing "expressive" cluster (the
    "try LESS control" lesson).
    """
    tried = {d for d in changed_dimensions if d}
    hammered_control = bool(tried & set(_CONTROL_DIMENSIONS)) and not (
        tried & set(_EXPRESSIVE_DIMENSIONS)
    )

    if hammered_control:
        candidates = [d for d in _EXPRESSIVE_DIMENSIONS if d not in tried]
        message = (
            "Die Kontroll-Dimensionen ("
            + ", ".join(sorted(tried & set(_CONTROL_DIMENSIONS)))
            + ") wurden mehrfach erfolglos geändert. Probiere das Gegenteil: "
            "weniger Struktur/Regeln, mehr Tonalität und Zielgruppen-Anpassung."
        )
    else:
        candidates = [d for d in ALL_DIMENSIONS if d not in tried]
        message = (
            "Die bisher geänderten Dimensionen ("
            + (", ".join(sorted(tried)) or "—")
            + ") zeigen keinen Effekt. Teste eine bislang unveränderte Dimension."
        )

    if not candidates:
        candidates = [d for d in ALL_DIMENSIONS if d not in tried] or list(ALL_DIMENSIONS)

    return {
        "tried": sorted(tried),
        "recommended_dimension": candidates[0],
        "candidates": candidates,
        "message": message,
        # The engine must apply this as a single-dimension change.
        "single_dimension_constraint": True,
    }
