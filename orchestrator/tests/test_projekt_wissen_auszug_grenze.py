"""Issue #979: Der Auftrag nannte eine ZEILEN-Grenze, durchgesetzt wird eine ZEICHEN-Grenze.

Eine ``index.md``, die sich exakt an die Vorgabe „höchstens 40 Zeilen" hielt, war
rund 2500 Zeichen lang — in den Projektauftrag gingen davon 1500. Die letzten 40 %
fielen still weg, und zwar systematisch das Seitenende: Vereinbarungen und
Ansprechpartner, also genau die Pflichtabschnitte, die die Vorgabe selbst nennt.

Für den schreibenden Agenten war das unsichtbar: ``wc -l`` bestätigte 40 Zeilen.

Die Tests sichern drei Dinge: die Auftragszeile nennt die durchgesetzte Grenze in
ihrer Einheit, eine vorgabenkonforme Seite überlebt den Auszug vollständig, und
wer die Grenze beim Schreiben überschreitet, erfährt es sofort — bis hinaus in
die Antworttexte, die Agent-Client und MCP-Server selbst zusammenbauen.
"""

import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.core import projekt_wissen, vault

REPO = Path(__file__).resolve().parents[2]
ZAHL_ZEILEN = re.compile(r"\d+\s*Zeilen")


def _asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)


class AuftragNenntDieDurchgesetzteGrenze(unittest.TestCase):
    def test_zeichen_und_zwar_die_echte_zahl(self):
        zeile = projekt_wissen.auftrag_index_zeile()
        self.assertIn(str(projekt_wissen.AUSZUG_ZEICHEN), zeile)
        self.assertIn("ZEICHEN", zeile)
        self.assertIn(projekt_wissen.AUSZUG_SEITE, zeile)

    def test_keine_zeilenzahl_als_grenze(self):
        """„40 Zeilen" war die Fehlerklasse: eine Zahl in der falschen Einheit."""
        self.assertIsNone(ZAHL_ZEILEN.search(projekt_wissen.auftrag_index_zeile()))

    def test_die_route_baut_keine_eigene_zahl_mehr(self):
        """Die Quelle der Zahl ist ``AUSZUG_ZEICHEN``, nicht ein zweiter Fließtext."""
        quelle = (REPO / "orchestrator/app/api/teams.py").read_text(encoding="utf-8")
        treffer = ZAHL_ZEILEN.findall(quelle)
        self.assertEqual(treffer, [], f"Zeilen-Grenze im Auftragstext: {treffer}")
        self.assertIn("projekt_wissen.auftrag_index_zeile()", quelle)


