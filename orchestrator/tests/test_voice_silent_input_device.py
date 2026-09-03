"""Eine stumme Eingabequelle muss auffallen.

Befund #679: Die Sprachsitzung verbindet, der Agent begruesst hoerbar, der
Zustand steht dauerhaft auf „Hört zu …" — und nichts passiert. Keine Meldung,
kein Hinweis. Der Betroffene hat tagelang am Reverse-Proxy, an Bedrock und am
Netz gesucht.

Die Ursache lag im Browser: als Eingabegeraet war ein virtuelles Geraet
ausgewaehlt (von Videokonferenz-Software beim Installieren angelegt und oft als
Vorgabe gesetzt), das dauerhaft Nullen liefert. Nachgemessen: Spitzenpegel
0.0000 nach fuenf Sekunden normalem Sprechen.

Warum beide vorhandenen Schutznetze versagten:

1. Der Wachhund prueft, OB Blöcke ankommen. Sie kommen — sie sind nur leer.
2. Das Rauschtor fuellt zu leise Blöcke bewusst mit Nullen und sendet sie
   weiter, damit der Tonstrom lueckenlos bleibt. Eine stumme Quelle ist davon
   nicht zu unterscheiden.

Serverseitig sah alles gesund aus: Blöcke kamen an, der Keepalive hatte nichts
zu tun, und die Engine wartete auf ein Sprechende, das nie kam.
"""

import re
import unittest
from pathlib import Path

_VOICE = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "components"
          / "agents" / "voice-session.tsx").read_text()


class DerRohpegelWirdGemessenTests(unittest.TestCase):
    """Der entscheidende Punkt: VOR dem Rauschtor. Danach ist jede Quelle
    stumm, die unter der Schwelle liegt."""

    def test_es_gibt_eine_spitzenwert_messung(self):
        self.assertIn("let rohSpitze = 0", _VOICE)

    def test_sie_liegt_vor_dem_rauschtor(self):
        block = _VOICE.split("const pegel = Math.sqrt(summe / input.length);", 1)[1][:900]
        spitze = block.index("rohSpitze")
        tor = block.index("empfindlichkeitRef.current")
        self.assertLess(spitze, tor, "Nach dem Tor gemessen waere der Wert wertlos")

    def test_der_wachhund_prueft_den_pegel_nicht_nur_die_anzahl(self):
        self.assertIn("rohSpitze < STUMM_EPSILON", _VOICE)

    def test_er_schlaegt_nicht_an_wenn_gar_nichts_kommt(self):
        """Dafuer gibt es den anderen Wachhund — zwei Meldungen fuer dieselbe
        Sache waeren nur verwirrend."""
        block = _VOICE.split("const STUMM_EPSILON", 1)[1][:400]
        self.assertIn("framesSent === 0) return", block)

    def test_die_schwelle_ist_praktisch_null(self):
        """Ein echtes Mikrofon zeigt auch in Ruhe Grundrauschen deutlich
        darueber; zu hoch angesetzt wuerde die Meldung bei leisen Raeumen
        falsch anschlagen."""
        wert = float(re.search(r"const STUMM_EPSILON = ([\d.]+)", _VOICE).group(1))
        self.assertLess(wert, 0.005)
        self.assertGreater(wert, 0)


class DieMeldungNenntDasGeraetTests(unittest.TestCase):
    """Der Geraetename ist der entscheidende Teil — er zeigt auf einen Blick,
    dass ein virtuelles Geraet aktiv ist."""

    def test_der_name_wird_geholt(self):
        self.assertIn("stream.getAudioTracks()[0]?.label", _VOICE)

    def test_er_steht_in_der_meldung(self):
        block = _VOICE.split("rohSpitze < STUMM_EPSILON", 1)[1][:800]
        self.assertIn("eingabeGeraet", block)

    def test_ohne_namen_gibt_es_trotzdem_eine_meldung(self):
        """Manche Browser geben das Label erst nach erteilter Erlaubnis frei."""
        block = _VOICE.split("rohSpitze < STUMM_EPSILON", 1)[1][:800]
        self.assertIn("Das gewählte Eingabegerät liefert kein Signal", block)

    def test_die_meldung_sagt_was_zu_tun_ist(self):
        block = _VOICE.split("rohSpitze < STUMM_EPSILON", 1)[1][:900]
        self.assertIn("echtes Mikrofon", block)
        self.assertIn("virtuelle", block.lower())


class DerReglerZeigtJetztEtwasAnTests(unittest.TestCase):
    """Ohne Pegelanzeige ist „Tor zu, Schwelle zu hoch" von „Quelle liefert
    nichts" nicht zu unterscheiden — beides sieht identisch aus."""

    def test_es_gibt_einen_pegelzustand(self):
        self.assertIn("const [pegel, setPegel] = useState(0)", _VOICE)

    def test_die_anzeige_ist_gedrosselt(self):
        """128 Blöcke je Sekunde in den Zustand zu schreiben wuerde die
        Oberflaeche mehr beschaeftigen als die Aufnahme selbst."""
        block = _VOICE.split("if (pegel > rohSpitze) rohSpitze = pegel;", 1)[1][:400]
        self.assertIn("jetzt - letzteAnzeige > 100", block)

    def test_die_schwelle_ist_eingezeichnet(self):
        self.assertIn("Ab hier hört der Agent zu", _VOICE)

    def test_der_balken_haengt_am_gemessenen_pegel(self):
        self.assertIn("(pegel / 0.06) * 100", _VOICE)

    def test_bei_stille_steht_es_auch_am_regler(self):
        self.assertIn("kein Signal vom Mikrofon", _VOICE)


if __name__ == "__main__":
    unittest.main()
