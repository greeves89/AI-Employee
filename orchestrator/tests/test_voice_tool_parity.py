"""Die Sprachfront darf nicht heimlich weniger können als der Agent.

**Vorgabe des Nutzers (18.08.2026): „Sprachinteraktion IMMER über
MCP-Services."** Anlass war eine Messung: 42 Werkzeuge in der Sprachfront gegen
79 beim Agenten — zwei handgepflegte Listen, die auseinandergelaufen sind.

Diese Pruefung macht die Differenz zu einer Zahl, die nur fallen darf. Ohne sie
war die Luecke unsichtbar: beide Seiten funktionierten fuer sich, und dass die
Sprachfront bei Unsicherheit RAET statt zu eskalieren, faellt erst auf, wenn
jemand einer geratenen Antwort geglaubt hat.

Dasselbe Muster hat am selben Tag zweimal zugeschlagen — ein doppelter
MCP-Abschnitt, der einen Agenten komplett entwaffnete, und eine
Freigabe-Anzeige, die dreimal existierte und beim Erweitern nur zweimal
erwischt wurde. Zwei Listen ohne Test dazwischen laufen immer auseinander.
"""

import re
import unittest
from pathlib import Path

from app.core.agent_toolset import DEFINITION_TOOLS
from app.core.voice_tool_parity import (
    DELEGIERT,
    DIREKT,
    NOCH_OFFEN,
    einordnung,
    sprachname,
)

ROOT = Path(__file__).resolve().parents[2]
VOICE = (ROOT / "orchestrator/app/services/realtime_voice_session.py").read_text()


def _sprachwerkzeuge() -> set[str]:
    """Was der Sprach-Engine tatsaechlich angeboten wird."""
    liste = VOICE.split("_tools = [", 1)[1].split("]", 1)[0]
    namen = set()
    for konst in re.findall(r"\b([A-Z_]+_TOOL)\b", liste):
        m = re.search(r'%s = \{\s*"toolSpec": \{\s*"name": "([a-z_0-9]+)"' % konst, VOICE)
        if m:
            namen.add(m.group(1))
    return namen


class EveryToolIsClassifiedTests(unittest.TestCase):
    """Ein neues Werkzeug muss jemand einordnen — sonst fehlt es in der
    Sprachfront, ohne dass es auffaellt."""

    def test_nothing_is_unclassified(self):
        offen = sorted(set(DEFINITION_TOOLS) - DIREKT - DELEGIERT)
        self.assertEqual(offen, [], f"nicht eingeordnet: {offen}")

    def test_nothing_is_in_both_groups(self):
        self.assertEqual(DIREKT & DELEGIERT, set())

    def test_the_helper_agrees(self):
        self.assertEqual(einordnung("bash"), "delegiert")
        self.assertEqual(einordnung("memory_save"), "direkt")
        self.assertEqual(einordnung("gibtsnicht"), "unbekannt")


class AgentLocalToolsAreNotMirroredTests(unittest.TestCase):
    """``bash`` und Konsorten laufen im Agenten-Container. Sie in der
    Sprachfront nachzubauen hiesse, eine zweite Ausfuehrung danebenzustellen —
    genau das, was die Vorgabe verhindern soll."""

    def test_the_voice_has_no_shell(self):
        self.assertNotIn("bash", _sprachwerkzeuge())

    def test_the_voice_writes_no_files_itself(self):
        for t in ("write_file", "edit_file", "multi_edit"):
            with self.subTest(werkzeug=t):
                self.assertNotIn(t, _sprachwerkzeuge())

    def test_it_can_delegate_instead(self):
        """Ohne Delegation waere das Nicht-Spiegeln eine Luecke statt einer
        Aufgabenteilung."""
        self.assertIn("ask_agent", _sprachwerkzeuge())


class TheGapOnlyShrinksTests(unittest.TestCase):
    """``NOCH_OFFEN`` ist eine Schuld, keine Erlaubnis."""

    def test_the_recorded_gap_matches_reality(self):
        vorhanden = _sprachwerkzeuge()
        tatsaechlich = {t for t in DIREKT if sprachname(t) not in vorhanden}
        neu = sorted(tatsaechlich - NOCH_OFFEN)
        self.assertEqual(neu, [], f"NEUE Luecke in der Sprachfront: {neu}")

    def test_closed_gaps_are_removed_from_the_list(self):
        """Sonst waechst die Liste zu und sagt irgendwann nichts mehr aus."""
        vorhanden = _sprachwerkzeuge()
        erledigt = sorted(t for t in NOCH_OFFEN if sprachname(t) in vorhanden)
        self.assertEqual(erledigt, [],
                         f"steht als offen, ist aber da — bitte streichen: {erledigt}")

    def test_the_gap_is_smaller_than_when_it_was_found(self):
        """Am 18.08.2026 waren es 42 gegen 79 Werkzeuge."""
        self.assertLess(len(NOCH_OFFEN), len(DIREKT))


class TheConfidenceGateReachedTheVoiceTests(unittest.TestCase):
    """Die schwerwiegendste Luecke, geschlossen am 18.08.2026.

    Ohne ``escalate_if_unsure`` RIET die Sprachfront, statt bei Unsicherheit an
    einen Menschen abzugeben — als einzige der vier Laufzeiten. Am Telefon wiegt
    das schwerer als im Geschriebenen: ein falscher Name klingt genauso sicher
    wie ein richtiger, und niemand kann zurueckblaettern.
    """

    def test_the_voice_can_escalate(self):
        self.assertIn("escalate_if_unsure", _sprachwerkzeuge())

    def test_it_is_no_longer_on_the_open_list(self):
        self.assertNotIn("escalate_if_unsure", NOCH_OFFEN)

    def test_it_uses_the_servers_rule_not_its_own(self):
        """Zwei Schwellen waeren zwei Regeln, von denen eine irgendwann die
        falsche ist. Die Schwelle gehoert dem Betreiber."""
        self.assertIn("confidence_gate", VOICE)
        self.assertNotIn("if confidence <", VOICE)

    def test_a_failed_question_does_not_invite_guessing(self):
        """Sonst raet das Modell trotzdem — nur mit Rueckendeckung."""
        block = VOICE.split("Konfidenz-Gate fehlgeschlagen", 1)[1][:400]
        self.assertIn("Rate NICHT", block)

    def test_it_stops_working_until_the_answer_arrives(self):
        block = VOICE.split("Deine Sicherheit reicht nicht", 1)[1][:400]
        self.assertIn("ARBEITE NICHT WEITER", block)

    def test_the_answer_is_carried_back_to_the_model(self):
        """Eine Rueckfrage, deren Antwort nie ankommt, ist eine Sackgasse."""
        self.assertIn("_auf_entscheidung_warten", VOICE)
        block = VOICE.split("async def _auf_entscheidung_warten", 1)[1][:1800]
        self.assertIn("_inject_when_quiet", block)

    def test_a_denial_does_not_read_as_permission(self):
        block = VOICE.split("async def _auf_entscheidung_warten", 1)[1][:1800]
        self.assertIn("Mach NICHT weiter", block)


class ReachingTheUserIsStillMissingTests(unittest.TestCase):
    """Die naechste Luecke, damit sie nicht in Vergessenheit geraet."""

    def test_notifications_are_recorded_as_open(self):
        self.assertIn("notify_user", NOCH_OFFEN)
        self.assertIn("send_telegram", NOCH_OFFEN)


if __name__ == "__main__":
    unittest.main()
