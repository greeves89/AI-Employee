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


class SlackTests(unittest.TestCase):
    def _agent(self, **cfg):
        return SimpleNamespace(id="a1", name="Helfer", user_id="u1",
                               config={"channels": {"slack": cfg}})

    def test_disabled_without_channels(self):
        from app.services import slack_gateway as sl
        self.assertFalse(sl.is_enabled(self._agent(enabled=True)))
        self.assertTrue(sl.is_enabled(self._agent(enabled=True, channels=["C1"])))

    def test_mention_by_bot_user_id(self):
        from app.services import slack_gateway as sl
        agent = self._agent(enabled=True, channels=["C1"], bot_user_id="U42")
        cfg = sl.channel_config(agent)
        self.assertTrue(sl._mentions_agent("hey <@U42> mach mal", agent, cfg))
        self.assertFalse(sl._mentions_agent("nur so geredet", agent, cfg))

    def test_mrkdwn_is_not_markdown(self):
        """Slack kennt *fett*, nicht **fett** — ohne Umwandlung stehen die
        Sternchen im Klartext da."""
        from app.services.slack_gateway import to_mrkdwn
        self.assertEqual(to_mrkdwn("**wichtig**"), "*wichtig*")
        self.assertEqual(to_mrkdwn("## Titel"), "*Titel*")


class WhatsAppTests(unittest.TestCase):
    SECRET = "geheim"

    def test_signature_must_match(self):
        """Die Webhook-Adresse ist oeffentlich erreichbar — ohne Pruefung koennte
        jeder, der sie kennt, dem Agenten Nachrichten unterschieben."""
        import hashlib, hmac
        from app.services import whatsapp_gateway as wa

        body = b'{"entry":[]}'
        good = "sha256=" + hmac.new(self.SECRET.encode(), body, hashlib.sha256).hexdigest()
        self.assertTrue(wa.verify_signature(body, good, self.SECRET))
        self.assertFalse(wa.verify_signature(body, "sha256=falsch", self.SECRET))
        self.assertFalse(wa.verify_signature(b'{"entry":[1]}', good, self.SECRET))

    def test_missing_secret_rejects_instead_of_waving_through(self):
        from app.services import whatsapp_gateway as wa
        self.assertFalse(wa.verify_signature(b"x", "sha256=irgendwas", ""))
        self.assertFalse(wa.verify_signature(b"x", "", self.SECRET))

    def test_delivery_receipts_are_not_messages(self):
        """Statusmeldungen kommen ueber denselben Weg — wuerde man sie
        mitverarbeiten, antwortete der Agent auf seine eigenen Quittungen."""
        from app.services import whatsapp_gateway as wa
        payload = {"entry": [{"changes": [{"value": {
            "metadata": {"phone_number_id": "123"},
            "statuses": [{"status": "delivered"}],
        }}]}]}
        self.assertEqual(wa.extract_messages(payload), [])

    def test_text_messages_are_extracted(self):
        from app.services import whatsapp_gateway as wa
        payload = {"entry": [{"changes": [{"value": {
            "metadata": {"phone_number_id": "123"},
            "contacts": [{"wa_id": "4915112345", "profile": {"name": "Anna"}}],
            "messages": [{"from": "4915112345", "id": "wamid.1",
                          "type": "text", "text": {"body": "Hallo"}}],
        }}]}]}
        out = wa.extract_messages(payload)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["text"], "Hallo")
        self.assertEqual(out[0]["sender_name"], "Anna")

    def test_non_text_types_are_skipped(self):
        from app.services import whatsapp_gateway as wa
        payload = {"entry": [{"changes": [{"value": {
            "metadata": {"phone_number_id": "1"},
            "messages": [{"from": "4915", "id": "w1", "type": "image"}],
        }}]}]}
        self.assertEqual(wa.extract_messages(payload), [])


