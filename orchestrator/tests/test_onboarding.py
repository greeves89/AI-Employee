"""Einrichtung: eine Wahrheit, aktives Nachfragen, in allen Laufzeiten.

Befund beim Kunden (2026-08-07): Es gab ZWEI Einrichtungsstaende, die sich widersprachen —
``config['onboarding_complete']`` in der DB (beim Anlegen gesetzt, danach nie geaendert) und
die Kopfzeile in ``/workspace/knowledge.md``, die der Agent selbst pflegen sollte. In der DB
stand „fertig", in der Datei „nicht fertig". Die Agenten hielten deshalb jeden proaktiven
Lauf an, waehrend die Oberflaeche sie als eingerichtet zeigte: 493 Laeufe, 51 USD, null
Arbeitsergebnis.
"""

import unittest
from pathlib import Path
from types import SimpleNamespace

from app.core import onboarding as ob

REPO = Path(__file__).resolve().parents[2]
ORCH = REPO / "orchestrator"
AGENT = REPO / "agent"


def _agent(**config):
    return SimpleNamespace(id="a1", name="Testi", config=config)


class StateTests(unittest.TestCase):
    def test_missing_flag_means_not_onboarded(self):
        """Fehlt der Eintrag, gilt NICHT eingerichtet — fail-closed."""
        self.assertFalse(ob.is_onboarded(_agent()))
        self.assertFalse(ob.is_onboarded(None))

    def test_duties_are_read_from_the_proactive_config(self):
        self.assertFalse(ob.has_duties(_agent(proactive={})))
        self.assertTrue(ob.has_duties(_agent(proactive={"responsibilities": [{"title": "X"}]})))

    def test_note_is_empty_only_when_both_are_there(self):
        ready = _agent(onboarding_complete=True, proactive={"responsibilities": [{"title": "X"}]})
        self.assertEqual(ob.onboarding_note(ready), "")
        self.assertNotEqual(ob.onboarding_note(_agent()), "")
        self.assertNotEqual(ob.onboarding_note(_agent(onboarding_complete=True)), "")

    def test_note_asks_for_duties_not_for_a_briefing(self):
        """Das Einrichtungsgespraech ist entfallen (Entscheidung des Nutzers am
        2026-08-13: „onboarding raus, der soll sich an seine Vorlage halten").
        Uebrig bleibt die einzige Luecke, die ihn wirklich lahmlegt."""
        note = ob.onboarding_note(_agent())
        self.assertIn("notify_user", note)
        self.assertIn("complete_onboarding", note)
        self.assertIn("VERANTWORTUNGSBEREICHE", note)

    def test_the_note_no_longer_conducts_an_interview(self):
        """Genau das war der Widerspruch: das Interview war aus knowledge.md
        entfernt, wurde aber vom Zeitplaner weiter eingeblendet."""
        note = ob.onboarding_note(_agent())
        self.assertNotIn("Welche Rolle sollst du ausfuellen", note)
        self.assertNotIn("EINRICHTUNG STEHT AUS", note)
        gesprochen = ob.onboarding_note(_agent(), spoken=True)
        self.assertNotIn("wofuer du mich brauchst", gesprochen)

    def test_spoken_note_is_for_the_phone_not_the_log(self):
        note = ob.onboarding_note(_agent(), spoken=True)
        self.assertIn("Verantwortungsbereiche", note)
        self.assertNotIn("notify_user", note)   # am Telefon meldet man sich nicht per Werkzeug


class NoLeftoverSetupFlagTests(unittest.TestCase):
    """Nachfrage des Nutzers am 2026-08-13: „da wir das onboarding abgestellt
    hatten, wurde das auch im Frontend beruecksichtigt?"

    Teilweise nicht. Das Interview war aus ``knowledge.md`` entfernt, aber der
    Haken ``onboarding_complete`` wurde fuer neue ``claude_code``-Agenten weiter
    auf ``false`` gesetzt — und *nichts* konnte ihn danach noch auf ``true``
    bringen, weil genau das Gespraech ihn setzte. Folge: dauerhaft das Abzeichen
    „nicht eingerichtet" in der Oberflaeche UND uebersprungene proaktive Laeufe.
    """

    def test_new_agents_count_as_set_up(self):
        src = (ORCH / "app/core/agent_manager.py").read_text()
        self.assertIn('"onboarding_complete": True,', src)
        self.assertNotIn('False if mode == "claude_code" else True', src)

    def test_existing_agents_are_normalised_at_startup(self):
        """Ohne das bleibt jeder Bestandsagent auf ``false`` haengen."""
        main = (ORCH / "app/main.py").read_text()
        self.assertIn("'{onboarding_complete}', 'true'::jsonb", main)

    def test_the_normalisation_is_idempotent(self):
        """Es darf nur anfassen, was noch nicht ``true`` ist — sonst schreibt
        jeder Start jede Zeile neu."""
        main = (ORCH / "app/main.py").read_text()
        self.assertIn(
            "WHERE coalesce((config->>'onboarding_complete')::boolean, false) IS NOT TRUE",
            main,
        )

    def test_the_card_no_longer_shows_a_setup_badge(self):
        card = (REPO / "frontend/src/components/dashboard/agent-card.tsx").read_text()
        self.assertNotIn("Nicht eingerichtet", card)
        self.assertNotIn("UserCog", card, "Symbol muss auch aus dem Import raus")

    def test_the_card_still_warns_about_missing_duties(self):
        """Diese Warnung bleibt richtig: ohne Bereiche wird der proaktive Lauf
        wirklich uebersprungen."""
        card = (REPO / "frontend/src/components/dashboard/agent-card.tsx").read_text()
        self.assertIn("has_responsibilities === false", card)
        self.assertIn("AlertTriangle", card)


