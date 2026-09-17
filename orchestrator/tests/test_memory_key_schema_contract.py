"""Der Reflexions-Prompt verspricht den Agenten eine Einteilung — die Tabelle muss sie halten.

`runner_hooks.py` nennt den Agenten einen festen Satz von Schluesseln und sagt je Schluessel
dazu, ob er einfach ("auto-supersedes the old one") oder mehrfach ist. Steht so ein Schluessel
nicht in `KEY_SCHEMA`, faellt `classify_key` still auf "multi" zurueck — der Prompt sagt dann
etwas anderes als der Server tut, und niemand merkt es.

Genau das war bei `current_task` der Fall: angekuendigt als einfach, in Wahrheit mehrfach.
Bei einem Agenten im Betrieb standen am 07.09.2026 dadurch 1.327 current_task-Zeilen (519
aktiv, ueber 205 Raeume) statt einer je Raum.
"""
import pytest

from app.core.memory_key_schema import KEY_SCHEMA, classify_key, is_run_state_key

#: Der kanonische Satz aus dem Reflexions-Prompt, mit der dort zugesagten Art.
#: Aendert sich der Prompt, gehoert diese Liste mitgeaendert — dann faellt beim
#: naechsten Lauf auf, wenn die Tabelle nicht nachgezogen wurde.
PROMPT_KEYS = {
    "code_pattern": "multi",
    "lesson_learned": "multi",
    "anti_pattern": "multi",
    "decision_rationale": "multi",
    "capability_gained": "multi",
    "working_pipeline": "multi",
    "current_task": "single",
}


def test_current_task_supersedes_like_the_prompt_promises():
    assert classify_key("current_task") == "single"


@pytest.mark.parametrize("key", sorted(PROMPT_KEYS))
def test_prompt_key_is_listed_in_the_table(key):
    # Ein Schluessel, den der Prompt nennt, darf nicht nur zufaellig ueber den
    # "multi"-Rueckfall richtig liegen — er gehoert in die Tabelle.
    assert key in KEY_SCHEMA


@pytest.mark.parametrize("key,kind", sorted(PROMPT_KEYS.items()))
def test_prompt_and_table_agree(key, kind):
    assert classify_key(key) == kind


def test_unknown_key_still_defaults_to_multi():
    # Der Rueckfall bleibt die nicht-zerstoerende Wahl.
    assert classify_key("voellig_unbekannter_schluessel") == "multi"


# --- Issue #716: run-state exemption from the hybrid-mode approval gate ----

@pytest.mark.parametrize("key", ["current_task", "current_task_id", "current_mode"])
def test_run_state_keys_are_flagged(key):
    assert is_run_state_key(key)
    # Ein Laufzustand-Schluessel ist immer auch "single" — die Ausnahme in
    # save_memory_core greift nur im single-Zweig.
    assert classify_key(key) == "single"


@pytest.mark.parametrize("key", ["current_goal", "assigned_agent_type", "preferred_style"])
def test_other_single_keys_are_not_run_state(key):
    # Diese Schluessel sind ebenfalls "single", tragen aber eine echte
    # Entscheidung — eine Ablosung soll im Hybridmodus weiterhin Freigabe
    # brauchen, deshalb duerfen sie NICHT in RUN_STATE_KEYS landen.
    assert classify_key(key) == "single"
    assert not is_run_state_key(key)


def test_unknown_key_is_not_run_state():
    assert not is_run_state_key("voellig_unbekannter_schluessel")
