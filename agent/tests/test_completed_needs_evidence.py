"""„completed" muss sich ausweisen (#705).

Der Lauf sagt „fertig" — das ist eine ENTSCHEIDUNG. Ob er die Sache angefasst
hat, ist ein BEFUND. Bis hierher wurde nur die Entscheidung veroeffentlicht,
und beim Kunden standen am 2026-08-12 zwei Auftraege auf „erledigt", in deren
Ergebnis woertlich „nur angekuendigt" stand.

Die Tests halten die Grenze fest, an der ein Lauf als belegt gilt — und die
Faelle, in denen ein fehlender Beleg NICHT verdaechtig ist.
"""

import unittest

from app.agent_runner import AgentRunner
from app.task_evidence import (
    REPORTED,
    UNVERIFIED,
    VERIFIED,
    EvidenceLedger,
    verdict,
)


class VerdictTest(unittest.TestCase):
    def test_erfolgreiches_werkzeug_an_der_sache_ist_ein_beleg(self):
        b = verdict(status="completed", succeeded={"Write"}, failed=set())
        self.assertEqual(b["verdict"], VERIFIED)
        self.assertTrue(b["verified"])

    def test_nur_buchhaltung_ist_kein_beleg(self):
        """Genau der Kundenfall: der einzige Aufruf war die Bewertung selbst."""
        b = verdict(status="completed", succeeded={"rate_task"}, failed=set())
        self.assertEqual(b["verdict"], UNVERIFIED)
        self.assertFalse(b["verified"])

    def test_gar_kein_werkzeug_ist_kein_beleg(self):
        b = verdict(status="completed", succeeded=set(), failed=set())
        self.assertEqual(b["verdict"], UNVERIFIED)

    def test_gescheitertes_werkzeug_belegt_nichts(self):
        """Wer nur Fehlschlaege vorweist, hat die Sache nicht erledigt."""
        b = verdict(status="completed", succeeded=set(), failed={"Bash"})
        self.assertEqual(b["verdict"], UNVERIFIED)
        self.assertIn("Fehler", b["reason"])

    def test_kurzer_lauf_ohne_werkzeug_ist_nicht_verdaechtig(self):
        """Eine beantwortete Frage braucht kein Werkzeug — kein Fehlalarm."""
        b = verdict(
            status="completed", succeeded=set(), failed=set(), lightweight=True
        )
        self.assertEqual(b["verdict"], REPORTED)
        self.assertFalse(b["verified"])

    def test_mcp_buchhaltung_wird_am_namen_erkannt(self):
        b = verdict(
            status="completed",
            succeeded={"mcp__memory__memory_save"},
            failed=set(),
        )
        self.assertEqual(b["verdict"], UNVERIFIED)

    def test_mcp_arbeit_zaehlt(self):
        b = verdict(
            status="completed", succeeded={"mcp__github__create_issue"}, failed=set()
        )
        self.assertEqual(b["verdict"], VERIFIED)

    def test_delegieren_ist_arbeit(self):
        """Wer verteilt, hat gearbeitet — sonst meldet ausgerechnet der Lauf
        leer, der die Aufgabe richtig weitergegeben hat."""
        b = verdict(status="completed", succeeded={"TaskCreate"}, failed=set())
        self.assertEqual(b["verdict"], VERIFIED)

    def test_reines_nachschlagen_ist_keine_arbeit(self):
        """brain_get/list_my_team fehlten zuerst in der Liste."""
        for werkzeug in ("brain_get", "brain_list", "list_my_team", "list_tasks",
                         "ToolSearch", "mcp__brain__brain_get"):
            with self.subTest(werkzeug=werkzeug):
                b = verdict(status="completed", succeeded={werkzeug}, failed=set())
                self.assertEqual(b["verdict"], UNVERIFIED)

    def test_offener_aufruf_wird_nicht_als_fehler_ausgegeben(self):
        """Ein Aufruf ohne Ergebnis ist ungeklaert, nicht gescheitert — die
        Begruendung darf nichts behaupten, was niemand gesehen hat."""
        b = verdict(
            status="completed", succeeded=set(), failed=set(), unpaired={"Bash"}
        )
        self.assertEqual(b["verdict"], UNVERIFIED)
        self.assertNotIn("Fehler", b["reason"])
        self.assertIn("Ergebnis", b["reason"])

    def test_gescheiterter_lauf_bekommt_keinen_beleg(self):
        b = verdict(status="error", succeeded={"Write"}, failed=set())
        self.assertFalse(b["verified"])
        self.assertEqual(b["verdict"], "error")


