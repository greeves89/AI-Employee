"""Der Agent muss von SELBST nachsehen, wer sein Team ist.

Nutzerbericht vom 18.08.2026: „wieso hat der das erst nach ansprache
gesichtet... wieso KOMMT DER NICHT ALLEIN AUF DEN GEDANKEN MAL ZU SCHAUEN"

Nachgesehen auf der Kundenkiste: der Agent trug vier Notizen mit sich, die einen
Kollegen nennen, den es nicht mehr gibt — die aelteste vom 01.07., die JUENGSTE
danach geschrieben. Er hat den toten Kollegen also nicht nur behalten, sondern
neu aufgeschrieben.

Die Sicherung dagegen (``UnknownAgentError``) hat NIE ausgeloest, und das
zurecht: an die tote Kennung ging kein einziger Auftrag. Der Agent hat nie
falsch delegiert — er hat nur falsch geglaubt. Dagegen hilft kein Fehler beim
Zustellen, sondern nur die Anweisung, vor dem Handeln nachzusehen.

Die Regel GAB es, aber sie hing am Gefragtwerden: „When someone asks ... ALWAYS
call list_my_team". Genau das beschreibt der Bericht — er sah nach, als er
angesprochen wurde. Hier wird geprueft, dass sie am HANDELN haengt.
"""

import re
import unittest

from app.core.agent_manager import DEFAULT_CLAUDE_MD


def _absatz(doc: str, anfang: str) -> str:
    """Der Absatz ab ``anfang`` bis zur naechsten Aufzaehlung auf oberster
    Ebene (eine Zeile, die mit ``- **`` beginnt, ohne Einrueckung) — als
    tatsaechliche Grenze im Dokument, nicht als geschaetzte Zeichenzahl. Ein
    laengerer Satz davor oder mittendrin verschiebt sie nicht; ein neuer
    Regel-Absatz, der spaeter davorgeschoben wird, aendert nur den Anfang."""
    i = doc.index(anfang)
    rest = doc[i:]
    treffer = re.search(r"\n-\s\*\*", rest[1:])
    return rest[: treffer.start() + 1] if treffer else rest


class TheRosterIsLookedUpNotRememberedTests(unittest.TestCase):
    def test_the_rule_is_not_bound_to_being_asked(self):
        """Der alte Wortlaut loeste nur aus, wenn jemand fragte."""
        self.assertNotIn(
            'When someone asks "who is on your team', DEFAULT_CLAUDE_MD
        )

    def test_looking_up_before_delegating_is_demanded(self):
        self.assertIn("**Your roster is something you LOOK UP", DEFAULT_CLAUDE_MD,
                      "Die Regel fehlt ganz")
        regel = _absatz(DEFAULT_CLAUDE_MD, "**Your roster is something you LOOK UP")
        for erwartet in ("before you delegate", "before you write anything about the team into memory"):
            self.assertIn(erwartet, regel)

    def test_it_says_what_to_do_when_a_colleague_is_gone(self):
        """Ohne diesen Satz plant er weiter mit einem Namen, den es nicht gibt."""
        regel = _absatz(DEFAULT_CLAUDE_MD, "**Your roster is something you LOOK UP")
        self.assertIn("do not queue work for a name that is no longer there", regel)

    def test_it_points_at_the_way_to_forget(self):
        """Genau das, was der Agent versucht hat und was am 401 scheiterte —
        siehe test_agent_can_forget.py."""
        regel = _absatz(DEFAULT_CLAUDE_MD, "**Your roster is something you LOOK UP")
        self.assertIn("memory_delete", regel)

    def test_the_tool_is_still_named(self):
        self.assertIn("list_my_team", DEFAULT_CLAUDE_MD)


if __name__ == "__main__":
    unittest.main()
