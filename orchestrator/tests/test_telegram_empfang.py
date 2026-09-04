"""Was Telegram schickt, muss beim Agenten ankommen.

Die Sendeseite ist vollstaendig (Sticker, Standort, GIF, Sprache, Datei …), die
Empfangsseite war es nicht: Standort, Kontakt, Umfrage, Sticker und Wuerfel passten
durch keinen Filter und wurden stillschweigend verworfen — der Nutzer bekam gar
keine Antwort. Weiterleitungen kamen ohne Herkunft an und lasen sich wie eigene
Aussagen. Korrekturen und Reaktionen forderten wir bei Telegram nie an.
"""

import pathlib
import unittest
from types import SimpleNamespace

from app.telegram.agent_bot import (
    _REACTION_BUFFER_MAX,
    _forward_context,
    _sonderinhalt,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
BOT = ROOT / "app/telegram/agent_bot.py"


def _msg(**felder):
    """Eine Nachricht, in der jedes nicht gesetzte Feld None ist — wie bei Telegram."""
    leer = dict(venue=None, location=None, contact=None, poll=None,
                sticker=None, dice=None, forward_origin=None)
    return SimpleNamespace(**{**leer, **felder})


class WeiterleitungTests(unittest.TestCase):
    def test_ohne_weiterleitung_kein_vorspann(self):
        self.assertEqual(_forward_context(_msg()), "")

    def test_nennt_die_person(self):
        herkunft = SimpleNamespace(sender_user=SimpleNamespace(first_name="Mustermann"))
        self.assertEqual(_forward_context(_msg(forward_origin=herkunft)),
                         "[Weitergeleitet von Mustermann]\n")

    def test_nennt_den_kanal(self):
        herkunft = SimpleNamespace(sender_user=None, sender_chat=None,
                                   chat=SimpleNamespace(title="Beispielkanal"))
        self.assertEqual(_forward_context(_msg(forward_origin=herkunft)),
                         "[Weitergeleitet von Beispielkanal]\n")

    def test_verborgene_herkunft_nennt_den_anzeigenamen(self):
        """Wer Weiterleitungen verbirgt, hinterlaesst nur einen Namen ohne Konto."""
        herkunft = SimpleNamespace(sender_user=None, sender_user_name="M. Mustermann")
        self.assertEqual(_forward_context(_msg(forward_origin=herkunft)),
                         "[Weitergeleitet von M. Mustermann]\n")

    def test_ohne_jede_angabe_trotzdem_als_weiterleitung_erkennbar(self):
        herkunft = SimpleNamespace(sender_user=None, sender_chat=None, chat=None)
        self.assertEqual(_forward_context(_msg(forward_origin=herkunft)),
                         "[Weitergeleitet]\n")


class SonderinhaltTests(unittest.TestCase):
    def test_reiner_text_hat_keinen_sonderinhalt(self):
        self.assertEqual(_sonderinhalt(_msg()), "")

    def test_standort(self):
        ort = SimpleNamespace(latitude=52.52, longitude=13.405)
        self.assertEqual(_sonderinhalt(_msg(location=ort)), "[Standort: 52.52, 13.405]")

    def test_benannter_ort_schlaegt_die_reinen_koordinaten(self):
        """Eine Ortsangabe mit Namen fuellt beide Felder — der Name ist nuetzlicher."""
        nachricht = _msg(
            venue=SimpleNamespace(title="Hauptbahnhof", address="Bahnhofstr. 1"),
            location=SimpleNamespace(latitude=52.52, longitude=13.405),
        )
        self.assertEqual(_sonderinhalt(nachricht),
                         "[Ort: Hauptbahnhof — Bahnhofstr. 1]")

    def test_kontakt(self):
        person = SimpleNamespace(first_name="Max", last_name="Mustermann",
                                 phone_number="+49000000000")
        self.assertEqual(_sonderinhalt(_msg(contact=person)),
                         "[Kontakt: Max Mustermann, +49000000000]")

    def test_kontakt_ohne_nachnamen(self):
        person = SimpleNamespace(first_name="Max", last_name=None,
                                 phone_number="+49000000000")
        self.assertEqual(_sonderinhalt(_msg(contact=person)),
                         "[Kontakt: Max, +49000000000]")

    def test_umfrage_nennt_frage_und_optionen(self):
        frage = SimpleNamespace(question="Heute oder morgen?", options=[
            SimpleNamespace(text="Heute"), SimpleNamespace(text="Morgen")])
        self.assertEqual(_sonderinhalt(_msg(poll=frage)),
                         "[Umfrage: Heute oder morgen? — Heute / Morgen]")

    def test_sticker_nennt_sein_emoji(self):
        self.assertEqual(_sonderinhalt(_msg(sticker=SimpleNamespace(emoji="👍"))),
                         "[Sticker 👍]")

    def test_sticker_ohne_emoji_faellt_auf_den_satznamen_zurueck(self):
        aufkleber = SimpleNamespace(emoji=None, set_name="Beispielsatz")
        self.assertEqual(_sonderinhalt(_msg(sticker=aufkleber)), "[Sticker Beispielsatz]")

    def test_sticker_ganz_ohne_angaben(self):
        aufkleber = SimpleNamespace(emoji=None, set_name=None)
        self.assertEqual(_sonderinhalt(_msg(sticker=aufkleber)), "[Sticker]")

    def test_wuerfel_nennt_das_ergebnis(self):
        self.assertEqual(_sonderinhalt(_msg(dice=SimpleNamespace(emoji="🎲", value=4))),
                         "[🎲 gewuerfelt: 4]")


class AbonnierteAktualisierungenTests(unittest.TestCase):
    """Telegram schickt NUR die angeforderten Arten — fehlt eine, kommt sie nie an."""

    def test_korrekturen_und_reaktionen_werden_angefordert(self):
        quelle = BOT.read_text(encoding="utf-8")
        for art in ("message", "edited_message", "callback_query", "message_reaction"):
            self.assertIn(f'"{art}"', quelle, f"{art} fehlt in allowed_updates")

    def test_bisher_verworfene_arten_haben_einen_handler(self):
        quelle = BOT.read_text(encoding="utf-8")
        for f in ("filters.LOCATION", "filters.VENUE", "filters.CONTACT",
                  "filters.POLL", "filters.Sticker.ALL", "filters.Dice.ALL",
                  "filters.ANIMATION", "filters.VIDEO_NOTE"):
            self.assertIn(f, quelle, f"{f} wird von keinem Handler abgedeckt")

    def test_bearbeitete_nachricht_greift_nicht_mehr_auf_update_message_zu(self):
        """Bei einer Korrektur ist `update.message` None — der Zugriff wuerde werfen."""
        quelle = BOT.read_text(encoding="utf-8")
        handler = quelle.split("async def _handle_message(")[1].split("async def ")[0]
        self.assertNotIn("update.message.", handler)


if __name__ == "__main__":
    unittest.main()
