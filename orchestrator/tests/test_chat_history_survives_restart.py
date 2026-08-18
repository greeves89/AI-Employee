"""Ein Chat darf seinen Verlauf nicht verlieren, wenn der Agent neu startet.

Beim Kunden am 18.08.2026, mit Bildschirmfoto: der Agent sprach in der
Unterhaltung „KI Uebersicht" ueber ein ganz anderes Projekt und stiess vier
Reviews bei drei Kollegen an. Der Nutzer: „vermischt der wirklich 2 Chats
miteinander?"

Nachgesehen auf der Anlage: **nein**. Alle Nachrichten des Wortwechsels lagen in
EINER Sitzung, die Anzeige stimmte, und in Redis gab es keinen gemeinsamen
Sitzungsschluessel. Der Agent laeuft als ``custom_llm`` — dort gibt es weder
``--resume`` noch eine Wiederherstellung.

Die eigentliche Luecke: bei dieser Laufzeit lebt der Verlauf ausschliesslich im
Arbeitsspeicher und wurde NIE aus der Datenbank zurueckgeholt. Nach einem
Neustart stand der Agent in einem Chat mit 70 gespeicherten Nachrichten vor
einem leeren Blatt — und reimte sich aus semantisch gesuchten Erinnerungen
zusammen, worum es geht. Er schrieb selbst „am wahrscheinlichsten" und handelte
trotzdem.

Der Weg dorthin war zusaetzlich verschlossen: ``/chat/history`` hing an einem
reinen NUTZER-Login. Derselbe Fehlertyp wie beim Loeschen eigener Erinnerungen
am selben Tag.
"""

import unittest
from types import SimpleNamespace

from app.api import agents as api
from app.dependencies import AgentPrincipal
from app.models.user import UserRole
from fastapi import HTTPException


class TheAgentMayReadItsOwnHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_agent_itself_is_let_through(self):
        """Das war der verschlossene Weg."""
        await api._check_owner_or_self("a1", AgentPrincipal(id="a1", username="agent-a1"), None)

    async def test_a_foreign_agent_is_refused(self):
        """Sonst liest ein Agent die Gespraeche eines anderen mit."""
        with self.assertRaises(HTTPException) as fall:
            await api._check_owner_or_self("a1", AgentPrincipal(id="a2", username="agent-a2"), None)
        self.assertEqual(fall.exception.status_code, 403)

    async def test_a_user_still_goes_through_the_normal_check(self):
        gerufen = {}

        async def _fake(agent_id, user, db):
            gerufen["ja"] = agent_id

        nutzer = SimpleNamespace(id="u1", role=UserRole.MEMBER)
        original = api._check_owner
        api._check_owner = _fake
        try:
            await api._check_owner_or_self("a1", nutzer, None)
        finally:
            api._check_owner = original
        self.assertEqual(gerufen.get("ja"), "a1")

    def test_the_endpoint_accepts_an_agent_token(self):
        from app.dependencies import require_auth, require_auth_or_agent
        route = next(r for r in api.router.routes
                     if getattr(r, "path", "").endswith("/chat/history")
                     and "GET" in getattr(r, "methods", ()))
        abhaengig = [d.call for d in route.dependant.dependencies]
        self.assertIn(require_auth_or_agent, abhaengig)
        self.assertNotIn(require_auth, abhaengig)


class TheRuntimeActuallyReloadsTests(unittest.TestCase):
    from pathlib import Path
    QUELLE = (Path(__file__).resolve().parents[2] / "agent/app/llm_chat_handler.py").read_text()

    def test_it_reloads_on_the_first_turn_of_a_fresh_handler(self):
        """Genau dann ist der Speicher leer — nach Neustart, Update oder
        Container-Tausch."""
        block = self.QUELLE.split('self._history.append(ChatMessage(role="system"', 1)
        self.assertEqual(len(block), 2)
        self.assertIn("_verlauf_nachladen()", block[1][:1400])

    def test_it_asks_for_this_conversation_only(self):
        """Ohne die Sitzungskennung holte es fremde Unterhaltungen herein —
        also genau das, was der Nutzer befuerchtet hat."""
        block = self.QUELLE.split("async def _verlauf_nachladen", 1)[1][:2000]
        self.assertIn("current_chat_session", block)
        self.assertIn('"session_id": session_id', block)

    def test_only_real_turns_are_restored(self):
        """`system`-Zeilen sind Kacheln und Statusmeldungen der Oberflaeche —
        das Modell hat sie nie gesehen und wuerde davon nur irritiert."""
        block = self.QUELLE.split("async def _verlauf_nachladen", 1)[1][:2000]
        self.assertIn('rolle in ("user", "assistant")', block)

    def test_a_failure_does_not_kill_the_turn(self):
        """Ohne Vorgeschichte weiterreden ist der Zustand von vorher — das darf
        keinen Fehler werfen."""
        block = self.QUELLE.split("async def _verlauf_nachladen", 1)[1][:2000]
        self.assertIn("Verlauf nicht nachladbar", block)
        self.assertIn("return []", block)

    def test_the_amount_is_bounded(self):
        """Der Verlauf wandert in den Kontext; die Kompaktierung greift erst
        danach."""
        self.assertIn("VERLAUF_NACHLADEN_MAX", self.QUELLE)


if __name__ == "__main__":
    unittest.main()
