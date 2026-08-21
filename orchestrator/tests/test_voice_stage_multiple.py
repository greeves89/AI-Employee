"""Mehrere Anzeigen stehen NEBENEINANDER, nicht uebereinander.

Meldung des Nutzers (2026-08-21): „wenn ich mir nun bilder anzeigen lasse, dann
kommen die nicht nebenher sondern UEBEREINANDER."

Ursache: die Buehne holte sich mit `media.find(...)` genau EIN Element — das
neueste. Jedes weitere Bild landete zwar im Zustand, legte sich dort aber
unsichtbar darunter. Wer beide Bildschirme aufnahm, sah trotzdem nur einen, und
das ohne jede Fehlermeldung.

Dazu kam: beide Screenshots hiessen im Backend „Bildschirm des Nutzers" — selbst
nebeneinander waeren sie nicht auseinanderzuhalten gewesen.
"""

import unittest
from pathlib import Path

_WURZEL = Path(__file__).resolve().parents[2]
_VOICE_TSX = (_WURZEL / "frontend" / "src" / "components" / "agents"
              / "voice-session.tsx").read_text()
_SESSION_PY = (_WURZEL / "orchestrator" / "app" / "services"
               / "realtime_voice_session.py").read_text()


class DieBuehneZeigtMehrAlsEinesTests(unittest.TestCase):
    def test_nicht_mehr_nur_das_neueste(self):
        self.assertNotIn('const stageItem = media.find(', _VOICE_TSX)
        self.assertIn("const stageItems = alleAnzeigen.slice(0, 4)", _VOICE_TSX)

    def test_die_anzeigen_werden_durchlaufen(self):
        """Ein einzelnes gerendertes Element waere derselbe Fehler in gruen."""
        self.assertIn("stageItems.map(", _VOICE_TSX)

    def test_es_gibt_ein_raster_mit_woertlichen_klassen(self):
        """Tailwind liest Text — ein zusammengebauter Klassenname fehlt im CSS."""
        for klasse in (
            "grid gap-3 grid-cols-1 md:grid-cols-2 xl:grid-cols-3",
            "grid gap-3 grid-cols-1 md:grid-cols-2",
            "grid gap-3 grid-cols-1",
        ):
            self.assertIn(klasse, _VOICE_TSX, f"{klasse} fehlt woertlich")
        self.assertNotRegex(_VOICE_TSX, r"grid-cols-\d?\[?\$\{")

    def test_zwei_bilder_oeffnen_die_buehne_von_selbst(self):
        """Sonst muesste der Nutzer erst eine Spalte einklappen, damit
        nebeneinander ueberhaupt Platz hat."""
        zeile = [z for z in _VOICE_TSX.splitlines() if "const buehneWeit =" in z][0]
        self.assertIn("stageItems.length > 1", zeile)

    def test_jede_anzeige_bleibt_einzeln_schliessbar(self):
        self.assertIn("onAusblenden={() => setMedia((prev) => prev.filter((m) => m !== item))}",
                      _VOICE_TSX)

    def test_zu_viele_anzeigen_werden_nicht_stillschweigend_verschluckt(self):
        self.assertIn("alleAnzeigen.length > stageItems.length", _VOICE_TSX)
        self.assertIn("ausgeblendet", _VOICE_TSX)


class ScreenshotsSindAuseinanderzuhaltenTests(unittest.TestCase):
    def test_die_bildschirmnummer_steht_in_der_beschriftung(self):
        self.assertIn('beschriftung = f"Bildschirm {nr}" if nr else', _SESSION_PY)

    def test_ohne_nummer_bleibt_die_alte_beschriftung(self):
        """Ein Rechner mit einem Bildschirm liefert keine Nummer — dort darf
        nicht ploetzlich „Bildschirm None" stehen."""
        self.assertIn('"Bildschirm des Nutzers"', _SESSION_PY)

    def test_die_bildgroesse_steht_dabei(self):
        block = _SESSION_PY.split('beschriftung = f"Bildschirm', 1)[1][:400]
        self.assertIn("groesse['w']", block)
        self.assertIn("groesse['h']", block)


if __name__ == "__main__":
    unittest.main()
