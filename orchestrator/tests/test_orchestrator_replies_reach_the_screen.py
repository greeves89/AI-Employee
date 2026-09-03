"""Vom Orchestrator angestossene Antworten landen im Chat — nicht nur in der Datenbank.

Kundenfall vom 2026-08-13: Ein Lead schickt einen Testauftrag, die Kachel wird
grün („abgeschlossen", mit Ergebnisvorschau) — und der Lead **schreibt nichts
mehr**. Er hatte angekündigt „Ich sag dir Bescheid, sobald er antwortet".

Die Antwort entstand tatsächlich. Sie wurde nur nicht ausgeliefert.

Der Weiterleiter im WS schottet Gespräche gegeneinander ab: er kennt
``_mid_to_session`` — die Nachrichtenkennungen, die **dieser Browser** gesendet
hat — und verwirft alles andere. Eine Rückmeldung, die der Orchestrator anstösst
(Fertigmeldung einer Delegation, Antwort eines Kollegen), trägt eine Kennung, die
der Browser nie gesehen hat. Sie fiel damit genau durch die Abschottung, die
verhindern soll, dass fremde Gespräche hineinbluten.

Ergebnis: die Antwort stand in ``chat_messages``, aber nie auf dem Bildschirm —
sichtbar erst nach einem Neuladen oder wenn der Mensch „und?" tippte.

Die Abschottung bleibt. Der Orchestrator hinterlegt beim Anstossen zusätzlich den
Zielfaden (``chat:msg:{id}:session``, eine Stunde haltbar), und der Weiterleiter
sieht dort nach, **bevor** er verwirft — und liefert nur aus, wenn der Faden zu
diesem Fenster gehört.
"""

import inspect
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WS = (ROOT / "orchestrator/app/api/ws.py").read_text()


class TheForwarderLooksItUpTests(unittest.TestCase):
    def test_it_checks_the_mapping_before_dropping(self):
        self.assertIn('f"chat:msg:{_mid}:session"', WS)

    def test_it_only_forwards_into_the_matching_session(self):
        """Sonst waere die Abschottung aufgehoben und fremde Gespraeche blueteten
        in das offene Fenster."""
        self.assertIn('if _looked == _session["id"]:', WS)

    def test_the_lookup_is_remembered_for_the_rest_of_the_turn(self):
        """Ein Zug erzeugt viele Ereignisse — einmal nachsehen genuegt."""
        self.assertIn("_mid_to_session[_mid] = _looked", WS)

    def test_a_broken_lookup_does_not_break_the_stream(self):
        block = WS.split('f"chat:msg:{_mid}:session"')[1][:700]
        self.assertIn("except Exception", block)


class TheOrchestratorLeavesTheMappingTests(unittest.TestCase):
    """Ein Nachschlagewerk, das niemand fuellt, hilft nicht."""

    def test_the_delegation_callback_stores_it(self):
        from app.core.task_router import TaskRouter

        src = inspect.getsource(TaskRouter._notify_delegating_agent)
        self.assertIn('f"chat:msg:{callback_id}:session"', src)

    def test_the_reply_notification_stores_it(self):
        src = (ROOT / "orchestrator/app/api/agents.py").read_text()
        self.assertIn('f"chat:msg:{_cb_id}:session"', src)

    def test_the_mapping_expires(self):
        """Ohne Verfall sammelt Redis fuer jede Rueckmeldung dauerhaft einen
        Schluessel an."""
        from app.core.task_router import TaskRouter

        src = inspect.getsource(TaskRouter._notify_delegating_agent)
        self.assertIn("setex(", src)


if __name__ == "__main__":
    unittest.main()
