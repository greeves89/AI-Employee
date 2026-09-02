"""Ein Sprachfehler darf keine Diagnose-Historie vernichten.

Vorfall 31.08.2026: In 85 Sekunden 441 fehlgeschlagene Sprach-Starts und 473
`Unclosed client session`. Das Fehlerprotokoll wuchs auf 668 KB in einer Stunde
(normal: 2 MB in dreieinhalb Tagen), erzwang eine ausserplanmaessige Rotation
und schob die aelteste Datei — Zeitraum 18.07. bis 22.08. — aus dem Fenster.
**Ein Monat Diagnose-Historie ist dadurch verloren.** Das ist der eigentliche
Schaden; die auslösende Ursache selbst war klein und ist getrennt behoben.

Zwei voneinander unabhaengige Defekte haben sich dabei verstaerkt (#691).
"""

import re
import unittest
from pathlib import Path

_WURZEL = Path(__file__).resolve().parents[2]
_WS = (_WURZEL / "orchestrator" / "app" / "api" / "ws.py").read_text()
_VOICE = (_WURZEL / "frontend" / "src" / "components" / "agents"
          / "voice-session.tsx").read_text()


class DieSitzungLecktNichtMehrTests(unittest.TestCase):
    """`init()` baut den Client — und damit eine HTTP-Sitzung — BEVOR es den
    Datenstrom oeffnet. Scheitert es danach, blieb der Client offen zurueck."""

    def _fehlerzweig(self) -> str:
        return _WS.split("voice session init failed agent=", 1)[1][:1200]

    def test_die_sitzung_wird_geschlossen(self):
        self.assertIn("await session.close()", self._fehlerzweig())

    def test_das_geschieht_vor_dem_schliessen_der_verbindung(self):
        """Danach ist die Funktion verlassen — das Aufraeumen kaeme nie dran."""
        zweig = self._fehlerzweig()
        self.assertLess(zweig.index("await session.close()"),
                        zweig.index("websocket.close(code=1011"))

    def test_ein_fehler_beim_aufraeumen_verschluckt_den_eigentlichen_nicht(self):
        zweig = self._fehlerzweig()
        block = zweig.split("await session.close()", 1)[1][:300]
        self.assertIn("except Exception", block)

    def test_es_gilt_fuer_JEDE_ausnahme(self):
        """Nicht nur fuer die eine bekannte Ursache — jede Ausnahme in `init()`
        leckte."""
        self.assertIn("except Exception as e:  # noqa: BLE001", _WS)


class DieReconnectBremseHaengtNichtAmZaehlerTests(unittest.TestCase):
    """Die vorhandene Grenze von 8 Versuchen war wirkungslos: der Zaehler wird
    zurueckgesetzt, sobald Gespraechsdaten eintreffen. Kommt vom Server auch im
    Fehlerfall noch ein Ereignis, faengt das Zaehlen von vorn an — gemessen
    wurden 441 Versuche statt 8."""

    def test_es_gibt_eine_zeitfenster_bremse(self):
        self.assertIn("VERSUCHSFENSTER_MS", _VOICE)
        self.assertIn("MAX_VERSUCHE_IM_FENSTER", _VOICE)

    def test_sie_wird_von_keinem_zaehler_ruecksetzen_beruehrt(self):
        """Das ist ihr ganzer Zweck — sonst waere sie dieselbe Grenze nochmal."""
        for stelle in re.findall(r"reconnectsRef\.current = 0[^\n]*", _VOICE):
            self.assertNotIn("versucheImFenster", stelle)

    def test_sie_greift_vor_dem_zaehler(self):
        block = _VOICE.split("const scheduleReconnect", 1)[1][:1800]
        self.assertLess(block.index("MAX_VERSUCHE_IM_FENSTER"),
                        block.index("MAX_VOICE_RECONNECTS"))

    def test_alte_versuche_fallen_aus_dem_fenster(self):
        """Ohne das waere es keine Rate, sondern eine Lebenszeit-Obergrenze —
        eine lange, gesunde Sitzung wuerde irgendwann nicht mehr verbinden."""
        block = _VOICE.split("const scheduleReconnect", 1)[1][:1200]
        self.assertIn("jetzt - t < VERSUCHSFENSTER_MS", block)

    def test_die_grenze_deckelt_das_gemessene_symptom(self):
        """441 Versuche in 85 s duerfen nicht mehr moeglich sein."""
        fenster = int(re.search(r"VERSUCHSFENSTER_MS = ([\d_]+)", _VOICE)
                      .group(1).replace("_", ""))
        grenze = int(re.search(r"MAX_VERSUCHE_IM_FENSTER = (\d+)", _VOICE).group(1))
        pro_minute = grenze * 60_000 / fenster
        self.assertLessEqual(pro_minute, 15,
                             "Mehr als 15 Versuche je Minute waeren wieder ein Sturm")


class DerAbstandWaechstTests(unittest.TestCase):
    def test_kein_fester_abstand_mehr(self):
        """600 ms konstant gegen einen Fehler, der nie von allein weggeht, ist
        nur schnelleres Scheitern."""
        self.assertNotIn("void connectWs(); }, 600)", _VOICE)

    def test_er_verdoppelt_sich(self):
        block = _VOICE.split("const scheduleReconnect", 1)[1][:2000]
        self.assertIn("BACKOFF_START_MS * 2 **", block)

    def test_er_hat_einen_deckel(self):
        """Ohne Deckel waere der zehnte Versuch in zehn Minuten."""
        self.assertIn("BACKOFF_DECKEL_MS", _VOICE)
        deckel = int(re.search(r"BACKOFF_DECKEL_MS = ([\d_]+)", _VOICE)
                     .group(1).replace("_", ""))
        self.assertLessEqual(deckel, 60_000)

    def test_der_nutzerklick_raeumt_die_bremse_weg(self):
        """Wer selbst auf „Neu verbinden" drueckt, hat sich entschieden — das
        ist kein Sturm."""
        block = _VOICE.split("neuVerbindenRef.current = () =>", 1)[1][:600]
        self.assertIn("versucheImFenster.current = []", block)


if __name__ == "__main__":
    unittest.main()