class LedgerTest(unittest.TestCase):
    def test_werkzeug_zaehlt_erst_mit_seinem_ergebnis(self):
        led = EvidenceLedger()
        led.record_call("t1", "Write")
        # Noch kein Ergebnis: der Aufruf ist offen und belegt nichts.
        self.assertEqual(led.verdict("completed")["verdict"], UNVERIFIED)
        led.record_result("t1", is_error=False)
        self.assertEqual(led.verdict("completed")["verdict"], VERIFIED)

    def test_fehlerhaftes_ergebnis_zaehlt_nicht_als_arbeit(self):
        led = EvidenceLedger()
        led.record_call("t1", "Bash")
        led.record_result("t1", is_error=True)
        self.assertEqual(led.verdict("completed")["verdict"], UNVERIFIED)

    def test_aufruf_ohne_id_belegt_nichts(self):
        led = EvidenceLedger()
        led.record_call(None, "Write")
        b = led.verdict("completed")
        self.assertEqual(b["verdict"], UNVERIFIED)
        self.assertNotIn("Fehler", b["reason"])

    def test_ein_erfolg_neben_fehlschlaegen_genuegt(self):
        led = EvidenceLedger()
        led.record_call("t1", "Bash")
        led.record_result("t1", is_error=True)
        led.record_call("t2", "Edit")
        led.record_result("t2", is_error=False)
        self.assertEqual(led.verdict("completed")["verdict"], VERIFIED)


class StreamVerdrahtungTest(unittest.TestCase):
    """Der Claude-Code-Pfad hatte bis #705 gar keine Pruefung."""

    def _ledger_fuer(self, events):
        led = EvidenceLedger()
        for e in events:
            AgentRunner._record_evidence(led, e)
        return led

    def test_ergebnis_als_user_nachricht(self):
        """Die CLI liefert tool_result meist als user-Nachricht — sonst bliebe
        jeder Aufruf ohne Ausgang und echte Arbeit staende ohne Beleg da."""
        led = self._ledger_fuer([
            {
                "type": "assistant",
                "message": {"content": [
                    {"type": "tool_use", "id": "t1", "name": "Write"}
                ]},
            },
            {
                "type": "user",
                "message": {"content": [
                    {"type": "tool_result", "tool_use_id": "t1", "is_error": False}
                ]},
            },
        ])
        self.assertEqual(led.succeeded, {"Write"})
        self.assertTrue(led.verdict("completed")["verified"])

    def test_ergebnis_als_eigenes_ereignis(self):
        led = self._ledger_fuer([
            {
                "type": "assistant",
                "message": {"content": [
                    {"type": "tool_use", "id": "t1", "name": "Edit"}
                ]},
            },
            {"type": "tool_result", "tool_use_id": "t1", "is_error": True},
        ])
        self.assertEqual(led.failed, {"Edit"})
        self.assertEqual(led.verdict("completed")["verdict"], UNVERIFIED)

    def test_reiner_redelauf_bleibt_ohne_beleg(self):
        """Der Bericht vom 2026-08-16: drei Zuege Ankuendigung, null Werkzeug."""
        led = self._ledger_fuer([
            {
                "type": "assistant",
                "message": {"content": [
                    {"type": "text", "text": "Ich erstelle die App jetzt."}
                ]},
            },
        ])
        self.assertEqual(led.verdict("completed")["verdict"], UNVERIFIED)


class LLMPfadVerdrahtungTest(unittest.TestCase):
    """Der LLM-Pfad darf den Ausgang seiner Aufrufe nicht wegwerfen.

    Die Ausnahme wird dort sofort zu Text (``str(res)``); wer erst danach
    hinsieht, kann einen gescheiterten Aufruf nicht mehr von einem gelungenen
    unterscheiden und meldet den leeren Lauf als belegt. Deshalb als
    Quelltext-Pruefung: der Fehler waere im Betrieb unsichtbar.
    """

    def setUp(self):
        from pathlib import Path
        self.quelle = Path(__file__).resolve().parents[1] / "app" / "llm_runner.py"
        self.text = self.quelle.read_text(encoding="utf-8")

    def test_gescheiterte_werkzeuge_werden_beim_dispatch_erfasst(self):
        self.assertIn("tools_failed.add(tc[\"name\"])", self.text)

    def test_befund_bekommt_die_fehlschlaege(self):
        self.assertIn("failed=tools_failed", self.text)
        self.assertIn("succeeded=tools_called - tools_failed", self.text)


class BuchhaltungIstEineListeTest(unittest.TestCase):
    """Zwei Listen, die dasselbe meinen, laufen auseinander (#705)."""

    def test_llm_runner_teilt_die_liste(self):
        from app.llm_runner import LLMRunner
        from app.task_evidence import BOOKKEEPING_TOOLS
        self.assertIs(LLMRunner._BOOKKEEPING_TOOLS, BOOKKEEPING_TOOLS)

    def test_kein_name_der_alten_liste_ging_verloren(self):
        from app.task_evidence import BOOKKEEPING_TOOLS
        # Wortlaut der Liste vor der Zusammenlegung. Faellt einer heraus, gilt
        # ein reiner Buchhaltungslauf ploetzlich als Arbeit.
        for name in (
            "rate_task", "skill_rate", "memory_save", "memory_search",
            "memory_list", "memory_delete", "brain_search", "brain_related",
            "skill_search", "list_todos", "update_todos", "search_tools",
            "notify_user", "escalate_if_unsure", "request_approval",
            "check_approval",
        ):
            with self.subTest(name=name):
                self.assertIn(name, BOOKKEEPING_TOOLS)


if __name__ == "__main__":
    unittest.main()