class VorgabenkonformeSeiteUeberlebtDenAuszug(unittest.TestCase):
    PFLICHT = ("Ziel", "Stand", "Wichtige Orte", "Vereinbarungen", "Ansprechpartner")

    def _seite(self, laenge: int) -> str:
        """Eine wohlgeformte ``index.md`` mit allen Pflichtabschnitten, genau ``laenge`` Zeichen.

        Der Füllstoff steht VOR dem letzten Abschnitt: so fällt beim Kürzen genau
        der Pflichtabschnitt am Seitenende weg — der echte Schaden aus #979. Kein
        ``text[:laenge]`` am Ende: das würde die Zusicherung „wird nicht gekürzt"
        selbst erfüllen und den Test damit aussagelos machen.
        """
        kopf = "# Projekt\n\n"
        abschnitte = [f"## {name}\n\n- Inhalt zu {name}.\n" for name in self.PFLICHT]
        rumpf = kopf + "\n".join(abschnitte[:-1]) + "\n"
        schluss = abschnitte[-1].rstrip()  # ohne Zeilenende: ``auszug_text`` strippt, sonst wäre len(auszug) < len(seite)
        fehlt = laenge - len(rumpf) - len(schluss) - 1  # 1 für das trennende \n
        self.assertGreater(fehlt, 0, "Die Pflichtabschnitte passen nicht in die Vorgabe.")
        seite = rumpf + ("." * fehlt) + "\n" + schluss
        self.assertEqual(len(seite), laenge)
        self.assertTrue(seite.endswith(schluss), "Letzter Pflichtabschnitt ist angeschnitten.")
        return seite

    def test_die_im_auftrag_genannte_laenge_wird_nicht_gekuerzt(self):
        genannt = re.search(r"(\d+)\s*ZEICHEN", projekt_wissen.auftrag_index_zeile())
        self.assertIsNotNone(genannt, "Der Auftrag nennt keine Zeichen-Grenze.")
        grenze = int(genannt.group(1))
        seite = self._seite(grenze)
        self.assertEqual(len(seite), grenze)
        auszug, gekuerzt = projekt_wissen.auszug_text(seite)
        self.assertFalse(gekuerzt, "Der Auftrag nennt eine Länge, die der Auszug gar nicht trägt.")
        self.assertEqual(auszug, seite)
        for name in self.PFLICHT:
            with self.subTest(abschnitt=name):
                self.assertIn(name, auszug)

    def test_gegenprobe_die_alte_vorgabe_verliert_das_seitenende(self):
        """Ohne die Korrektur wäre der Test oben grün geblieben — hier ist der Schaden.

        40 Zeilen deutscher Prosa (rund 2500 Zeichen) verlieren den letzten
        Pflichtabschnitt. Steht diese Zusicherung nicht da, kann der Test oben
        auch grün sein, weil ``auszug_text`` nie kürzt.
        """
        seite = self._seite(2500)
        auszug, gekuerzt = projekt_wissen.auszug_text(seite)
        self.assertTrue(gekuerzt)
        self.assertIn(projekt_wissen.AUSZUG_MARKER, auszug)
        self.assertNotIn(self.PFLICHT[-1], auszug)

    def test_grenze_ist_der_letzte_ungekuerzte_wert(self):
        g = projekt_wissen.AUSZUG_ZEICHEN
        self.assertFalse(projekt_wissen.auszug_text("x" * g)[1])
        self.assertTrue(projekt_wissen.auszug_text("x" * (g + 1))[1])

    def test_umgebender_leerraum_zaehlt_nicht_mit(self):
        """``auszug`` liest mit ``strip`` — der Hinweis muss dieselbe Länge messen."""
        g = projekt_wissen.AUSZUG_ZEICHEN
        self.assertFalse(projekt_wissen.auszug_text("\n\n" + "x" * g + "\n\n")[1])


class DerLeserBenutztDIESELBEGrenze(unittest.TestCase):
    """``auszug`` ist die Stelle, die in den Projektauftrag schreibt.

    Hätte sie ihren eigenen Längenvergleich behalten, wäre der Hinweis beim
    Schreiben ein Versprechen, das der Leser nicht hält.
    """

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.brain = SimpleNamespace(host_path=self.root, label="brain-test")
        self.team = SimpleNamespace(id="p1", wissen_slug="projekt-p1")

    def _auszug(self, inhalt):
        vault.write_file(self.root, "index.md", inhalt)
        with patch("app.core.projekt_wissen.brain_des_projekts", new=AsyncMock(return_value=self.brain)):
            return _asyncio_run(projekt_wissen.auszug(None, self.team))

    def test_an_der_grenze_kommt_alles_an(self):
        inhalt = "x" * projekt_wissen.AUSZUG_ZEICHEN
        self.assertEqual(self._auszug(inhalt), inhalt)

    def test_einen_ueber_der_grenze_steht_der_marker(self):
        auszug = self._auszug("x" * (projekt_wissen.AUSZUG_ZEICHEN + 1))
        self.assertIn(projekt_wissen.AUSZUG_MARKER, auszug)
        self.assertEqual(len(auszug), projekt_wissen.AUSZUG_ZEICHEN + 1 + len(projekt_wissen.AUSZUG_MARKER))