class CompletionTests(unittest.TestCase):
    def test_completion_sets_flag_and_writes_duties(self):
        cfg = ob.apply_completion(
            _agent(),
            role="Sekretariat",
            boundaries="Keine Rechnungen freigeben",
            responsibilities=[{"title": "Posteingang sichten", "rhythm": "daily"}],
        )
        self.assertTrue(cfg["onboarding_complete"])
        self.assertEqual(cfg["role"], "Sekretariat")
        self.assertEqual(cfg["boundaries"], "Keine Rechnungen freigeben")
        (duty,) = cfg["proactive"]["responsibilities"]
        self.assertEqual(duty["title"], "Posteingang sichten")
        self.assertEqual(duty["rhythm"], "daily")

    def test_existing_duties_are_kept_not_overwritten(self):
        """Ein zweites Gespraech ergaenzt — es loescht nicht, was von Hand gepflegt wurde."""
        agent = _agent(proactive={"responsibilities": [{"title": "Wiki pflegen"}]})
        cfg = ob.apply_completion(agent, responsibilities=[{"title": "Posteingang sichten"}])
        titles = [d["title"] for d in cfg["proactive"]["responsibilities"]]
        self.assertIn("Wiki pflegen", titles)
        self.assertIn("Posteingang sichten", titles)

    def test_duplicates_are_not_added_twice(self):
        agent = _agent(proactive={"responsibilities": [{"title": "Posteingang sichten"}]})
        cfg = ob.apply_completion(agent, responsibilities=[{"title": "posteingang sichten"}])
        self.assertEqual(len(cfg["proactive"]["responsibilities"]), 1)

    def test_notes_are_appended_to_existing_instructions(self):
        agent = _agent(proactive={"custom_instructions": "Alt"})
        cfg = ob.apply_completion(agent, responsibilities=[{"title": "X"}], notes="Neu")
        self.assertIn("Alt", cfg["proactive"]["custom_instructions"])
        self.assertIn("Neu", cfg["proactive"]["custom_instructions"])


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = (ORCH / "app/api/onboarding.py").read_text()

    def test_completion_requires_at_least_one_duty(self):
        """'Eingerichtet' ohne Auftrag waere genau der alte Zustand — nur mit Haken dran."""
        self.assertIn("Mindestens eine Daueraufgabe", self.src)

    def test_agent_can_only_onboard_itself(self):
        self.assertIn("Agent can only onboard itself", self.src)
        self.assertIn("visible_agent_ids", self.src)

    def test_router_is_mounted(self):
        self.assertIn("onboarding_api.router", (ORCH / "app/api/router.py").read_text())


class HarnessParityTests(unittest.TestCase):
    """complete_onboarding und der Statusblock muessen ueberall ankommen."""

    def test_tool_in_mcp_for_claude_code(self):
        mcp = (AGENT / "mcp/orchestrator-server.mjs").read_text()
        self.assertIn('name: "complete_onboarding"', mcp)
        self.assertIn('case "complete_onboarding":', mcp)

    def test_tool_for_codex_and_custom_llm(self):
        self.assertIn('"name": "complete_onboarding"', (AGENT / "app/tools/definitions.py").read_text())
        self.assertIn("async def complete_onboarding(", (AGENT / "app/tools/api_client.py").read_text())

    def test_tool_is_in_the_core_set(self):
        chat = (AGENT / "app/llm_chat_handler.py").read_text()
        core = chat.split("CORE_TOOL_NAMES = {", 1)[1].split("}", 1)[0]
        self.assertIn('"complete_onboarding"', core)

    def test_status_reaches_every_agent_runtime(self):
        hooks = (AGENT / "app/runner_hooks.py").read_text()
        self.assertIn("def get_onboarding_context(", hooks)
        # custom_llm bekommt ihn ueber die Identitaet ...
        identity = hooks.split("def get_identity_context(", 1)[1].split("def get_onboarding_context(", 1)[0]
        self.assertIn("get_onboarding_context()", identity)
        # ... die CLI-Laufzeiten ueber das gemeinsame Buendel (beide Zweige).
        bundle = hooks.split("def compose_prompt_bundle(", 1)[1].split("def get_user_feedback(", 1)[0]
        self.assertEqual(bundle.count("get_onboarding_context()"), 2)

    def test_status_reaches_the_proactive_run(self):
        sched = (ORCH / "app/services/scheduler_service.py").read_text()
        self.assertIn("onboarding_note(_agent)", sched)

    def test_status_reaches_the_voice_front(self):
        voice = (ORCH / "app/services/realtime_voice_session.py").read_text()
        self.assertIn("onboarding_note(agent, spoken=True)", voice)

    def test_proactive_prompt_forbids_silent_idling(self):
        mgr = (ORCH / "app/core/agent_manager.py").read_text()
        step3 = mgr.split("## STEP 3:", 1)[1].split("## STEP 4", 1)[0]
        self.assertIn("onboarding", step3.lower())



