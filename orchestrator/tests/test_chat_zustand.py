"""Abnahme v1.362.1, Chat: Adresse, Rückfrage-Karte, Ziel-Banner.

Geprueft wird das Verhalten von ``frontend/src/lib/chat-zustand.ts``, indem Node
die Datei wirklich ausfuehrt (Typen werden nur abgestreift):

* #900 „Neues Gespräch“ setzte eine NEUE ``?session=`` — die Kennung eines
  Gespraechs ohne Nachricht, das es nach F5 nicht gibt; der Chat sprang dann ins
  vorige zurueck. Jetzt: Parameter weg, ``?neu=1`` haelt die bewusste Wahl über F5;
  erst die erste Nachricht schreibt die neue Kennung in die Adresse.
* Eine beantwortete Rückfrage zeigt ihren Zustand und ist nicht mehr bedienbar;
  eine Abfrage, die vor der Antwort losging, holt sie nicht als offen zurück.
* „Ziel erreicht nach 1 Runde“ stand auch nach spaeteren Nachrichten noch unten.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path

MODUL = Path(__file__).resolve().parents[2] / "frontend/src/lib/chat-zustand.ts"
NODE = shutil.which("node")


def _ausfuehren(ausdruck: str):
    skript = (
        f"import * as m from {json.dumps(MODUL.as_uri())};\n"
        f"process.stdout.write(JSON.stringify({ausdruck}));\n"
    )
    ergebnis = subprocess.run(
        [NODE, "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", skript],
        capture_output=True, text=True, timeout=30,
    )
    if ergebnis.returncode != 0:
        raise AssertionError(ergebnis.stderr)
    return json.loads(ergebnis.stdout)


def _j(wert) -> str:
    return json.dumps(wert)


@unittest.skipUnless(NODE, "Node.js nicht installiert")
class GespraechInDerAdresse(unittest.TestCase):
    def suche(self, suche, gespraech, vorher):
        return _ausfuehren(f"m.gespraechsSuche({_j(suche)}, {_j(gespraech)}, {_j(vorher)})")

    def test_neues_gespraech_entfernt_den_parameter(self):
        self.assertEqual(self.suche("?session=alt123", None, "alt123"), "neu=1")

    def test_andere_parameter_bleiben(self):
        self.assertEqual(self.suche("?tab=chat&session=alt123", None, "alt123"), "tab=chat&neu=1")

    def test_erste_nachricht_schreibt_die_neue_kennung(self):
        self.assertEqual(self.suche("?neu=1", "frisch1", None), "session=frisch1")

    def test_f5_im_neuen_gespraech_aendert_nichts(self):
        """Nach F5 meldet der Chat „kein Gespräch“ — ``neu=1`` muss bleiben."""
        self.assertIsNone(self.suche("?neu=1", None, None))

    def test_ohne_vorheriges_gespraech_kein_neu_parameter(self):
        """Ein Agent ohne Gespräche bekommt keinen ``neu=1`` in die Adresse."""
        self.assertIsNone(self.suche("", None, None))

    def test_gleiche_kennung_ist_keine_aenderung(self):
        self.assertIsNone(self.suche("?session=abc", "abc", "abc"))

    def test_wechsel_auf_anderes_gespraech(self):
        self.assertEqual(self.suche("?session=abc", "def", "abc"), "session=def")


@unittest.skipUnless(NODE, "Node.js nicht installiert")
class GemeldetesGespraech(unittest.TestCase):
    def test_leeres_neues_gespraech_wird_nicht_gemeldet(self):
        """Die Kennung nach „Neues Gespräch“ kommt sofort vom Server — es gibt das
        Gespräch aber erst mit der ersten Nachricht."""
        self.assertIsNone(_ausfuehren('m.gemeldetesGespraech("frisch1", "frisch1")'))

    def test_nach_der_ersten_nachricht_schon(self):
        self.assertEqual(_ausfuehren('m.gemeldetesGespraech("frisch1", null)'), "frisch1")

    def test_altes_gespraech_bleibt_gemeldet(self):
        self.assertEqual(_ausfuehren('m.gemeldetesGespraech("alt", "frisch1")'), "alt")

    def test_kein_gespraech(self):
        self.assertIsNone(_ausfuehren("m.gemeldetesGespraech(null, null)"))


@unittest.skipUnless(NODE, "Node.js nicht installiert")
class RueckfrageKarte(unittest.TestCase):
    LISTE = [
        {"approval_id": "1", "agent_id": "a1", "tool": "user_decision"},
        {"approval_id": "2", "agent_id": "a1", "tool": "reflection_change"},
        {"approval_id": "3", "agent_id": "b2", "tool": "user_decision"},
        {"approval_id": "4", "agent_id": "a1", "tool": "user_decision"},
    ]

    def offen(self, erledigt):
        treffer = _ausfuehren(f"m.offeneRueckfrage({_j(self.LISTE)}, 'a1', new Set({_j(erledigt)}))")
        return treffer and treffer["approval_id"]

    def test_erste_offene_dieses_agenten(self):
        self.assertEqual(self.offen([]), "1")

    def test_hier_beantwortete_kommt_nicht_als_offen_zurueck(self):
        """Eine Abfrage, die vor der Antwort losging, liefert sie noch als offen."""
        self.assertEqual(self.offen(["1"]), "4")

    def test_nichts_offen(self):
        self.assertIsNone(self.offen(["1", "4"]))

    def test_ergebnis_einer_rueckfrage(self):
        self.assertEqual(_ausfuehren('m.rueckfrageErgebnis("beantwortet", "Abbrechen")'),
                         "Beantwortet: Abbrechen")

    def test_ergebnis_einer_freigabe_ohne_wahl(self):
        self.assertEqual(_ausfuehren('m.rueckfrageErgebnis("beantwortet", "")'), "Freigegeben")

    def test_ergebnis_einer_ablehnung(self):
        self.assertEqual(_ausfuehren('m.rueckfrageErgebnis("abgelehnt")'), "Abgelehnt")


@unittest.skipUnless(NODE, "Node.js nicht installiert")
class ZielBanner(unittest.TestCase):
    ZIEL_GESETZT = {"role": "user", "content": "/goal Schreib drei Zeilen über Ordnung im Büro"}
    ANTWORT = {"role": "assistant", "content": "Drei Zeilen …"}
    RUNDE = {"role": "user", "content": "Weiter am Ziel — Runde 2 von 20", "meta": {"source": "goal"}}
    NUTZER = {"role": "user", "content": "Führe den Befehl aus."}

    def sichtbar(self, status, nachrichten):
        return _ausfuehren(f"m.zielBannerSichtbar({_j(status)}, {_j(nachrichten)})")

    def test_erreicht_direkt_nach_der_letzten_runde_sichtbar(self):
        self.assertTrue(self.sichtbar("erreicht", [self.ZIEL_GESETZT, self.ANTWORT]))
        self.assertTrue(self.sichtbar("erreicht", [self.ZIEL_GESETZT, self.ANTWORT, self.RUNDE, self.ANTWORT]))

    def test_erreicht_verschwindet_mit_der_naechsten_nutzernachricht(self):
        self.assertFalse(self.sichtbar("erreicht", [self.ZIEL_GESETZT, self.ANTWORT, self.NUTZER]))
        self.assertFalse(self.sichtbar("erreicht", [self.ZIEL_GESETZT, self.ANTWORT, self.NUTZER, self.ANTWORT]))

    def test_grenze_verschwindet_ebenso(self):
        self.assertFalse(self.sichtbar("grenze", [self.ZIEL_GESETZT, self.ANTWORT, self.NUTZER]))

    def test_laufendes_ziel_bleibt_sichtbar(self):
        for status in ("aktiv", "pausiert"):
            with self.subTest(status=status):
                self.assertTrue(self.sichtbar(status, [self.ZIEL_GESETZT, self.ANTWORT, self.NUTZER]))

    def test_gestoppt_nie(self):
        self.assertFalse(self.sichtbar("gestoppt", [self.ZIEL_GESETZT]))

    def test_ziel_befehl_auch_als_ziel_geschrieben(self):
        self.assertTrue(self.sichtbar("erreicht", [{"role": "user", "content": "/ziel drei Zeilen"}, self.ANTWORT]))


if __name__ == "__main__":
    unittest.main()
