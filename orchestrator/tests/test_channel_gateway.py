"""Kanalneutraler Eingang + Teams als vollwertiger Kanal.

Telegram war lange der einzige Weg von aussen zu einem Agenten, und der Ablauf steckte
im Telegram-Bot. Fuer Teams und Slack denselben Ablauf noch einmal zu schreiben hiesse,
ihn dreimal zu pflegen — beim naechsten Fix wuerde einer vergessen. Genau dieses Muster
hat in diesem Projekt schon mehrfach zugeschlagen.

Der Teams-AUSGANG war laengst da (ms_send_teams_message); es fehlte der EINGANG. Teams
war damit eine Einbahnstrasse: der Agent konnte hineinrufen, aber niemand ihn ansprechen.
"""

import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from app.core import channel_gateway as gw
from app.services import teams_gateway as tg

REPO = Path(__file__).resolve().parents[2]
ORCH = REPO / "orchestrator"


class _FakeRedisClient:
    def __init__(self):
        self.keys: dict = {}
        self.lists: dict = {}

    async def set(self, key, value, nx=False, ex=None):  # noqa: ANN001
        if nx and key in self.keys:
            return False
        self.keys[key] = value
        return True

    async def get(self, key):
        return self.keys.get(key)

    async def lpush(self, key, value):
        self.lists.setdefault(key, []).insert(0, value)
        return len(self.lists[key])


class _FakeRedis:
    def __init__(self):
        self.client = _FakeRedisClient()


def _msg(channel=gw.CHANNEL_TELEGRAM, **over):
    data = dict(agent_id="a1", text="Hallo", channel=channel,
                conversation_id="c1", message_id="m1", context={"chat_id": "c1"})
    data.update(over)
    return gw.InboundMessage(**data)


class IdentityTests(unittest.TestCase):
    def test_session_id_is_channel_scoped(self):
        """Ein Gespraech je Chat — sonst laufen Telegram und Teams in derselben
        Sitzung zusammen und der Agent verwechselt die Unterhaltungen."""
        self.assertEqual(_msg().session_id, "telegram:c1")
        self.assertEqual(_msg(gw.CHANNEL_TEAMS).session_id, "teams:c1")

    def test_telegram_prefix_is_unchanged(self):
        """Die Telegram-Rueckstrecke filtert Antworten mit startswith('tg-').
        Ein aus dem Kanalnamen abgeleitetes 'te-' haette bedeutet: der Agent
        antwortet, und niemand sieht es."""
        self.assertEqual(_msg().queue_message_id, "tg-m1")

    def test_every_channel_has_its_own_prefix(self):
        prefixes = [gw.CHANNEL_PREFIX[c] for c in gw.KNOWN_CHANNELS]
        self.assertEqual(len(prefixes), len(set(prefixes)),
                         "Zwei Kanaele mit gleichem Praefix — Antworten landen falsch.")

    def test_telegram_filter_still_matches(self):
        """Genau die Bedingung aus agent_bot.py."""
        self.assertTrue(_msg().queue_message_id.startswith("tg-"))
        self.assertFalse(_msg(gw.CHANNEL_TEAMS).queue_message_id.startswith("tg-"))


class DeduplicationTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_same_message_is_delivered_once(self):
        """Fuer abgefragte Kanaele unverzichtbar: zwei Durchlaeufe sehen dieselbe
        Nachricht, und der Agent wuerde zweimal antworten."""
        redis = _FakeRedis()
        self.assertFalse(await gw.already_seen(redis, _msg()))
        self.assertTrue(await gw.already_seen(redis, _msg()))

    async def test_different_channels_do_not_collide(self):
        redis = _FakeRedis()
        await gw.already_seen(redis, _msg(gw.CHANNEL_TELEGRAM))
        self.assertFalse(await gw.already_seen(redis, _msg(gw.CHANNEL_TEAMS)))


class EnqueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_payload_shape(self):
        redis = _FakeRedis()
        await gw.enqueue(redis, _msg(gw.CHANNEL_TEAMS))
        raw = redis.client.lists["agent:a1:chat"][0]
        payload = json.loads(raw)
        self.assertEqual(payload["chat_session_id"], "teams:c1")
        self.assertEqual(payload["channel"], "teams")
        self.assertIn("teams", payload)

    async def test_telegram_keeps_its_legacy_key(self):
        """Die Agenten-Laufzeiten werten den Schluessel `telegram` bereits aus —
        faellt er weg, weiss der Agent nicht mehr, wohin er antwortet."""
        redis = _FakeRedis()
        await gw.enqueue(redis, _msg(gw.CHANNEL_TELEGRAM))
        payload = json.loads(redis.client.lists["agent:a1:chat"][0])
        self.assertIn("telegram", payload)

    async def test_context_is_stashed_for_the_reply_path(self):
        """Ohne diesen Rueckgriff waere die Antwort erzeugt, aber unzustellbar."""
        redis = _FakeRedis()
        await gw.enqueue(redis, _msg(gw.CHANNEL_TEAMS))
        stored = json.loads(redis.client.keys["gateway:ctx:tm-m1"])
        self.assertEqual(stored["chat_id"], "c1")
        self.assertEqual(stored["channel"], "teams")

    async def test_uses_the_same_queue_as_the_web_chat(self):
        """Keine zweite Warteschlange je Kanal — die Live-Steuerung haengt an dieser."""
        redis = _FakeRedis()
        await gw.enqueue(redis, _msg(gw.CHANNEL_TEAMS))
        self.assertIn("agent:a1:chat", redis.client.lists)