class NoAssignmentTests(unittest.TestCase):
    """Ohne Auftrag darf der proaktive Lauf gar nicht erst starten.

    Er koennte nichts zustande bringen — frueher lief er trotzdem, kostete Modell-Zeit
    und meldete 'nichts zu tun'. Statt eines Laufs bekommt der Besitzer einen Hinweis,
    und die Agentenkachel ein Ausrufezeichen.
    """

    @classmethod
    def setUpClass(cls):
        cls.sched = (ORCH / "app/services/scheduler_service.py").read_text()

    def test_run_is_skipped_when_duties_are_missing(self):
        """Frueher wurde zusaetzlich der Einrichtungshaken geprueft. Seit das
        Einrichtungsgespraech entfallen ist, war das eine Falle: nichts konnte den
        Haken noch setzen, ein Bestandsagent mit `false` waere fuer immer
        uebersprungen worden."""
        self.assertNotIn("is_onboarded(_agent)", self.sched)
        self.assertIn("not has_duties(_agent)", self.sched)
        skip = self.sched.split("not has_duties(_agent)", 1)[1][:600]
        self.assertIn("_nudge_missing_assignment", skip)
        self.assertIn("schedule.next_run_at = _calc_next_run(schedule, now)", skip)
        self.assertIn("return", skip)

    def test_owner_gets_a_notification(self):
        self.assertIn("async def _nudge_missing_assignment", self.sched)
        nudge = self.sched.split("async def _nudge_missing_assignment", 1)[1].split("async def _proactive_config", 1)[0]
        self.assertIn("Notification(", nudge)
        self.assertIn("wartet auf seinen Auftrag", nudge)
        self.assertIn("action_url", nudge)

    def test_notification_is_throttled(self):
        """Bei stuendlichem Zeitplan waeren es sonst 24 Hinweise am Tag."""
        nudge = self.sched.split("async def _nudge_missing_assignment", 1)[1].split("async def _proactive_config", 1)[0]
        self.assertIn("onboarding_nudge:", nudge)
        self.assertIn("12 * 3600", nudge)

    def test_agent_card_shows_the_mark(self):
        card = (REPO / "frontend/src/components/dashboard/agent-card.tsx").read_text()
        self.assertIn("has_responsibilities === false", card)
        self.assertIn("AlertTriangle", card)

    def test_api_delivers_the_flag(self):
        self.assertIn("has_responsibilities", (ORCH / "app/schemas/agent.py").read_text())
        self.assertIn("has_responsibilities=bool(", (ORCH / "app/api/agents.py").read_text())


class VoiceOnboardingTests(unittest.TestCase):
    """Der Sprachweg muss nicht nur fragen, sondern die Antwort auch sichern koennen."""

    @classmethod
    def setUpClass(cls):
        cls.voice = (ORCH / "app/services/realtime_voice_session.py").read_text()

    def test_tool_exists_and_is_offered(self):
        self.assertIn('"name": "complete_onboarding"', self.voice)
        tool_list = self.voice.split("_tools = [", 1)[1].split("]", 1)[0]
        self.assertIn("COMPLETE_ONBOARDING_TOOL", tool_list)

    def test_handler_writes_to_the_database(self):
        self.assertIn('if name == "complete_onboarding":', self.voice)
        handler = self.voice.split("async def _complete_onboarding", 1)[1].split("async def _get_day_plan", 1)[0]
        self.assertIn("apply_completion(", handler)
        self.assertIn("flag_modified", handler)
        self.assertIn("await db.commit()", handler)

    def test_handler_refuses_without_a_duty(self):
        handler = self.voice.split("async def _complete_onboarding", 1)[1].split("async def _get_day_plan", 1)[0]
        self.assertIn("mindestens eine wiederkehrende Aufgabe", handler)


if __name__ == "__main__":
    unittest.main()