class VorlageNenntDieGrenzeUndPasstHinein(unittest.TestCase):
    def test_vorlage_nennt_die_echte_zahl(self):
        gerendert = projekt_wissen.index_vorlage("Projekt", "Ziel des Projekts")
        self.assertIn(str(projekt_wissen.AUSZUG_ZEICHEN), gerendert)
        self.assertNotIn("{", gerendert, "Platzhalter in der Vorlage nicht ersetzt")
        self.assertIsNone(ZAHL_ZEILEN.search(gerendert))

    def test_vorlage_legt_jeden_im_auftrag_verlangten_abschnitt_an(self):
        """Sonst verlangt der Auftrag Abschnitte, die die Startseite nicht vorsieht —
        und der Agent erfindet sie an einer beliebigen Stelle oder lässt sie weg."""
        gerendert = projekt_wissen.index_vorlage("Projekt", "Ziel")
        zeile = projekt_wissen.auftrag_index_zeile()
        verlangt = [t.strip(" .`") for t in zeile.split(":", 1)[1].split(".", 1)[0].split(",")]
        self.assertGreaterEqual(len(verlangt), 4, "Pflichtliste nicht erkannt — Testaufbau prüfen.")
        for name in verlangt:
            with self.subTest(abschnitt=name):
                self.assertIn(f"## {name[0].upper() + name[1:]}", gerendert)

    def test_vorlage_selbst_wird_nicht_gekuerzt(self):
        """Eine Startseite, die schon beim Anlegen über der Grenze liegt, wäre absurd."""
        gerendert = projekt_wissen.index_vorlage("Projekt", "Ziel " * 20)
        self.assertFalse(projekt_wissen.auszug_text(gerendert)[1])


