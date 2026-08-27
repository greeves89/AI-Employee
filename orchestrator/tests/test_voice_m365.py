"""Sprachfront-Luecken bei M365, live gemeldet am 27.08.2026:

1. `m365_mail_recent` konnte nur "letzte N Mails" — keine Stichwortsuche, obwohl
   der Agent (`ms_list_emails`) laengst `search`/`sender`/`subject` hatte. Der
   Nutzer bat um eine Reisekosten-Suche ("Deutsche Bahn", "Hotel"); die Stimme
   scannte stattdessen blind die letzten 20 Mails.
2. `m365_calendar_today` reichte `days_ahead` direkt an ein ROLLIERENDES Fenster
   ab JETZT durch — "Termine fuer morgen" (days_ahead=1) lieferte ueberwiegend
   den Rest von HEUTE, weil das Fenster erst um Mitternacht wirklich "morgen"
   erreicht.
3. Teams-Nachrichten: die Sprachfront hatte dafuer NULL Werkzeuge, obwohl der
   Agent laengst `ms_list_chats`/`ms_send_chat_message` hat, und keine
   Personensuche (`ms_search_people` existierte nur agentseitig).

Diese Tests fixieren die Behebung — nicht die konkreten Woerter der
Live-Antwort, sondern dass die richtigen Parameter/Werkzeuge tatsaechlich
ankommen.
"""

import unittest
from unittest.mock import AsyncMock, patch

from app.core import msgraph_mcp
from app.services.realtime_voice_session import RealtimeVoiceSession


def _voice():
    v = RealtimeVoiceSession.__new__(RealtimeVoiceSession)
    v.user_id, v.agent_id = "u1", "agent-1"
    v.redis = AsyncMock()
    v._emit = AsyncMock()
    return v


class MailSearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_keyword_is_forwarded_as_search_not_ignored(self):
        """Der eigentliche Fund: vorher gab es fuer `search` gar kein Feld."""
        v = _voice()
        with patch.object(v, "_m365_token", new=AsyncMock(return_value="tok")), \
             patch.object(msgraph_mcp, "handle_tool", new=AsyncMock(return_value="")) as ht:
            await v._m365_mail_recent(limit=8, search="Deutsche Bahn")
        args = ht.await_args[0][1]
        self.assertEqual(args.get("search"), "Deutsche Bahn")

    async def test_sender_and_subject_filters_are_forwarded_too(self):
        v = _voice()
        with patch.object(v, "_m365_token", new=AsyncMock(return_value="tok")), \
             patch.object(msgraph_mcp, "handle_tool", new=AsyncMock(return_value="")) as ht:
            await v._m365_mail_recent(sender="funke@mindsquare.de", subject="Award")
        args = ht.await_args[0][1]
        self.assertEqual(args.get("sender"), "funke@mindsquare.de")
        self.assertEqual(args.get("subject"), "Award")

    async def test_plain_recent_call_sends_no_filters(self):
        """Ohne Suchbegriff bleibt das alte Verhalten (letzte N) unveraendert."""
        v = _voice()
        with patch.object(v, "_m365_token", new=AsyncMock(return_value="tok")), \
             patch.object(msgraph_mcp, "handle_tool", new=AsyncMock(return_value="")) as ht:
            await v._m365_mail_recent(limit=5)
        args = ht.await_args[0][1]
        self.assertNotIn("search", args)
        self.assertNotIn("sender", args)
        self.assertNotIn("subject", args)


class CalendarDayAlignmentTests(unittest.IsolatedAsyncioTestCase):
    async def test_when_tomorrow_maps_to_a_real_date_not_a_rolling_window(self):
        v = _voice()
        with patch.object(v, "_m365_token", new=AsyncMock(return_value="tok")), \
             patch.object(msgraph_mcp, "handle_tool", new=AsyncMock(return_value="")) as ht:
            await v._m365_calendar_today(when="tomorrow")
        args = ht.await_args[0][1]
        self.assertEqual(args.get("date"), "tomorrow")
        self.assertNotIn("days_ahead", args)

    async def test_when_today_also_maps_to_date(self):
        v = _voice()
        with patch.object(v, "_m365_token", new=AsyncMock(return_value="tok")), \
             patch.object(msgraph_mcp, "handle_tool", new=AsyncMock(return_value="")) as ht:
            await v._m365_calendar_today(when="today")
        self.assertEqual(ht.await_args[0][1].get("date"), "today")

    async def test_without_when_the_old_rolling_window_still_works(self):
        """Fuer Wochenuebersichten bleibt days_ahead nuetzlich — nicht entfernen."""
        v = _voice()
        with patch.object(v, "_m365_token", new=AsyncMock(return_value="tok")), \
             patch.object(msgraph_mcp, "handle_tool", new=AsyncMock(return_value="")) as ht:
            await v._m365_calendar_today(days_ahead=7)
        args = ht.await_args[0][1]
        self.assertEqual(args.get("days_ahead"), 7)
        self.assertNotIn("date", args)


class SearchPeopleToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_query_reaches_the_same_lookup_the_agent_uses(self):
        v = _voice()
        with patch.object(v, "_m365_token", new=AsyncMock(return_value="tok")), \
             patch.object(msgraph_mcp, "handle_tool",
                          new=AsyncMock(return_value="Yannik Gassmann — yannik.gassmann@mindsquare.de")) as ht:
            out = await v._m365_search_people("Yannik Gassmann")
        self.assertEqual(ht.await_args[0][0], "ms_search_people")
        self.assertEqual(ht.await_args[0][1], {"query": "Yannik Gassmann"})
        self.assertIn("yannik.gassmann@mindsquare.de", out)

    async def test_blank_query_does_not_hit_the_network(self):
        v = _voice()
        with patch.object(msgraph_mcp, "handle_tool", new=AsyncMock()) as ht:
            out = await v._m365_search_people("  ")
        ht.assert_not_awaited()
        self.assertIn("wem", out.lower())


class TeamsMessageTests(unittest.IsolatedAsyncioTestCase):
    """Vorher: null Werkzeuge fuer Teams in der Sprachfront ueberhaupt."""

    _CHATS = {
        "value": [
            {"id": "chat-1", "topic": None,
             "members": [{"displayName": "Yannik Gassmann"}, {"displayName": "Daniel Alisch"}]},
            {"id": "chat-2", "topic": "Projektteam",
             "members": [{"displayName": "Anna Muster"}, {"displayName": "Ben Muster"}]},
        ]
    }

    async def test_a_clear_name_match_previews_before_sending(self):
        v = _voice()
        with patch.object(v, "_m365_token", new=AsyncMock(return_value="tok")), \
             patch.object(msgraph_mcp, "_graph", new=AsyncMock(return_value=self._CHATS)) as g:
            out = await v._m365_teams_message("Yannik", "test", confirmed=False)
        self.assertIn("Yannik Gassmann", out)
        self.assertIn("test", out)
        # Nur die Chat-Liste wurde abgefragt — noch NICHT gesendet.
        self.assertEqual(g.await_count, 1)
        self.assertEqual(g.await_args[0][0], "GET")

    async def test_confirmed_true_actually_sends(self):
        v = _voice()
        with patch.object(v, "_m365_token", new=AsyncMock(return_value="tok")), \
             patch.object(msgraph_mcp, "_graph", new=AsyncMock(return_value=self._CHATS)) as g:
            out = await v._m365_teams_message("Yannik", "test", confirmed=True)
        self.assertIn("gesendet", out.lower())
        post_calls = [c for c in g.await_args_list if c[0][0] == "POST"]
        self.assertEqual(len(post_calls), 1)
        self.assertEqual(post_calls[0][0][1], "/me/chats/chat-1/messages")

    async def test_no_existing_chat_says_so_plainly_not_a_false_excuse(self):
        v = _voice()
        with patch.object(v, "_m365_token", new=AsyncMock(return_value="tok")), \
             patch.object(msgraph_mcp, "_graph", new=AsyncMock(return_value=self._CHATS)):
            out = await v._m365_teams_message("Someone Unknown", "test")
        self.assertIn("keinen bestehenden Chat", out)

    async def test_ambiguous_name_asks_which_one_instead_of_guessing(self):
        v = _voice()
        chats = {"value": self._CHATS["value"] + [
            {"id": "chat-3", "topic": None, "members": [{"displayName": "Yannik Weber"}]},
        ]}
        with patch.object(v, "_m365_token", new=AsyncMock(return_value="tok")), \
             patch.object(msgraph_mcp, "_graph", new=AsyncMock(return_value=chats)) as g:
            out = await v._m365_teams_message("Yannik", "test", confirmed=True)
        self.assertIn("Mehrere Chats", out)
        # Bei Mehrdeutigkeit wird NICHT gesendet, egal was `confirmed` sagt.
        post_calls = [c for c in g.await_args_list if c[0][0] == "POST"]
        self.assertEqual(post_calls, [])

    async def test_not_connected_is_refused_cleanly(self):
        v = _voice()
        with patch.object(v, "_m365_token", new=AsyncMock(return_value=None)):
            out = await v._m365_teams_message("Yannik", "test")
        self.assertIn("nicht verbunden", out)


if __name__ == "__main__":
    unittest.main()
