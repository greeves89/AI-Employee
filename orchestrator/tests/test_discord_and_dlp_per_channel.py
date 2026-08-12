"""Discord als vierter Kanal — und der DLP-Filter für ALLE Kanäle (#195, #139).

Zwei Dinge, die zusammengehören.

**Discord** war der letzte der genannten Kanäle ohne Anbindung. Er folgt exakt dem
Muster von Slack: der Ablauf bleibt im ``channel_gateway``, der Kanal liefert nur
Herkunft und Rückweg. Abgefragt statt über die dauerhafte WebSocket — in einem
Kliniknetz ist ausgehendes HTTPS die einzige verlässliche Voraussetzung.

**Der DLP-Filter** hing bis v1.181.0 **nur** am Telegram-Weg. Teams, Slack und
WhatsApp schickten ungeprüft hinaus — auf einer Klinikanlage genau der Fall, für
den es den Filter überhaupt gibt. Er sitzt jetzt in ``channel_gateway.send_reply``,
also an der einen Stelle, durch die alle diese Kanäle senden. Damit gilt er auch
für jeden Kanal, der später dazukommt, ohne dass jemand daran denken muss — und
genau das ist der Punkt: Discord wäre sonst der nächste ungeschützte gewesen.
"""

import unittest
from types import SimpleNamespace

from app.core import channel_gateway as gw
from app.services import discord_gateway as dc


class DlpCoversEveryChannelTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.sent: list[tuple[str, str]] = []

    def _fake_channel(self, monkey):
        """Ersetzt den Versand aller vier Kanäle durch einen Mitschnitt."""
        import sys

        for name in ("teams_gateway", "slack_gateway", "whatsapp_gateway",
                     "discord_gateway"):
            mod = sys.modules.get(f"app.services.{name}")
            if mod is not None:
                monkey.append((mod, mod.send_reply))

                async def _capture(agent, context, text, _n=name):
                    self.sent.append((_n, text))
                    return True

                mod.send_reply = _capture

    async def _send(self, channel, text, verdict):
        import sys

        import app.core.dlp as dlp

        monkey: list = []
        # Alle vier Kanaele laden, damit der Mitschnitt sie ersetzen kann.
        import app.services.discord_gateway  # noqa: F401
        import app.services.slack_gateway  # noqa: F401
        import app.services.teams_gateway  # noqa: F401
        import app.services.whatsapp_gateway  # noqa: F401

        self._fake_channel(monkey)
        orig_eval = dlp.evaluate_egress

        async def _fake_eval(t, **kw):
            return verdict

        dlp.evaluate_egress = _fake_eval
        try:
            ok = await gw.send_reply(channel, SimpleNamespace(id="a1"), {}, text)
        finally:
            dlp.evaluate_egress = orig_eval
            for mod, fn in monkey:
                mod.send_reply = fn
            sys.modules  # noqa: B018
        return ok

    async def test_masked_text_is_what_goes_out(self):
        verdict = SimpleNamespace(blocked=False, output="IBAN ***")
        for channel in (gw.CHANNEL_TEAMS, gw.CHANNEL_SLACK,
                        gw.CHANNEL_WHATSAPP, gw.CHANNEL_DISCORD):
            with self.subTest(channel):
                self.sent.clear()
                await self._send(channel, "IBAN DE02120300000000202051", verdict)
                self.assertEqual(self.sent[0][1], "IBAN ***")

    async def test_a_blocked_message_never_leaves_the_house(self):
        verdict = SimpleNamespace(blocked=True, output="egal")
        for channel in (gw.CHANNEL_TEAMS, gw.CHANNEL_SLACK,
                        gw.CHANNEL_WHATSAPP, gw.CHANNEL_DISCORD):
            with self.subTest(channel):
                self.sent.clear()
                await self._send(channel, "sk-geheim", verdict)
                self.assertNotIn("sk-geheim", self.sent[0][1])
                self.assertIn("DLP", self.sent[0][1])

    async def test_the_recipient_learns_that_something_was_suppressed(self):
        """Stillschweigend nichts zu senden waere schlimmer: der Mensch wartet
        dann auf eine Antwort, die nie kommt."""
        verdict = SimpleNamespace(blocked=True, output="egal")
        await self._send(gw.CHANNEL_SLACK, "sk-geheim", verdict)
        self.assertIn("blockiert", self.sent[0][1])


class DiscordIsRegisteredTests(unittest.TestCase):
    """Ein Kanal, der im Gateway fehlt, kommt nie zurueck — der Agent antwortet,
    und niemand sieht es."""

    def test_the_channel_is_known(self):
        self.assertIn(gw.CHANNEL_DISCORD, gw.KNOWN_CHANNELS)

    def test_it_has_its_own_id_prefix(self):
        """Abgeleitete Praefixe haben hier schon einmal Antworten verschluckt."""
        self.assertEqual(gw.CHANNEL_PREFIX[gw.CHANNEL_DISCORD], "dc")
        self.assertEqual(len(set(gw.CHANNEL_PREFIX.values())), len(gw.CHANNEL_PREFIX))

    def test_the_responder_listens_for_it(self):
        self.assertIn(gw.CHANNEL_DISCORD, gw.ChannelResponder.HANDLED)


class DiscordMessageHandlingTests(unittest.TestCase):
    def test_long_answers_are_split_not_truncated(self):
        """Abschneiden waere einfacher und falsch: eine halbe Antwort sieht aus
        wie eine ganze."""
        text = "\n\n".join(f"Absatz {i} " + "x" * 300 for i in range(20))
        chunks = dc.split_message(text)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(c) <= dc.MAX_MESSAGE_CHARS for c in chunks))
        self.assertIn("Absatz 19", chunks[-1])

    def test_short_text_stays_one_message(self):
        self.assertEqual(dc.split_message("kurz"), ["kurz"])

    def test_empty_text_sends_nothing(self):
        self.assertEqual(dc.split_message(""), [])

    def test_no_word_is_cut_in_half(self):
        chunks = dc.split_message("wort " * 900)
        self.assertTrue(all(not c.endswith("wo") for c in chunks))

    def test_deep_headings_become_bold(self):
        """Discord kennt keine vierstufigen Ueberschriften — sonst stuenden die
        Rauten im Klartext da."""
        self.assertEqual(dc.to_discord_markdown("#### Titel"), "**Titel**")

    def test_a_bot_message_is_ignored(self):
        """Sonst antworten zwei Bots einander bis zum Rate-Limit."""
        agent = SimpleNamespace(id="a1", name="Support")
        self.assertFalse(dc._mentions_agent("", agent, {}, []))

    def test_mention_by_name_reaches_the_agent(self):
        agent = SimpleNamespace(id="a1", name="Support")
        self.assertTrue(dc._mentions_agent("Hey Support, hilfst du?", agent, {}, []))

    def test_a_real_mention_reaches_the_agent(self):
        agent = SimpleNamespace(id="a1", name="Support")
        cfg = {"bot_user_id": "999"}
        self.assertTrue(dc._mentions_agent("hallo", agent, cfg, [{"id": "999"}]))

    def test_without_mention_requirement_everything_arrives(self):
        agent = SimpleNamespace(id="a1", name="Support")
        self.assertTrue(dc._mentions_agent("beliebig", agent,
                                           {"mention_required": False}, []))

    def test_timestamps_with_z_are_understood(self):
        self.assertIsNotNone(dc._parse_stamp("2026-08-12T18:00:00.000000Z"))
        self.assertIsNone(dc._parse_stamp("kaputt"))
        self.assertIsNone(dc._parse_stamp(None))


if __name__ == "__main__":
    unittest.main()