class SchreibenMeldetDenSchnitt(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.brain = SimpleNamespace(host_path=self.root, label="brain-test")
        self.patcher = patch("app.services.vault_indexer.index_file", new=AsyncMock())
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def _schreiben(self, pfad, inhalt):
        return _asyncio_run(projekt_wissen.schreiben(None, self.brain, pfad, inhalt))

    def test_zu_lange_indexseite_bekommt_einen_hinweis(self):
        r = self._schreiben("index.md", "x" * (projekt_wissen.AUSZUG_ZEICHEN + 1))
        self.assertIn("hinweis", r)
        self.assertIn(str(projekt_wissen.AUSZUG_ZEICHEN), r["hinweis"])

    def test_passende_indexseite_bekommt_keinen(self):
        r = self._schreiben("index.md", "x" * projekt_wissen.AUSZUG_ZEICHEN)
        self.assertNotIn("hinweis", r)

    def test_andere_seite_bekommt_keinen(self):
        """Trennschärfe: nur ``index.md`` geht in den Auftrag, jede andere Seite ganz."""
        r = self._schreiben("architektur.md", "x" * (projekt_wissen.AUSZUG_ZEICHEN * 3))
        self.assertNotIn("hinweis", r)

    def test_schreibweise_des_pfads_aendert_das_urteil_nicht(self):
        """``./index.md`` ist dieselbe Datei, die ``auszug`` liest."""
        r = self._schreiben("./index.md", "x" * (projekt_wissen.AUSZUG_ZEICHEN + 1))
        self.assertIn("hinweis", r)

    def test_anhaengen_meldet_es_auch(self):
        """``projekt_wissen_ergaenzen`` ist der Weg, auf dem eine Seite langsam wächst."""
        self._schreiben("index.md", "# Projekt\n")
        r = _asyncio_run(projekt_wissen.anhaengen(
            None, self.brain, "index.md", "y" * (projekt_wissen.AUSZUG_ZEICHEN + 1)))
        self.assertIn("hinweis", r)

    def test_die_seite_bleibt_vollstaendig_gespeichert(self):
        """Der Hinweis ist eine Rückmeldung, keine Kürzung der Datei."""
        inhalt = "x" * (projekt_wissen.AUSZUG_ZEICHEN + 500)
        self._schreiben("index.md", inhalt)
        self.assertEqual(vault.read_file(self.root, "index.md"), inhalt)


class AgentseitigeAntworttexteNehmenDenHinweisMit(unittest.TestCase):
    """Beide Agentenwege bauen ihren Antworttext SELBST aus einzelnen Feldern.

    Genau dort wäre der Hinweis verschwunden, ohne dass irgendein Test rot würde:
    der Server liefert ihn, der Client wirft ihn weg (die Klasse „Rückgabewert
    ignoriert" aus Issue #977).
    """

    def test_beide_wege_fragen_das_feld_in_beiden_zweigen_ab(self):
        """Verhalten geprüft wird in ``agent/tests/test_projekt_wissen_auszug_hinweis.py``
        und ``agent/mcp/projekt.test.mjs``; hier bleibt nur, dass kein Zweig fehlt."""
        py = (REPO / "agent/app/tools/api_client.py").read_text(encoding="utf-8")
        for name in ("projekt_wissen_schreiben", "projekt_wissen_ergaenzen"):
            with self.subTest(methode=name):
                rumpf = py.split(f"async def {name}(", 1)[1].split("\n    async def ", 1)[0]
                self.assertIn("hinweis", rumpf, f"{name} verschluckt den Hinweis")
        mjs = (REPO / "agent/mcp/orchestrator-server.mjs").read_text(encoding="utf-8")
        for zweig in ("r.hinweis ? `${meldung} ${r.hinweis}`", "r.hinweis ? `${ergaenzt} ${r.hinweis}`"):
            with self.subTest(zweig=zweig):
                self.assertIn(zweig, mjs)
        mjs = (REPO / "agent/mcp/orchestrator-server.mjs").read_text(encoding="utf-8")
        for zweig in ("r.hinweis ? `${meldung} ${r.hinweis}`", "r.hinweis ? `${ergaenzt} ${r.hinweis}`"):
            with self.subTest(zweig=zweig):
                self.assertIn(zweig, mjs)

    def test_beide_werkzeugbeschreibungen_nennen_die_grenze(self):
        """Und zwar für BEIDE Schreibwerkzeuge: ``ergaenzen`` ist der Weg, auf dem
        ``index.md`` langsam über die Grenze wächst (``core/projekt_takt`` schickt
        den Lead ausdrücklich dorthin)."""
        for rel in ("agent/app/tools/definitions.py", "agent/mcp/orchestrator-server.mjs"):
            with self.subTest(datei=rel):
                quelle = (REPO / rel).read_text(encoding="utf-8")
                self.assertEqual(quelle.count(f"first {projekt_wissen.AUSZUG_ZEICHEN}"), 2,
                                 "schreiben UND ergaenzen müssen die Grenze nennen")

    # Der Mensch im Web-Editor hatte dieselbe Blindstelle wie der Agent:
    # ``PUT /wissen/datei`` hat kein ``response_model``, das Feld kommt also im
    # Browser an — und wurde dort weggeworfen. Je Stelle ein eigener Test, damit
    # die Fehlermeldung sagt, WELCHE fehlt.
    def test_web_editor_kennt_dieselbe_zahl(self):
        tsx = (REPO / "frontend/src/components/projekte/projekt-wissen.tsx").read_text(encoding="utf-8")
        self.assertIn(f"const AUSZUG_ZEICHEN = {projekt_wissen.AUSZUG_ZEICHEN};", tsx)

    def test_web_editor_zeigt_den_hinweis_beim_speichern(self):
        tsx = (REPO / "frontend/src/components/projekte/projekt-wissen.tsx").read_text(encoding="utf-8")
        rumpf = tsx.split("const speichern = async", 1)[1].split("\n  const ", 1)[0]
        self.assertIn("r?.hinweis", rumpf)

    def test_web_editor_zeigt_den_hinweis_beim_zurueckholen(self):
        """Eine lange Seite kann auch über das Zurückholen einer Version entstehen."""
        tsx = (REPO / "frontend/src/components/projekte/projekt-wissen.tsx").read_text(encoding="utf-8")
        rumpf = tsx.split("const zurueckholen = async", 1)[1].split("\n  const ", 1)[0]
        self.assertIn("r?.hinweis", rumpf)

    def test_der_typ_im_frontend_kennt_das_feld(self):
        """Ohne das Feld im Typ ist jede Abfrage darauf ein TypeScript-Fehler."""
        api_ts = (REPO / "frontend/src/lib/api.ts").read_text(encoding="utf-8")
        self.assertIn("hinweis?: string;", api_ts)


if __name__ == "__main__":
    unittest.main()
