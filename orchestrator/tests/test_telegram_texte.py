"""Telegram spricht Deutsch und kommt ohne Emojis aus (#902).

Der Bot meldete Zustände als farbige Kreise, Bewertungen als Sterne und die
Kennzahlen eines Laufs als „⏱ 3.2s | 💰 $0.0123 | 🔄 2 turns“. Harte Vorgabe ist:
keine Emojis, echtes Deutsch. Die Texte entstehen an EINER Stelle
(``app.telegram.texte``), damit Sammel-Bot und Agenten-Bot gleich sprechen.
"""

import re
import unittest
from pathlib import Path
from unittest.mock import patch

from app.telegram import texte

APP = Path(__file__).resolve().parents[1] / "app"

#: Emoji-Bereiche (Bildzeichen, Symbole, Pfeile-Emojis, Sterne, Häkchen).
EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B50\u2B06\u2B07\u23E9-\u23FA"
    "\u231A\u231B\u2139\u2705\u274C\u2753\u203C\u2049\uFE0F]"
)


class ZustandAlsWort(unittest.TestCase):
    def test_bekannte_zustaende(self):
        self.assertEqual(texte.zustand_wort("running"), "läuft")
        self.assertEqual(texte.zustand_wort("idle"), "bereit")
        self.assertEqual(texte.zustand_wort("working"), "arbeitet")
        self.assertEqual(texte.zustand_wort("stopped"), "gestoppt")
        self.assertEqual(texte.zustand_wort("error"), "Fehler")

    def test_unbekannter_zustand(self):
        self.assertEqual(texte.zustand_wort("created"), "startet")
        self.assertEqual(texte.zustand_wort("irgendwas"), "unbekannt")
        self.assertEqual(texte.zustand_wort(None), "unbekannt")


class AufgabenStatus(unittest.TestCase):
    def test_wie_im_browser(self):
        self.assertEqual(texte.aufgaben_status("pending"), "Wartet")
        self.assertEqual(texte.aufgaben_status("completed"), "Erledigt")
        self.assertEqual(texte.aufgaben_status("sonderfall"), "sonderfall")


class Bewertung(unittest.TestCase):
    def test_sterne_als_text(self):
        self.assertEqual(texte.bewertung(4), "4 von 5")
        self.assertEqual(texte.bewertung(1), "1 von 5")


class Kennzahlen(unittest.TestCase):
    def test_volle_zeile_mit_anzeigewaehrung(self):
        with patch("app.telegram.texte.betrag_anzeigen", return_value="0,01 $"):
            zeile = texte.kennzahlen(3200, 0.0123, 2)
        self.assertEqual(zeile, "Dauer 3,2 s · Kosten 0,01 $ · 2 Runden")

    def test_eine_runde_ohne_kosten(self):
        self.assertEqual(texte.kennzahlen(7700, 0, 1), "Dauer 7,7 s · 1 Runde")

    def test_ohne_dauer_keine_zeile(self):
        self.assertEqual(texte.kennzahlen(0, 0.5, 3), "")

    def test_kein_emoji_kein_englisch(self):
        zeile = texte.kennzahlen(1000, 0, 2)
        self.assertIsNone(EMOJI.search(zeile))
        self.assertNotIn("turns", zeile)


class OhneEmojis(unittest.TestCase):
    def test_entfernt_emojis_und_doppelte_leerzeichen(self):
        self.assertEqual(texte.ohne_emojis("⚠️ *Zeitplan* verpasst"), "*Zeitplan* verpasst")
        self.assertEqual(texte.ohne_emojis("✅ 3/3 bestanden\n❌ 0 Fehler"), "3/3 bestanden\n0 Fehler")

    def test_laesst_text_und_umlaute_stehen(self):
        self.assertEqual(texte.ohne_emojis("Grüße — „Test“ · 5 €"), "Grüße — „Test“ · 5 €")


class TelegramQuelltextOhneEmojis(unittest.TestCase):
    """Kein Emoji mehr in den Texten des Telegram-Bots (Ordner ``app/telegram``)."""

    def test_ordner_telegram(self):
        for datei in sorted((APP / "telegram").rglob("*.py")):
            with self.subTest(datei=datei.name):
                treffer = [
                    f"{nr}: {zeile.strip()}"
                    for nr, zeile in enumerate(datei.read_text().splitlines(), 1)
                    if EMOJI.search(zeile) and not zeile.strip().startswith("#")
                ]
                self.assertEqual(treffer, [])


if __name__ == "__main__":
    unittest.main()
