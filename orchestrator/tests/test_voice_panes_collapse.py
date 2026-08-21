"""Die Seitenspalten der Sprachansicht lassen sich wegklappen.

Wunsch des Nutzers (2026-08-21): „ich moechte gern links das gespraech
einklappbar machen, damit dann die screenshots groesser werden und aufgabe auch
einklappen. DAFUER dann das interact VIEW UI fuer den agenten GROESSER machen."

Die Buehne in der Mitte hing an `max-w-md` und `max-h-72`. Ohne das Loesen
dieser beiden Deckel bringt eine eingeklappte Spalte NICHTS — der Screenshot
bliebe gleich klein, daneben waere nur Luft.

Zweite, unsichtbare Falle: Tailwind liest die Dateien als TEXT. Ein zur Laufzeit
zusammengebauter Klassenname (`lg:grid-cols-[${x}_...]`) landet nie im fertigen
CSS; das Raster saehe im Browser unveraendert aus, ohne jede Fehlermeldung.
Genau davor schuetzt dieser Test — deshalb prueft er hier ausnahmsweise den
Quelltext: die Falle IST textueller Natur.
"""

import re
import unittest
from pathlib import Path

_DATEI = (Path(__file__).resolve().parents[2]
          / "frontend" / "src" / "components" / "agents" / "voice-session.tsx")
_SRC = _DATEI.read_text()


class SpaltenLassenSichKlappenTests(unittest.TestCase):
    def test_beide_seiten_haben_einen_zustand(self):
        self.assertIn("const [gesprAus, setGesprAus]", _SRC)
        self.assertIn("const [aufgabenAus, setAufgabenAus]", _SRC)

    def test_beide_seiten_haben_einen_knopf_zum_zuklappen(self):
        self.assertIn("setGesprAus(true)", _SRC)
        self.assertIn("setAufgabenAus(true)", _SRC)

    def test_und_einen_weg_zurueck(self):
        """Eine Spalte, die man nicht wieder aufbekommt, ist verloren."""
        self.assertIn("setGesprAus(false)", _SRC)
        self.assertIn("setAufgabenAus(false)", _SRC)

    def test_die_einstellung_ueberlebt_das_gespraech(self):
        self.assertIn('SPALTEN_KEY = "voice-spalten-eingeklappt"', _SRC)
        self.assertIn("localStorage.setItem(\n        SPALTEN_KEY,", _SRC)

    def test_gespeicherter_stand_darf_nicht_die_seite_zerlegen(self):
        """Im privaten Modus wirft schon der Zugriff auf localStorage."""
        block = _SRC.split("SPALTEN_KEY = ", 1)[1][:1400]
        self.assertGreaterEqual(block.count("catch {"), 2)


class DieMitteBekommtDenPlatzWirklichTests(unittest.TestCase):
    def test_die_rasterklassen_stehen_woertlich_im_quelltext(self):
        """Sonst kennt Tailwind sie nicht und das Raster bleibt, wie es war."""
        for klasse in (
            "lg:grid-cols-[2.75rem_minmax(280px,3fr)_2.75rem]",
            "lg:grid-cols-[2.75rem_minmax(280px,2fr)_1fr]",
            "lg:grid-cols-[1fr_minmax(280px,2fr)_2.75rem]",
            "lg:grid-cols-[1fr_minmax(280px,1.1fr)_1fr]",
        ):
            self.assertIn(klasse, _SRC, f"{klasse} fehlt woertlich")

    def test_kein_zusammengebauter_rasterklassenname(self):
        self.assertNotRegex(_SRC, r"grid-cols-\[\$\{")

    def test_die_buehne_loest_ihren_breitendeckel(self):
        """`max-w-md` haelt den Screenshot bei 28rem fest — der Punkt der Uebung."""
        self.assertIn("buehneWeit", _SRC)
        stelle = _SRC.index("buehneWeit ? \"max-w-full\" : \"max-w-md\"")
        self.assertGreater(stelle, 0)

    def test_und_ihren_hoehendeckel(self):
        self.assertIn('buehneWeit ? "max-h-[62vh]" : "max-h-72"', _SRC)

    def test_breit_gilt_sobald_eine_seite_zu_ist(self):
        zeile = re.search(r"const buehneWeit = .*", _SRC).group(0)
        self.assertIn("gesprAus || aufgabenAus", zeile)


if __name__ == "__main__":
    unittest.main()
