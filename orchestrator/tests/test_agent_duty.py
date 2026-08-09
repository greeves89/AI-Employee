"""Dienstzustand, Vertretung, Eskalation, eigene Dienstzeit, Abwesenheit.

Fuenf Luecken, ein Fundament (``core/agent_duty`` + ``services/duty_service``): faellt ein
Agent aus, uebernimmt sein Vertreter; schweigt der Mensch, geht es eine Stufe hoeher.
Beim Kunden blieb bisher beides unbemerkt — 337 fehlgeschlagene Laeufe und monatelang
unbeantwortete Rueckfragen.
"""

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from app.core import agent_duty as duty

REPO = Path(__file__).resolve().parents[2]
ORCH = REPO / "orchestrator"


def _agent(state="running", **config):
    return SimpleNamespace(id="a1", name="Testi", user_id="u1", state=state, config=config)


UTC = timezone.utc


class DutyStateTests(unittest.TestCase):
    def test_running_and_quiet_is_ok(self):
        self.assertEqual(duty.assess(_agent())["state"], duty.OK)

    def test_stopped_with_active_schedule_is_down(self):
        d = duty.assess(_agent(state="stopped"), schedule_active=True)
        self.assertEqual(d["state"], duty.DOWN)
        self.assertTrue(duty.needs_handover(d))

    def test_stopped_without_schedule_is_just_off_duty(self):
        """Ein bewusst gestoppter Agent ohne Zeitplan ist kein Notfall."""
        d = duty.assess(_agent(state="stopped"), schedule_active=False)
        self.assertEqual(d["state"], duty.OFF_DUTY)
        self.assertFalse(duty.needs_handover(d))

    def test_hanging_work_counts_as_blocked(self):
        d = duty.assess(_agent(), stale_tasks=1)
        self.assertEqual(d["state"], duty.BLOCKED)
        self.assertTrue(duty.needs_handover(d))

    def test_full_queue_is_overload_but_no_handover(self):
        d = duty.assess(_agent(), queue_depth=duty.OVERLOAD_QUEUE_DEPTH)
        self.assertEqual(d["state"], duty.OVERLOADED)
        self.assertFalse(duty.needs_handover(d))

    def test_blocked_outranks_overload(self):
        """Haengende Arbeit ist das groessere Problem als eine volle Schlange."""
        d = duty.assess(_agent(), queue_depth=99, stale_tasks=3)
        self.assertEqual(d["state"], duty.BLOCKED)


class WorkingHoursTests(unittest.TestCase):
    def test_without_hours_he_is_always_on_duty(self):
        self.assertTrue(duty.is_on_duty(_agent()))

    def test_inside_and_outside_the_window(self):
        a = _agent(working_hours={"start": "08:00", "end": "17:00", "timezone": "UTC"})
        self.assertTrue(duty.is_on_duty(a, datetime(2026, 8, 7, 10, 0, tzinfo=UTC)))
        self.assertFalse(duty.is_on_duty(a, datetime(2026, 8, 7, 21, 0, tzinfo=UTC)))

    def test_window_over_midnight(self):
        a = _agent(working_hours={"start": "22:00", "end": "06:00", "timezone": "UTC"})
        self.assertTrue(duty.is_on_duty(a, datetime(2026, 8, 7, 23, 30, tzinfo=UTC)))
        self.assertTrue(duty.is_on_duty(a, datetime(2026, 8, 7, 5, 0, tzinfo=UTC)))
        self.assertFalse(duty.is_on_duty(a, datetime(2026, 8, 7, 12, 0, tzinfo=UTC)))

    def test_weekdays_only(self):
        a = _agent(working_hours={"start": "08:00", "end": "17:00",
                                  "timezone": "UTC", "weekdays_only": True})
        self.assertTrue(duty.is_on_duty(a, datetime(2026, 8, 7, 10, 0, tzinfo=UTC)))   # Freitag
        self.assertFalse(duty.is_on_duty(a, datetime(2026, 8, 8, 10, 0, tzinfo=UTC)))  # Samstag

    def test_off_duty_shows_up_in_the_assessment(self):
        a = _agent(working_hours={"start": "08:00", "end": "17:00", "timezone": "UTC"})
        self.assertEqual(
            duty.assess(a, now=datetime(2026, 8, 7, 23, 0, tzinfo=UTC))["state"], duty.OFF_DUTY
        )


class AbsenceTests(unittest.TestCase):
    def test_absence_window(self):
        a = _agent(proactive={"contact_absence": {"from": "2026-08-01", "to": "2026-08-14"}})
        self.assertTrue(duty.is_contact_absent(a, datetime(2026, 8, 7, tzinfo=UTC)))
        self.assertFalse(duty.is_contact_absent(a, datetime(2026, 8, 20, tzinfo=UTC)))

    def test_no_window_means_present(self):
        self.assertFalse(duty.is_contact_absent(_agent()))

    def test_broken_dates_do_not_lock_him_out(self):
        a = _agent(proactive={"contact_absence": {"from": "gestern", "to": "morgen"}})
        self.assertFalse(duty.is_contact_absent(a))

    def test_note_tells_him_to_collect_questions(self):
        a = _agent(proactive={"contact_absence": {"from": "2026-08-01", "to": "2026-08-14"}})
        note = duty.duty_note(a, {"state": duty.OK}, now=datetime(2026, 8, 7, tzinfo=UTC))
        self.assertIn("sammle sie", note)
        self.assertIn("2026-08-14", note)


