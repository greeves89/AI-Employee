"""Subagenten muessen auch nach einem Neuladen als solche erkennbar sein.

Ein Agent schickt Helfer los — Claude Code startet Subagenten im eigenen
Prozess (Werkzeug ``Agent``, frueher ``Task``), Codex und Custom-LLM
delegieren ueber die Plattform an andere Agenten (``create_task``,
``delegate_and_wait``, ``create_task_batch``). Beides landet als
Werkzeugaufruf im Gespraech.

Fuer die Persistenz wird der Werkzeug-Input auf 200 Zeichen gekuerzt. Bei
einem ``Bash``-Aufruf ist das richtig. Bei einem Subagenten faellt dabei genau
das weg, was ihn ausmacht: Beschreibung und Art. In den echten Daten auf der
Anlage steht der Auftragstext an erster Stelle im JSON —

    {"description": "Review flaky PR #324", "prompt": "Repo: ... (sehr lang)"}

— je nach Reihenfolge ist die Beschreibung nach 200 Zeichen also schon weg
oder das JSON gar nicht mehr lesbar. Nach dem Neuladen stand dann ein
namenloser Helfer in der Uebersicht.

Deshalb tragen Subagenten ihre Kernfelder gesondert und ungekuerzt.
"""

import json
import re
import unittest
from pathlib import Path

_HANDLER = Path(__file__).resolve().parents[1] / "app" / "chat_handler.py"
_CHAT = (Path(__file__).resolve().parents[2] / "frontend" / "src"
         / "components" / "agents" / "chat.tsx")


class PersistenzTest(unittest.TestCase):
    def setUp(self):
        self.quelle = _HANDLER.read_text(encoding="utf-8")

    def test_subagenten_tragen_ihre_felder_gesondert(self):
        self.assertIn('eintrag["subagent"]', self.quelle,
                      "Ohne eigenes Feld ueberlebt die Beschreibung die Kuerzung nicht.")
        for feld in ("description", "subagent_type", "run_in_background"):
            with self.subTest(feld=feld):
                self.assertIn(feld, self.quelle)

    def test_beide_werkzeugnamen_werden_erkannt(self):
        """``Task`` ist die aeltere Bezeichnung und steht in alten Verlaeufen."""
        treffer = re.search(r'if tool_name in \(([^)]*)\)', self.quelle)
        self.assertIsNotNone(treffer, "Erkennung des Subagenten-Werkzeugs nicht gefunden")
        self.assertIn('"Agent"', treffer.group(1))
        self.assertIn('"Task"', treffer.group(1))

    def test_die_kuerzung_bleibt_fuer_gewoehnliche_werkzeuge(self):
        """Ein Bash-Aufruf soll den Verlauf nicht aufblaehen."""
        self.assertIn('json.dumps(tool_input)[:200]', self.quelle)