class OneResponderTests(unittest.TestCase):
    """Drei fast gleiche Antwort-Lauscher waeren genau die Doppelung, die dieser
    Baustein vermeiden soll."""

    def test_one_responder_serves_all_polled_channels(self):
        """Gegen die Kanalliste geprueft, nicht gegen eine Aufzaehlung.

        Vorher stand hier eine feste Liste. Beim vierten Kanal (Discord) faellt so
        ein Test zwar auf — aber erst, nachdem jemand ihn angepasst hat, und er
        haette genauso gut nur ergaenzt statt geprueft werden koennen. So schlaegt
        er beim naechsten Kanal von selbst an, wenn ihn jemand im Lauscher vergisst:
        dann antwortet der Agent, und niemand sieht es.
        """
        from app.core.channel_gateway import ChannelResponder

        polled = set(gw.KNOWN_CHANNELS) - {gw.CHANNEL_TELEGRAM}
        self.assertEqual(set(ChannelResponder.HANDLED), polled)

    def test_telegram_keeps_its_streaming_path(self):
        """Telegram laesst die Nachricht mitwachsen und kann deshalb nicht auf
        'fertiger Text am Ende' reduziert werden."""
        from app.core.channel_gateway import ChannelResponder
        self.assertNotIn(gw.CHANNEL_TELEGRAM, ChannelResponder.HANDLED)

    def test_teams_has_no_own_responder_anymore(self):
        src = (ORCH / "app/services/teams_gateway.py").read_text()
        self.assertNotIn("class TeamsResponder", src)

    def test_send_dispatch_covers_every_handled_channel(self):
        src = (ORCH / "app/core/channel_gateway.py").read_text()
        block = src.split("async def send_reply")[1].split("class ChannelResponder")[0]
        for module in ("teams_gateway", "slack_gateway", "whatsapp_gateway"):
            with self.subTest(module=module):
                self.assertIn(module, block)

    def test_webhook_never_returns_5xx_to_meta(self):
        """Meta wiederholt sonst die Zustellung und stellt den Webhook nach
        wiederholten Fehlern ganz ab."""
        src = (ORCH / "app/api/webhooks.py").read_text()
        block = src.split("async def whatsapp_inbound")[1]
        self.assertIn("except Exception", block)
        self.assertIn('return {"status": "ok"}', block)


class WatermarkTests(unittest.IsolatedAsyncioTestCase):
    """Der Wasserstand ist eine Laufmarke, keine Einstellung.

    SettingsService.set lehnt unbekannte Schluessel mit ValueError ab — ein
    Wasserstand je Agent waere dort NIE gespeichert worden, und der Poller haette
    bei jedem Durchlauf dasselbe Fenster erneut gelesen.
    """

    async def test_round_trip_through_redis(self):
        from app.services.teams_gateway import TeamsGateway

        gwy = TeamsGateway(_FakeRedis())
        stamp = datetime(2026, 8, 8, 9, 30, tzinfo=timezone.utc)
        await gwy._save_watermark("a1", stamp)
        self.assertEqual(await gwy._watermark("a1"), stamp)

    async def test_first_run_uses_the_short_lookback(self):
        from app.services.teams_gateway import TeamsGateway, FIRST_RUN_LOOKBACK

        got = await TeamsGateway(_FakeRedis())._watermark("neu")
        age = datetime.now(timezone.utc) - got
        self.assertLessEqual(age, FIRST_RUN_LOOKBACK + timedelta(seconds=5))

    async def test_slack_uses_its_own_namespace(self):
        """Sonst ueberschreiben sich die Marken beider Kanaele gegenseitig."""
        from app.services.slack_gateway import SlackGateway
        from app.services.teams_gateway import TeamsGateway

        redis = _FakeRedis()
        await TeamsGateway(redis)._save_watermark("a1", datetime(2026, 1, 1, tzinfo=timezone.utc))
        await SlackGateway(redis)._save_watermark("a1", datetime(2026, 6, 1, tzinfo=timezone.utc))
        self.assertEqual((await TeamsGateway(redis)._watermark("a1")).month, 1)
        self.assertEqual((await SlackGateway(redis)._watermark("a1")).month, 6)

    def test_no_dynamic_keys_in_platform_settings(self):
        for rel in ("app/services/teams_gateway.py", "app/services/slack_gateway.py"):
            src = (ORCH / rel).read_text()
            with self.subTest(file=rel):
                self.assertNotIn("watermark_{agent_id}", src)
                self.assertNotIn('SettingsService(db).set(f"', src)