class EscalationChainTests(unittest.TestCase):
    def test_order_is_human_deputy_lead_admin(self):
        chain = duty.escalation_chain(_agent(deputy_agent_id="b2"), team_lead_id="lead9")
        self.assertEqual([c["label"] for c in chain],
                         ["Ansprechpartner", "Vertreter", "Team-Lead", "Administration"])

    def test_chain_without_deputy_skips_that_step(self):
        chain = duty.escalation_chain(_agent(), team_lead_id="lead9")
        self.assertNotIn("Vertreter", [c["label"] for c in chain])

    def test_agent_is_never_its_own_lead(self):
        chain = duty.escalation_chain(_agent(), team_lead_id="a1")
        self.assertNotIn("Team-Lead", [c["label"] for c in chain])


class DutyNoteTests(unittest.TestCase):
    def test_overload_note_forbids_taking_more(self):
        note = duty.duty_note(_agent(), {"state": duty.OVERLOADED, "reason": "7 Aufgaben warten"})
        self.assertIn("UEBERLASTET", note)
        self.assertIn("Nimm nichts Neues an", note)

    def test_quiet_agent_gets_no_block(self):
        self.assertEqual(duty.duty_note(_agent(), {"state": duty.OK}), "")


class WiringTests(unittest.TestCase):
    """Die Mechanik muss im Scheduler ankommen — sonst ist sie Zierde."""

    @classmethod
    def setUpClass(cls):
        cls.sched = (ORCH / "app/services/scheduler_service.py").read_text()
        cls.svc = (ORCH / "app/services/duty_service.py").read_text()

    def test_scheduler_assesses_before_firing(self):
        self.assertIn("agent_duty.assess(", self.sched)
        self.assertIn("agent_duty.needs_handover(duty)", self.sched)
        self.assertIn("duty_service.escalate_failure", self.sched)

    def test_scheduler_checks_for_silence(self):
        self.assertIn("duty_service.escalate_silence", self.sched)

    def test_agent_learns_about_his_own_state(self):
        self.assertIn("agent_duty.duty_note(duty_agent, duty", self.sched)

    def test_handover_moves_the_existing_todos(self):
        """Kein zweites Aufgabensystem — die vorhandene Zeile wandert mit Herkunftsvermerk."""
        self.assertIn("todo.agent_id = deputy.id", self.svc)
        self.assertIn("Übernommen von", self.svc)

    def test_dead_deputy_is_skipped(self):
        self.assertIn('state in ("running", "idle", "working")', self.svc)

    def test_handover_and_escalation_are_throttled(self):
        self.assertIn("duty:handover:", self.svc)
        self.assertIn("duty:escalation:", self.svc)

    def test_missing_deputy_is_reported_loudly(self):
        self.assertIn("niemand übernimmt", self.svc)


class PriorityTests(unittest.TestCase):
    def test_plan_inherits_and_sorts_by_priority(self):
        src = (ORCH / "app/api/day_plan.py").read_text()
        self.assertIn('_PRIORITY_RANK = {"high": 0, "normal": 1, "low": 2}', src)
        self.assertIn("_PRIORITY_RANK.get(r.priority, 1)", src)

    def test_tool_can_pass_it_in_every_runtime(self):
        self.assertIn('"priority"', (REPO / "agent/app/tools/definitions.py").read_text())
        self.assertIn("priority:", (REPO / "agent/mcp/orchestrator-server.mjs").read_text())

    def test_prompt_has_a_conflict_rule(self):
        mgr = (ORCH / "app/core/agent_manager.py").read_text()
        self.assertIn("Zwei Dinge gleichzeitig dringend?", mgr)


class TemplateAndDevelopmentTests(unittest.TestCase):
    def test_templates_carry_responsibilities(self):
        self.assertIn("responsibilities", (ORCH / "app/models/agent_template.py").read_text())
        api = (ORCH / "app/api/templates.py").read_text()
        self.assertIn("validated_responsibilities", api)
        self.assertIn('cfg["onboarding_complete"] = True', api)

    def test_development_endpoint_exists(self):
        ana = (ORCH / "app/api/analytics.py").read_text()
        self.assertIn('@router.get("/agents/{agent_id}/development")', ana)
        self.assertIn("plan_adherence", ana)
        self.assertIn("probation", ana)

    def test_both_are_reachable_in_the_ui(self):
        """Backend ohne Oberflaeche ist fuer den Nutzer nicht vorhanden: die
        Vorlagen-Bereiche konnte niemand eintragen, die Kennzahl niemand sehen."""
        tpl = (REPO / "frontend/src/components/settings/template-manager.tsx").read_text()
        self.assertIn("ResponsibilitiesEditor", tpl)
        self.assertIn("responsibilities", tpl)
        page = (REPO / "frontend/src/app/agents/[id]/page.tsx").read_text()
        self.assertIn("DevelopmentCard", page)
        card = (REPO / "frontend/src/components/agents/development-card.tsx").read_text()
        self.assertIn("getAgentDevelopment", card)
        self.assertIn("Plan-Treue", card)

    def test_the_editor_exists_only_once(self):
        """Agent und Vorlage teilen sich EINEN Editor — sonst laufen die Regeln
        (Grenze, Takt, Prioritaet) auseinander."""
        shared = (REPO / "frontend/src/components/agents/responsibilities-editor.tsx").read_text()
        self.assertIn("MAX_RESPONSIBILITIES = 20", shared)
        toggle = (REPO / "frontend/src/components/agents/proactive-toggle.tsx").read_text()
        self.assertIn("ResponsibilitiesEditor", toggle)
        self.assertNotIn("const RHYTHMS", toggle)

    def test_screen_rules_are_in_the_shared_instructions(self):
        mgr = (ORCH / "app/core/agent_manager.py").read_text()
        self.assertIn("computer_find_element", mgr)
        self.assertIn("Nach JEDEM Klick nachsehen", mgr)


if __name__ == "__main__":
    unittest.main()