@unittest.skipUnless(_CHAT.exists(), "Frontend nicht vorhanden")
class OberflaecheTest(unittest.TestCase):
    def setUp(self):
        self.quelle = _CHAT.read_text(encoding="utf-8")

    def test_alle_drei_laufzeiten_sind_angebunden(self):
        """Sonst sieht ein Codex-Agent so aus, als koenne er keine Helfer."""
        block = re.search(r"const SUBAGENT_WERKZEUGE[^=]*= \{(.*?)\};", self.quelle, re.S)
        self.assertIsNotNone(block, "Werkzeugliste nicht gefunden")
        for werkzeug in ("Agent", "create_task", "create_task_batch", "delegate_and_wait"):
            with self.subTest(werkzeug=werkzeug):
                self.assertIn(werkzeug, block.group(1))

    def test_eigener_subagent_und_delegation_sind_unterscheidbar(self):
        """Verschiedene Mechanik — das soll in der Zeile stehen."""
        self.assertIn('"eigen"', self.quelle)
        self.assertIn('"delegiert"', self.quelle)
        self.assertIn("herkunft", self.quelle)

    def test_es_gibt_eine_eigene_kachel(self):
        self.assertIn("function SubagentCluster", self.quelle)

    def test_subagenten_sind_anklickbar(self):
        """Der Kern des Wunsches: draufklicken und alle Helfer sehen.

        Geprueft wird die REIHENFOLGE im Quelltext, nicht ein Zeichenabstand
        (#726): Aufklappen und Detailoeffnen muessen INNERHALB der Kachel
        stehen, also nach ihrem Beginn und vor der naechsten Funktion.
        """
        beginn = self.quelle.index("function SubagentCluster")
        ende = self.quelle.index("function ", beginn + 20)
        for was, warum in (
            ("setOffen", "Die Kachel laesst sich nicht aufklappen."),
            ("setDetail", "Einzelne Helfer lassen sich nicht oeffnen."),
            ("Auftrag", "Der Auftrag wird nicht gezeigt."),
            ("Ergebnis", "Das Ergebnis wird nicht gezeigt."),
        ):
            with self.subTest(was=was):
                stelle = self.quelle.find(was, beginn)
                self.assertNotEqual(stelle, -1, warum)
                self.assertLess(stelle, ende, warum)

    def test_sie_verschwinden_nicht_im_einfachen_modus(self):
        """Dass Helfer laufen, ist keine technische Einzelheit."""
        treffer = re.search(r"const visibleSteps = simpleMode\s*\?(.*?);", self.quelle, re.S)
        self.assertIsNotNone(treffer)
        self.assertIn('"subagent"', treffer.group(1))

    def test_es_gibt_eine_dauerhafte_anzeige_unten(self):
        """Die Kachel in der Blase reicht nicht.

        Sobald man weiterschreibt, ist sie nach oben gescrollt — und damit auch
        die Antwort auf "laeuft da noch was?". Deshalb zusaetzlich eine
        sitzungsweite Anzeige in der Eingabeleiste, wie das Modell-Abzeichen.
        """
        self.assertIn("function SubagentLeiste", self.quelle)
        # Sie muss WIRKLICH in der Eingabeleiste stehen, nicht irgendwo.
        # Geprueft ueber die Reihenfolge: Der Aufruf steht nach dem Beginn der
        # Leiste und vor der Bueroklammer, die dort als erstes Bedienelement
        # sitzt.
        leiste = self.quelle.index("border-t border-border/60 px-2 py-1.5")
        aufruf = self.quelle.find("<SubagentLeiste", leiste)
        klammer = self.quelle.find("fileInputRef.current?.click()", leiste)
        self.assertNotEqual(aufruf, -1, "Die Anzeige haengt nicht an der Eingabeleiste.")
        self.assertLess(aufruf, klammer,
                        "Sie steht nicht innerhalb der Eingabeleiste.")

    def test_sie_sammelt_ueber_die_ganze_sitzung(self):
        """Nicht nur die Helfer einer Blase — sonst waere sie so blind wie
        die Kachel."""
        stelle = self.quelle.index("const alleSubagenten")
        flat = self.quelle.find("messages.flatMap", stelle)
        naechste = self.quelle.find("\n  const ", stelle + 10)
        self.assertNotEqual(flat, -1, "Sie schaut nicht ueber alle Nachrichten.")
        self.assertLess(flat, naechste,
                        "messages.flatMap gehoert nicht zu dieser Berechnung.")

    def test_ohne_helfer_bleibt_die_leiste_leer(self):
        """Kein Platzhalter, der dauerhaft Raum kostet."""
        beginn = self.quelle.index("function SubagentLeiste")
        ende = self.quelle.index("function ", beginn + 20)
        stelle = self.quelle.find("subagenten.length === 0) return null", beginn)
        self.assertNotEqual(stelle, -1, "Die leere Leiste kostet dauerhaft Platz.")
        self.assertLess(stelle, ende)

    def test_der_verlauf_stellt_sie_wieder_her(self):
        self.assertIn("tc as { subagent?", self.quelle,
                      "Nach dem Neuladen waeren es wieder namenlose Werkzeugaufrufe.")


class EchteDatenTest(unittest.TestCase):
    """Gegen eine echte Nutzlast von der Anlage — nicht gegen eine erfundene."""

    #: Gekuerzt wie in der Datenbank beobachtet (22.09.2026).
    ECHT = {
        "description": "Review lotto-berlin light-mode and icons diff",
        "subagent_type": "mindCoder:code-reviewer",
        "run_in_background": False,
        "prompt": "Review an uncommitted diff in the git repo at /workspace/..." + "x" * 400,
    }

    def test_die_beschreibung_ueberlebt_die_kuerzung_nicht(self):
        """Begruendet, warum das eigene Feld noetig ist."""
        gekuerzt = json.dumps(self.ECHT)[:200]
        with self.assertRaises(json.JSONDecodeError):
            json.loads(gekuerzt)

    def test_mit_eigenem_feld_bleibt_alles_lesbar(self):
        eintrag = {
            "tool": "Agent",
            "input": json.dumps(self.ECHT)[:200],
            "subagent": {
                "description": self.ECHT["description"][:200],
                "subagent_type": self.ECHT["subagent_type"],
                "run_in_background": self.ECHT["run_in_background"],
            },
        }
        self.assertEqual(eintrag["subagent"]["description"],
                         "Review lotto-berlin light-mode and icons diff")
        self.assertEqual(eintrag["subagent"]["subagent_type"], "mindCoder:code-reviewer")


if __name__ == "__main__":
    unittest.main()
