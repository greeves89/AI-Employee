"""Zitiert der Nutzer eine Nachricht, muss der Agent sie sehen.

Telegram liefert das Zitat in `reply_to_message` mit, seit Bot-API 7.0 zusaetzlich
die markierte Textstelle in `quote`. Beides wurde verworfen: von „<Zitat> und das
hier?" kam beim Agenten nur „und das hier?" an. Er musste den Bezug aus dem Verlauf
raten — und riet bei langem Verlauf falsch.

Dazu die zweite Haelfte desselben Problems: die `message_id` stand nur im grossen
Block fuer eine NEUE Sitzung. Ab dem Folgezug wusste der Agent nicht mehr, auf
welche Nachricht er ueberhaupt reagieren koennte.
"""

import pathlib
import unittest
from types import SimpleNamespace

from app.telegram.agent_bot import _QUOTE_MAX_CHARS, _quoted_context

ROOT = pathlib.Path(__file__).resolve().parents[2]
PROMPT = ROOT / "agent/app/chat_consumer.py"


def _msg(*, reply_text=None, fragment=None, caption=None, is_bot=False, first_name="Mustermann"):
    """Eine Telegram-Nachricht, so weit nachgebildet wie die Funktion sie anfasst."""
    original = None
    if reply_text is not None or caption is not None:
        original = SimpleNamespace(
            text=reply_text,
            caption=caption,
            from_user=SimpleNamespace(is_bot=is_bot, first_name=first_name),
        )
    return SimpleNamespace(
        reply_to_message=original,
        quote=SimpleNamespace(text=fragment) if fragment else None,
    )


class QuotedContextTests(unittest.TestCase):
    def test_ohne_zitat_kein_vorspann(self):
        """Der Normalfall darf die Nachricht nicht veraendern."""
        self.assertEqual(_quoted_context(_msg()), "")

    def test_zitierter_text_taucht_auf(self):
        out = _quoted_context(_msg(reply_text="Der Watchdog killt jede Aufgabe."))
        self.assertIn("Der Watchdog killt jede Aufgabe.", out)
        self.assertTrue(out.endswith("\n\n"), "Vorspann muss vom Nutzertext getrennt sein")

    def test_markierte_stelle_schlaegt_die_ganze_nachricht(self):
        """Wer einen Satz markiert, meint genau diesen Satz — nicht die ganze Antwort."""
        out = _quoted_context(_msg(
            reply_text="Ein sehr langer Absatz mit vielen Nebensaetzen.",
            fragment="mit vielen Nebensaetzen",
        ))
        self.assertIn("mit vielen Nebensaetzen", out)
        self.assertNotIn("Ein sehr langer Absatz", out)

    def test_eigene_nachricht_wird_als_solche_benannt(self):
        """Zitiert der Nutzer den Agenten, ist das etwas anderes als ein Selbstzitat."""
        self.assertIn("eigenen", _quoted_context(_msg(reply_text="Hallo", is_bot=True)))
        self.assertIn("Mustermann", _quoted_context(_msg(reply_text="Hallo", is_bot=False)))

    def test_bildunterschrift_zaehlt_auch(self):
        """Ein zitiertes Foto hat keinen `text`, aber eine `caption`."""
        self.assertIn("Screenshot vom Fehler",
                      _quoted_context(_msg(caption="Screenshot vom Fehler")))

    def test_leeres_zitat_erzeugt_keinen_leeren_vorspann(self):
        """Ein zitierter Sticker hat weder Text noch Unterschrift."""
        self.assertEqual(_quoted_context(_msg(reply_text="")), "")
        self.assertEqual(_quoted_context(_msg(reply_text="   ")), "")

    def test_langes_zitat_wird_gekuerzt(self):
        """Sonst schiebt ein zitierter Roman die eigentliche Frage aus dem Blick."""
        out = _quoted_context(_msg(reply_text="wort " * 500))
        self.assertLess(len(out), _QUOTE_MAX_CHARS + 100)
        self.assertIn("[…]", out)


class MessageIdReachesTheAgentTests(unittest.TestCase):
    """Ohne message_id im Kopf kann der Agent ab Zug zwei nicht mehr reagieren."""

    def test_kopf_traegt_die_message_id(self):
        src = PROMPT.read_text()
        self.assertIn("message_id: {msg_ref}", src)

    def test_folgezug_verweist_auf_die_reaktion(self):
        """Der kurze Block ohne API-Referenz muss den Weg trotzdem nennen."""
        src = PROMPT.read_text()
        kurz = src.split("if not is_new_session:")[1].split("return f\"\"\"")[1]
        self.assertIn("message_id", kurz)


if __name__ == "__main__":
    unittest.main()
