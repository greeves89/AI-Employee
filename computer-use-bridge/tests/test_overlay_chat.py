"""Chat im Overlay: „Chat“ klappt ein Eingabefeld auf, Senden schickt den Text an
den Agenten. Reine Modell-Logik — die Oberflaechen (Mac/Windows) zeichnen nur."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tray_app  # noqa: E402


class OverlayChat(unittest.TestCase):
    def _arbeitet(self):
        m = tray_app.OverlayModell()
        m.verarbeiten({"type": "overlay_status", "zustand": "arbeitet", "text": "Öffnet Excel …"})
        return m

    def test_chat_klappt_das_feld_auf_und_zu(self):
        m = self._arbeitet()
        self.assertFalse(m.mit_eingabe)
        self.assertIsNone(m.klick("chat"))
        self.assertTrue(m.chat_offen and m.mit_eingabe)
        self.assertEqual(m.knoepfe()[0], ("senden", "Senden"))
        self.assertIn(("chat", "Chat zu"), m.knoepfe())
        m.klick("chat")
        self.assertFalse(m.chat_offen or m.mit_eingabe)

    def test_senden_schickt_den_text_und_schliesst(self):
        m = self._arbeitet()
        m.klick("chat")
        nachricht = m.klick("senden", "  nimm   die zweite\nSpalte ")
        self.assertEqual(nachricht, {"type": "overlay_chat", "text": "nimm die zweite Spalte"})
        self.assertFalse(m.chat_offen)
        self.assertEqual(m.art, "arbeitet")

    def test_leerer_text_schickt_nichts(self):
        m = self._arbeitet()
        m.klick("chat")
        self.assertIsNone(m.klick("senden", "   "))
        self.assertTrue(m.chat_offen)

    def test_ohne_laufenden_auftrag_zeigt_es_den_start(self):
        m = tray_app.OverlayModell()
        m.verarbeiten({"type": "overlay_status", "zustand": "fertig", "text": "Fertig."})
        m.klick("chat")
        self.assertEqual(m.klick("senden", "mach weiter")["type"], "overlay_chat")
        self.assertEqual(m.art, "arbeitet")

    def test_bei_einer_frage_bleiben_ja_und_nein(self):
        m = tray_app.OverlayModell()
        m.verarbeiten({"type": "vorschlag", "id": "v1", "text": "Titel vorschlagen?", "auftrag": "x"})
        m.klick("chat")
        knoepfe = [k for k, _ in m.knoepfe()]
        self.assertEqual(knoepfe[:3], ["senden", "ja", "nein"])

    def test_ausblenden_schliesst_den_chat(self):
        m = self._arbeitet()
        m.klick("chat")
        m.klick("aus")
        self.assertFalse(m.chat_offen or m.sichtbar)


if __name__ == "__main__":
    unittest.main()