class ChannelSecretsTests(unittest.TestCase):
    """Kanal-Zugangsdaten sind Geheimnisse, keine gewoehnlichen Einstellungen.

    Das Slack-Bot-Token erlaubt Lesen und Schreiben in den freigegebenen Kanaelen.
    Das WhatsApp-App-Geheimnis ist der Schluessel, mit dem JEDE eingehende Zustellung
    geprueft wird — wer es kennt, kann dem Agenten beliebige Nachrichten unterschieben.
    Beide muessen verschluesselt liegen und duerfen nie im Klartext zurueckkommen.
    """

    KEYS = ("slack_bot_token", "whatsapp_verify_token", "whatsapp_app_secret")

    def test_all_channel_credentials_are_secrets(self):
        from app.services.settings_service import SECRET_KEYS

        for key in self.KEYS:
            with self.subTest(key=key):
                self.assertIn(key, SECRET_KEYS)

    def test_and_are_settable_at_all(self):
        from app.services.settings_service import ALLOWED_KEYS

        for key in self.KEYS:
            with self.subTest(key=key):
                self.assertIn(key, ALLOWED_KEYS)

    def test_per_agent_tokens_are_stored_encrypted(self):
        """Ein Token im Klartext in agent.config landet in JEDER Antwort, die die
        Agenten-Konfiguration ausliefert."""
        for rel in ("app/services/slack_gateway.py", "app/services/whatsapp_gateway.py"):
            src = (ORCH / rel).read_text()
            with self.subTest(file=rel):
                self.assertIn("decrypt_token", src)
                self.assertIn("_enc", src)


class WhatsAppSenderAclTests(unittest.TestCase):
    """Absenderpruefung — die einzige Stelle ohne natuerlichen Rahmen.

    Telegram verlangt `/auth <key>`, Teams und Slack liegen im Firmen-Tenant. Eine
    WhatsApp-Nummer ist oeffentlich: wer sie kennt, schreibt dem Agenten. Ohne Liste
    waere der Agent fuer die ganze Welt erreichbar — und er arbeitet im Namen der
    Firma und hat Zugriff auf ihr Wissen.
    """

    def _allowed(self, entries, sender):
        from app.services.whatsapp_gateway import sender_allowed
        return sender_allowed({"allowed_senders": entries}, sender)

    def test_fail_closed_without_a_list(self):
        """Ohne gepflegte Liste kommt NIEMAND durch. Bewusst unbequem."""
        from app.services.whatsapp_gateway import sender_allowed
        self.assertFalse(sender_allowed({}, "4915112345"))
        self.assertFalse(self._allowed([], "4915112345"))

    def test_same_number_in_different_notations(self):
        """+49…, 0049… und 0151… sind dieselbe Nummer; der Anbieter liefert sie
        ohne Plus. Die fuehrende Null wird durch die Laendervorwahl ERSETZT."""
        for entry in ("+49 151 1234567", "0049 151 1234567", "0151 1234567",
                      "491511234567"):
            with self.subTest(entry=entry):
                self.assertTrue(self._allowed([entry], "491511234567"))

    def test_a_different_number_is_rejected(self):
        self.assertFalse(self._allowed(["+49 151 1234567"], "491519999999"))

    def test_too_short_an_entry_matches_nothing(self):
        """Bei sechs oder sieben Stellen ist eine zufaellige Uebereinstimmung mit
        einer fremden Nummer realistisch — die Liste wuerde Fremde hereinlassen,
        statt sie fernzuhalten."""
        for entry in ("1234", "1234567", "123456789"):
            with self.subTest(entry=entry):
                self.assertFalse(self._allowed([entry], "49151123456789"))

    def test_comparison_is_one_directional(self):
        """Der umgekehrte Vergleich (Eintrag endet auf eingehende Nummer) waere eine
        offene Liste: ein langer Eintrag haette jede kurze Nummer hereingelassen,
        die zufaellig sein Ende bildet."""
        self.assertFalse(self._allowed(["491511234567"], "1511234567"))

    def test_full_e164_matches_exactly(self):
        self.assertTrue(self._allowed(["+49 151 1234567"], "491511234567"))

    def test_empty_or_short_sender_is_rejected(self):
        for sender in ("", "123", "12345"):
            with self.subTest(sender=sender):
                self.assertFalse(self._allowed(["+49 151 1234567"], sender))

    def test_check_runs_before_anything_is_stored(self):
        """Eine abgewiesene Nachricht darf keine Spur hinterlassen — nicht in der
        Historie, nicht im Second Brain, nicht in der Warteschlange."""
        src = (ORCH / "app/services/whatsapp_gateway.py").read_text()
        block = src.split("async def handle_payload")[1]
        self.assertIn("sender_allowed", block)
        self.assertLess(block.index("sender_allowed"), block.index("gw.deliver"))

    def test_other_channels_have_their_own_gate(self):
        """Zum Vergleich festgehalten: die anderen Kanaele sind nicht ungeschuetzt."""
        tg_src = (ORCH / "app/telegram/agent_bot.py").read_text()
        self.assertIn("_is_authorized", tg_src)
        for rel in ("app/services/teams_gateway.py", "app/services/slack_gateway.py"):
            with self.subTest(file=rel):
                self.assertIn("mention_only", (ORCH / rel).read_text())
