"""Ein Startaufbau fuer alle Custom-LLM-Wege (#161).

Vorher baute LLMRunner seinen Prompt selbst und LLMChatHandler einen zweiten. Der
Doppelgaenger unten liefert je Baustein eine eigene Marke, damit fehlende oder
vertauschte Bausteine auffallen: ein Doppelgaenger mit leerem Text koennte beides nicht.
"""

import re
from pathlib import Path
from unittest.mock import patch

import pytest

from app import runner_hooks
from app.runner_hooks import compose_custom_llm_start

APP = Path(__file__).resolve().parents[1] / "app"
BAUSTEINE = (
    "get_identity_context", "get_approval_rules_prefix", "get_mounts_context",
    "get_user_feedback", "get_improvement_context", "get_skills_context",
    "get_onboarding_context", "get_disk_incident_context",
)


@pytest.fixture
def marken():
    p = [patch(f"app.runner_hooks.{n}", return_value=f"<{n}>") for n in BAUSTEINE]
    p.append(patch("app.runner_hooks.get_memory_preload", lambda a: f"<memory:{a}>"))
    p.append(patch("app.runner_hooks.get_skill_preload", lambda a="": f"<fach:{a}>"))
    p.append(patch("app.runner_hooks.get_marketplace_skill_suggestions", lambda a: f"<markt:{a}>"))
    p.append(patch("app.runner_hooks.zeitkontext", lambda: "<zeit>"))
    p.append(patch.object(runner_hooks.settings, "llm_system_prompt", ""))
    for x in p:
        x.start()
    yield
    for x in p:
        x.stop()


GEMEINSAM_SYSTEM = ("get_identity_context", "get_mounts_context", "get_user_feedback",
                    "get_skills_context")


@pytest.mark.parametrize("art", ["auftrag", "kurzlauf", "chat"])
def test_jeder_weg_bekommt_die_gemeinsamen_bausteine(marken, art):
    start = compose_custom_llm_start("Bericht", art=art, task_id="t1")
    alles = start.system + start.vor_auftrag
    for baustein in GEMEINSAM_SYSTEM:
        assert f"<{baustein}>" in start.system, baustein
    for marke in ("<memory:Bericht>", "<markt:Bericht>", "<get_onboarding_context>",
                  "<get_disk_incident_context>"):
        assert marke in alles, marke
    assert "TOOL USAGE RULES" in start.system
    assert "search_tools" in start.system
    assert runner_hooks.MULTIMODAL_CAPABILITY_NOTE in start.system


def test_voller_auftrag(marken):
    start = compose_custom_llm_start("Bericht", art="auftrag", task_id="t1")
    assert start.vor_auftrag.startswith("CURRENT_TASK_ID: t1\n\n")
    assert runner_hooks.TASK_STARTUP_PREFIX in start.vor_auftrag
    assert "<fach:Bericht>" in start.vor_auftrag
    assert "<get_approval_rules_prefix>" in start.system
    assert "<get_improvement_context>" in start.system
    assert start.nach_auftrag == runner_hooks.SELF_IMPROVEMENT_SUFFIX
    assert start.vor_auftrag.endswith("<zeit>\n\n")


def test_kurzlauf_ohne_regeln_und_ohne_abschlusspflicht(marken):
    start = compose_custom_llm_start("Hallo", art="kurzlauf", task_id="t2")
    assert "CURRENT_TASK_ID: t2" in start.vor_auftrag
    assert runner_hooks.CHAT_STARTUP_PREFIX in start.vor_auftrag
    assert "<get_approval_rules_prefix>" not in start.system
    assert "<get_improvement_context>" not in start.system
    assert start.nach_auftrag == ""


def test_chat_laesst_dem_consumer_was_er_je_nachricht_setzt(marken):
    """Regeln, Fachanleitungen und Zeit kommen im Chat vor JEDE Nachricht
    (ChatConsumer._prepare_text); im Systemprompt waeren sie doppelt und veraltet."""
    start = compose_custom_llm_start("Hallo", art="chat")
    assert (start.vor_auftrag, start.nach_auftrag) == ("", "")
    for fremd in ("<get_approval_rules_prefix>", "<fach:", "<zeit>", "CURRENT_TASK_ID",
                  runner_hooks.CHAT_STARTUP_PREFIX):
        assert fremd not in start.system


def test_systemprompt_haengt_nicht_am_wechselnden_teil(marken):
    """Sonst greift der Prompt-Cache des Anbieters nie."""
    start = compose_custom_llm_start("Bericht", art="auftrag", task_id="t1")
    for wechselnd in ("<zeit>", "t1", "<fach:", "<markt:", "<get_disk_incident_context>"):
        assert wechselnd not in start.system


def test_eigene_vorgabe_des_betreibers_gewinnt(marken):
    with patch.object(runner_hooks.settings, "llm_system_prompt", "Eigene Rolle"):
        assert compose_custom_llm_start("x", art="chat").system.startswith("Eigene Rolle")


def test_unbekannte_art_faellt_auf():
    with pytest.raises(ValueError):
        compose_custom_llm_start("x", art="irgendwas")


def test_jeder_baustein_des_cli_buendels_ist_auch_hier():
    """Kommt ins CLI-Buendel ein neuer Baustein, muss Custom-LLM ihn auch bekommen."""
    quelle = (APP / "runner_hooks.py").read_text()
    def aufrufe(name):
        rumpf = re.search(rf"def {name}\(.*?(?=\n(?:def|class|@|[A-Z_]+ =) )", quelle, re.S).group(0)
        return set(re.findall(r"\b(get_\w+)\(", rumpf))

    assert aufrufe("compose_prompt_bundle") - aufrufe("compose_custom_llm_start") == set()


@pytest.mark.parametrize("datei", ["llm_runner.py", "llm_chat_handler.py"])
def test_kein_weg_baut_sich_seinen_prompt_selbst(datei):
    quelle = (APP / datei).read_text()
    assert "compose_custom_llm_start" in quelle
    for name in ("get_memory_preload", "get_mounts_context", "get_identity_context",
                 "get_skills_context"):
        assert name not in quelle, f"{datei} setzt {name} wieder selbst ein"
