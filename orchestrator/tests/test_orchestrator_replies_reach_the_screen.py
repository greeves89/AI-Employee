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
from unittest.mock import AsyncMock, patch

from app.api import ws

ROOT = Path(__file__).resolve().parents[2]


class TheForwarderLooksItUpTests(unittest.IsolatedAsyncioTestCase):
    """Verhalten des Weiterleiters (``ws._sitzung_fuer_ereignis``), nicht sein
    Quelltext: Redis-Attrappe so streng wie Redis, Datenbank weiss nichts."""

    async def asyncSetUp(self):
        from tests.test_chat_neustart_ohne_abbruch import _StrengesRedis

        self.redis = _StrengesRedis()
        self._db = patch("app.services.chat_persistence.session_for_message",
                         AsyncMock(return_value=None))
        self._db.start()
        self.eigene, self.gefunden, self.gefragt = {}, {}, set()

    async def asyncTearDown(self):
        self._db.stop()

    async def _zuordnen(self, mid, aktuell):
        return await ws._sitzung_fuer_ereignis(
            self.redis, "a1", mid, aktuell, self.eigene, self.gefunden, self.gefragt)

    async def test_it_checks_the_mapping_before_dropping(self):
        await self.redis.setex("chat:msg:cb1:session", 3600, "s1")
        self.assertEqual(await self._zuordnen("cb1", "s1"), "s1")

    async def test_it_only_forwards_into_the_matching_session(self):
        """Sonst waere die Abschottung aufgehoben und fremde Gespraeche blueteten
        in das offene Fenster."""
        await self.redis.setex("chat:msg:cb1:session", 3600, "s-fremd")
        self.assertIsNone(await self._zuordnen("cb1", "s1"))
        self.assertIsNone(await self._zuordnen("cb2", "s1"), "unbekannt bleibt draussen")

    async def test_the_lookup_is_remembered_for_the_rest_of_the_turn(self):
        """Ein Zug erzeugt viele Ereignisse — einmal nachsehen genuegt."""
        await self.redis.setex("chat:msg:cb1:session", 3600, "s1")
        await self._zuordnen("cb1", "s1")
        self.redis.jetzt += 3601  # Eintrag laeuft ab — die Verbindung weiss es noch
        self.assertEqual(await self._zuordnen("cb1", "s1"), "s1")

    async def test_a_broken_lookup_does_not_break_the_stream(self):
        async def kaputt(*a, **k):
            raise ConnectionError("Redis weg")

        self.redis.get = kaputt
        self.assertIsNone(await self._zuordnen("cb1", "s1"))


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