class TeamsConfigTests(unittest.TestCase):
    def _agent(self, **teams_cfg):
        return SimpleNamespace(id="a1", name="Buchhalter", user_id="u1",
                               config={"channels": {"teams": teams_cfg}})

    def test_disabled_without_config(self):
        self.assertFalse(tg.is_enabled(SimpleNamespace(id="a", name="x", config={})))

    def test_enabled_needs_a_target(self):
        """Eingeschaltet ohne Chat waere ein Poller, der nichts abfragt."""
        self.assertFalse(tg.is_enabled(self._agent(enabled=True)))
        self.assertTrue(tg.is_enabled(self._agent(enabled=True, chat_ids=["c1"])))

    def test_mention_only_is_the_default(self):
        """In einem Gruppenchat soll der Agent nicht auf JEDE Nachricht antworten."""
        agent = self._agent(enabled=True, chat_ids=["c1"])
        cfg = tg.channel_config(agent)
        self.assertFalse(tg._mentions_agent("Wie war das Wetter?", agent, cfg))
        self.assertTrue(tg._mentions_agent("Buchhalter, mach mal", agent, cfg))

    def test_mention_only_can_be_switched_off(self):
        agent = self._agent(enabled=True, chat_ids=["c1"], mention_only=False)
        self.assertTrue(tg._mentions_agent("irgendwas", agent, tg.channel_config(agent)))

    def test_extra_mention_names(self):
        agent = self._agent(enabled=True, chat_ids=["c1"], mention_names=["Bot", "Kollege"])
        self.assertTrue(tg._mentions_agent("hey kollege", agent, tg.channel_config(agent)))


class TeamsParsingTests(unittest.TestCase):
    def test_html_becomes_plain_text(self):
        """Teams liefert HTML; die Auszeichnung interessiert den Agenten nicht
        und kostet im Prompt nur Platz."""
        msg = {"body": {"contentType": "html",
                        "content": "<p>Hallo <b>Welt</b><br>zweite Zeile</p>"}}
        self.assertEqual(tg._plain_text(msg), "Hallo Welt zweite Zeile")

    def test_entities_are_decoded(self):
        msg = {"body": {"contentType": "html", "content": "<p>M&uuml;ller &amp; Co</p>"}}
        self.assertEqual(tg._plain_text(msg), "Müller & Co")

    def test_plain_content_passes_through(self):
        msg = {"body": {"contentType": "text", "content": "roher Text"}}
        self.assertEqual(tg._plain_text(msg), "roher Text")

    def test_empty_body(self):
        self.assertEqual(tg._plain_text({}), "")


class TeamsOutboundTests(unittest.TestCase):
    def test_markdown_becomes_html(self):
        out = tg._to_html("**fett** und `code`")
        self.assertIn("<b>fett</b>", out)
        self.assertIn("<code>code</code>", out)

    def test_stray_characters_cannot_break_the_message(self):
        """Zuerst escapen, dann auszeichnen — dieselbe Ueberlegung wie bei Telegram."""
        out = tg._to_html("a < b & c > d")
        self.assertNotIn("<b ", out)
        self.assertIn("&lt;", out)
        self.assertIn("&amp;", out)

    def test_newlines_survive(self):
        self.assertIn("<br>", tg._to_html("Zeile eins\nZeile zwei"))


class NoSecondPathTests(unittest.TestCase):
    """Der eigentliche Punkt: ein Ablauf, nicht drei."""

    def test_telegram_uses_the_gateway(self):
        src = (ORCH / "app/telegram/agent_bot.py").read_text()
        self.assertIn("channel_gateway", src)
        self.assertNotIn("_maybe_capture", src)

    def test_telegram_no_longer_builds_its_own_payload(self):
        src = (ORCH / "app/telegram/agent_bot.py").read_text()
        block = src.split("async def _handle_message")[1].split("\n    async def ")[0]
        self.assertNotIn('"chat_session_id"', block,
                         "Telegram baut die Warteschlangen-Nachricht wieder selbst.")

    def test_teams_uses_the_gateway(self):
        self.assertIn("channel_gateway", (ORCH / "app/services/teams_gateway.py").read_text())

    def test_teams_has_no_own_scheduler(self):
        src = (ORCH / "app/services/scheduler_service.py").read_text()
        self.assertIn("_teams_gateway", src)
        self.assertNotIn("asyncio.create_task(self._teams", src,
                         "Eigene Dauerschleife statt des bestehenden Takts.")

    def test_teams_reuses_the_existing_graph_access(self):
        """Keine Bot-Registrierung: das haette eine neue Identitaet, ein neues
        Geheimnis und eine neue Freigabekette beim Kunden bedeutet."""
        src = (ORCH / "app/services/teams_gateway.py").read_text()
        self.assertIn("msgraph_mcp", src)
        self.assertIn("get_valid_token", src)
        self.assertNotIn("botframework", src.lower())

    def test_teams_sends_through_the_same_graph_path(self):
        src = (ORCH / "app/services/teams_gateway.py").read_text()
        self.assertIn("_graph(\"POST\"", src)


class LoopProtectionTests(unittest.TestCase):
    """Ohne diese Sperren frisst der Kanal sich selbst auf."""

    def test_own_messages_are_skipped(self):
        """Der Agent schreibt ueber dasselbe Konto — ohne Sperre antwortet er auf
        sich selbst, bis das Token-Budget aufgebraucht ist."""
        src = (ORCH / "app/services/teams_gateway.py").read_text()
        self.assertIn("own_user_id", src)

    def test_poll_size_is_bounded(self):
        self.assertLessEqual(tg.MAX_PER_POLL, 50)

    def test_first_run_does_not_replay_history(self):
        """Sonst arbeitet der Agent beim Einschalten den halben Chatverlauf ab."""
        self.assertLessEqual(tg.FIRST_RUN_LOOKBACK, timedelta(hours=1))


if __name__ == "__main__":
    unittest.main()
