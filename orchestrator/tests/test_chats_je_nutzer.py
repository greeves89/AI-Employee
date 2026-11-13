"""Chats je Nutzer an freigegebenen Agenten (core/chat_besitz).

Ein Agent, viele Menschen: Wer ihn freigegeben bekommt, sieht und fortsetzt nur
seine eigenen Gespräche. Besitzer und Admins sehen alle, der Agent selbst auch.
Geprüft wird über die echten Endpunkte, nicht nur über die Hilfsfunktionen —
eine vergessene Prüfung an einem Endpunkt ist genau der Fehler, den es zu
verhindern gilt.
"""

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import agents as api
from app.api import notifications as meldungen
from app.core import chat_besitz
from app.models.agent import Agent
from app.models.agent_access import AgentAccess
from app.models.chat_message import ChatMessage
from app.models.chat_session import ChatSession
from app.models.notification import Notification
from app.models.user import User, UserRole

BESITZER = SimpleNamespace(id="besitzer", role=UserRole.MEMBER)
ANNA = SimpleNamespace(id="anna", role=UserRole.MEMBER)
BEN = SimpleNamespace(id="ben", role=UserRole.MEMBER)
ADMIN = SimpleNamespace(id="adm", role=UserRole.ADMIN)
MANAGER = SimpleNamespace(id="mgr", role=UserRole.MANAGER)
AGENT_SELBST = SimpleNamespace(id="a1", role="agent", principal_type="agent")


class ChatsJeNutzerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            for m in (User, Agent, AgentAccess, ChatMessage, ChatSession, Notification):
                await conn.run_sync(m.metadata.create_all, tables=[m.__table__])
        self.db = async_sessionmaker(self.engine, expire_on_commit=False)()
        self.db.add_all([
            User(id=u, email=f"{u}@example.com", name=u, role=UserRole.MEMBER)
            for u in ("besitzer", "anna", "ben")
        ])
        self.db.add(Agent(id="a1", name="Assistenz", user_id="besitzer", config={}))
        self.db.add_all([AgentAccess(agent_id="a1", user_id="anna"),
                         AgentAccess(agent_id="a1", user_id="ben")])
        await self.db.commit()
        # Ein altes Gespräch (vor der Regel, ohne Besitzer) und je eins von Anna und Ben.
        await self._gespraech("alt", None, "Altes Gespräch des Besitzers")
        await self._gespraech("s-anna", "anna", "Annas Gehaltsfrage")
        await self._gespraech("s-ben", "ben", "Bens Urlaubsantrag")

    async def asyncTearDown(self):
        await self.db.close()
        await self.engine.dispose()

    async def _gespraech(self, sid, besitzer, text):
        if besitzer:
            await chat_besitz.beanspruchen(self.db, "a1", sid, besitzer)
        self.db.add_all([
            ChatMessage(agent_id="a1", session_id=sid, message_id=f"{sid}-u", role="user",
                        content=text, timestamp=datetime.now(timezone.utc)),
            ChatMessage(agent_id="a1", session_id=sid, message_id=f"{sid}-a", role="assistant",
                        content="Antwort", timestamp=datetime.now(timezone.utc)),
        ])
        await self.db.commit()

    async def _liste(self, user):
        r = await api.get_chat_sessions("a1", user=user, db=self.db)
        return {s["id"] for s in r["sessions"]}

    async def _verlauf(self, user, session_id=None):
        r = await api.get_chat_history("a1", session_id=session_id, limit=100, before_id=None,
                                       user=user, db=self.db)
        return {m["sessionId"] for m in r["messages"]}

    # --- Sehen -----------------------------------------------------------------

    async def test_freigegebener_nutzer_sieht_nur_eigene_gespraeche(self):
        self.assertEqual(await self._liste(ANNA), {"s-anna"})
        self.assertEqual(await self._liste(BEN), {"s-ben"})
        self.assertEqual(await self._verlauf(ANNA), {"s-anna"})

    async def test_besitzer_admin_und_agent_sehen_alle(self):
        alle = {"alt", "s-anna", "s-ben"}
        self.assertEqual(await self._liste(BESITZER), alle)
        self.assertEqual(await self._liste(ADMIN), alle)
        self.assertEqual(await self._verlauf(AGENT_SELBST), alle)

    async def test_manager_ohne_eigene_gespraeche_sieht_keine(self):
        """Manager sehen alle Agenten, aber nicht die Gespräche anderer Menschen."""
        self.assertEqual(await self._liste(MANAGER), set())
        self.assertEqual(await self._verlauf(MANAGER), set())

    async def test_fremdes_gespraech_ist_404(self):
        with self.assertRaises(HTTPException) as e:
            await self._verlauf(ANNA, "s-ben")
        self.assertEqual(e.exception.status_code, 404)
        with self.assertRaises(HTTPException):
            await self._verlauf(ANNA, "alt")  # gehört dem Besitzer
        self.assertEqual(await self._verlauf(BESITZER, "s-anna"), {"s-anna"})

    # --- Ändern ----------------------------------------------------------------

    async def test_fremde_gespraeche_nicht_aenderbar(self):
        aufrufe = [
            lambda: api.update_chat_session("a1", "s-ben", api.ChatSessionUpdate(title="x"), user=ANNA, db=self.db),
            lambda: api.fork_chat_session("a1", "s-ben", api.ForkRequest(message_id="s-ben-u"), user=ANNA, db=self.db),
            lambda: api.rewind_chat_session("a1", "s-ben", api.ForkRequest(message_id="s-ben-u"), user=ANNA, db=self.db),
            lambda: api.summarize_chat_session("a1", "s-ben", user=ANNA, db=self.db),
            lambda: api.compact_chat_session("a1", "s-ben", user=ANNA, db=self.db),
            lambda: api.chat_session_context("a1", "s-ben", user=ANNA, db=self.db),
            lambda: api.stop_chat_goal("a1", "s-ben", user=ANNA, db=self.db),
            lambda: api.set_message_context_exclusion(
                "a1", "s-ben", "s-ben-a", api.ContextExclusionRequest(), user=ANNA, db=self.db),
            lambda: api.delete_chat_session("a1", "s-ben", force=True, user=ANNA, db=self.db,
                                            redis=SimpleNamespace(client=None)),
        ]
        for aufruf in aufrufe:
            with self.assertRaises(HTTPException) as e:
                await aufruf()
            self.assertEqual(e.exception.status_code, 404)
        self.assertEqual(await self._verlauf(BEN, "s-ben"), {"s-ben"}, "Bens Gespräch ist unverändert")

    async def test_alle_loeschen_trifft_nur_die_eigenen(self):
        r = await api.delete_all_chat_sessions("a1", user=ANNA, db=self.db)
        self.assertEqual(r["deleted"], 2)
        self.assertEqual(await self._liste(BESITZER), {"alt", "s-ben"})

    async def test_abzweig_gehoert_dem_besitzer_des_originals(self):
        r = await api.fork_chat_session("a1", "s-anna", api.ForkRequest(message_id="s-anna-a"),
                                        user=ANNA, db=self.db)
        self.assertIn(r["session_id"], await self._liste(ANNA))
        self.assertNotIn(r["session_id"], await self._liste(BEN))

    # --- Neue Gespräche ----------------------------------------------------------

    async def test_neues_gespraech_gehoert_dem_der_es_beginnt(self):
        self.assertTrue(await chat_besitz.darf(self.db, "a1", BEN, "neu-1"))
        await chat_besitz.beanspruchen(self.db, "a1", "neu-1", "ben")
        self.assertFalse(await chat_besitz.darf(self.db, "a1", ANNA, "neu-1"))
        self.assertTrue(await chat_besitz.darf(self.db, "a1", BEN, "neu-1"))

    async def test_gespraech_mit_nachrichten_wechselt_nie_den_besitzer(self):
        await chat_besitz.beanspruchen(self.db, "a1", "alt", "anna")
        await chat_besitz.beanspruchen(self.db, "a1", "s-ben", "anna")
        self.assertIsNone(await chat_besitz.besitzer_von(self.db, "a1", "alt"))
        self.assertEqual(await chat_besitz.besitzer_von(self.db, "a1", "s-ben"), "ben")

    # --- Live-Chat und Sprache -----------------------------------------------------

    async def test_websocket_lehnt_fremdes_gespraech_ab(self):
        from app.api import ws

        with patch.object(ws, "async_session_factory", lambda: _Kontext(self.db)):
            self.assertFalse(await ws._chat_sitzung_erlaubt("a1", ANNA, "s-ben"))
            self.assertTrue(await ws._chat_sitzung_erlaubt("a1", ANNA, "s-anna"))
            self.assertTrue(await ws._chat_sitzung_erlaubt("a1", ANNA, "neu-2", beanspruchen=True))
        self.assertEqual(await chat_besitz.besitzer_von(self.db, "a1", "neu-2"), "anna")

    async def test_sprache_laedt_kein_fremdes_letztes_gespraech(self):
        from app.services.realtime_voice_session import RealtimeVoiceSession

        sitzung = RealtimeVoiceSession(agent_id="a1", user_id="anna", redis=None)
        self.assertEqual(await sitzung._eigene_gespraeche(self.db), {"s-anna"})
        sitzung = RealtimeVoiceSession(agent_id="a1", user_id="besitzer", redis=None)
        self.assertIsNone(await sitzung._eigene_gespraeche(self.db))
        sitzung = RealtimeVoiceSession(agent_id="a1", user_id="unknown", redis=None)
        self.assertEqual(await sitzung._eigene_gespraeche(self.db), set())

    # --- Benachrichtigungen ---------------------------------------------------------

    async def test_antwort_meldung_nur_fuer_den_gespraechsbesitzer(self):
        self.db.add_all([
            Notification(agent_id="a1", title="Assistenz", message="Annas Gehalt: 5.000",
                         empfaenger_id="anna", meta={"type": "chat_message"}),
            Notification(agent_id="a1", title="Assistenz", message="Für alle", meta={}),
        ])
        await self.db.commit()

        async def texte(user):
            with patch.object(meldungen, "_visible_agent_ids", AsyncMock(return_value=["a1"])):
                r = await meldungen.list_notifications(unread_only=False, limit=50, user=user, db=self.db)
                n = await meldungen.unread_count(user=user, db=self.db)
            return {x["message"] for x in r["notifications"]}, n["unread"]

        self.assertEqual(await texte(ANNA), ({"Annas Gehalt: 5.000", "Für alle"}, 2))
        self.assertEqual(await texte(BEN), ({"Für alle"}, 1))
        self.assertEqual(await texte(BESITZER), ({"Für alle"}, 1))


class _Kontext:
    """``async with async_session_factory() as db`` auf die Test-Datenbank."""

    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *a):
        return False


class QuelltextTests(unittest.TestCase):
    """Jeder Endpunkt mit ``session_id`` fragt core/chat_besitz — ein neuer
    Endpunkt ohne Prüfung fällt hier auf, bevor er Gespräche anderer zeigt."""

    def test_jeder_gespraechs_endpunkt_prueft_den_besitz(self):
        import inspect

        for name, fn in inspect.getmembers(api, inspect.iscoroutinefunction):
            quelle = inspect.getsource(fn)
            if "/chat/sessions/{session_id}" not in quelle and "session_id: str," not in quelle.split(")")[0]:
                continue
            if "@router" not in inspect.getsource(inspect.getmodule(fn)).split(f"async def {name}(")[0][-400:]:
                continue
            self.assertIn("chat_besitz", quelle, name)


if __name__ == "__main__":
    unittest.main()
