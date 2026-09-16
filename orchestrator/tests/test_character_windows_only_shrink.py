"""Zeichenfenster in Tests duerfen nur noch weniger werden (#726).

Ein Zeichenfenster ist ein Test, der den Quelltext hinter einem Stichwort auf N
Zeichen zuschneidet und darin nach einer Zeile sucht::

    block = QUELLE.split("if not await self._agent_exists(agent_id):")[1][:1400]
    self.assertIn("raise UnknownAgentError(", block)

Gemeint ist damit eine REIHENFOLGE ("das raise kommt vor dem db.add"), gemessen
wird ein ABSTAND in Zeichen. Beides faellt auseinander, sobald jemand einen
Kommentar einfuegt: die Hauptlinie wird rot, ohne dass sich am Verhalten etwas
geaendert hat. Der teurere Fehler ist der andere Weg — ein auskommentierter oder
nie erreichter Aufruf steht weiterhin im Fenster und besteht die Pruefung. Der
Test sichert dann nichts mehr zu und sagt es niemandem.

Die 174 Fundstellen aus 66 Dateien lassen sich nicht in einem Zug umbauen. Diese
Sperre haelt deshalb den Stand fest: bestehende Dateien duerfen schrumpfen, neue
Fenster kommen nicht mehr dazu. Jede Umstellung wird als kleinerer Zahlenwert im
Diff sichtbar.
"""

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SUITEN = ("orchestrator/tests", "agent/tests")
BASISLINIE = Path(__file__).with_name("zeichenfenster_basislinie.json")

# `.split(...)[1][:1400]`, `.find(...)`-Varianten und `quelle[i:i + 1000]`.
FENSTER = re.compile(
    r"\.(?:split|partition|rsplit)\([^\n]*\)[^\n]{0,40}\[:\s*\d{2,}\s*\]"
    r"|\.(?:find|index)\([^\n]*\)[^\n]{0,60}\[:\s*\d{2,}\s*\]"
    r"|\[\s*\w+\s*:\s*\w+\s*\+\s*\d{2,}\s*\]"
)


def zaehle(pfad: Path) -> int:
    return len(FENSTER.findall(pfad.read_text(encoding="utf-8")))


def bestand() -> dict[str, int]:
    gefunden = {}
    for suite in SUITEN:
        for pfad in sorted((ROOT / suite).rglob("test_*.py")):
            if pfad.resolve() == Path(__file__).resolve():
                continue  # die Beispiele im Kopf dieser Datei sind keine Fenster
            n = zaehle(pfad)
            if n:
                gefunden[str(pfad.relative_to(ROOT))] = n
    return gefunden


class CharacterWindowsOnlyShrinkTests(unittest.TestCase):
    def setUp(self):
        self.basis = json.loads(BASISLINIE.read_text(encoding="utf-8"))
        self.jetzt = bestand()

    def test_no_file_grows_a_new_window(self):
        gewachsen = {
            datei: (self.basis.get(datei, 0), n)
            for datei, n in self.jetzt.items()
            if n > self.basis.get(datei, 0)
        }
        self.assertEqual(
            gewachsen, {},
            "Neue Zeichenfenster (Datei: vorher -> jetzt). Ein Fenster prueft den "
            "ABSTAND zweier Zeilen, gemeint ist ihre REIHENFOLGE — und ein nie "
            "ausgefuehrter Aufruf besteht es klaglos. Stattdessen den echten "
            "Ablauf fahren und pruefen, was PASSIERT (Beispiele: "
            "test_delegation_to_unknown_agent.py, test_autonomy_sudo_coupling.py). "
            f"Gewachsen: {gewachsen}",
        )

    def test_the_baseline_does_not_carry_finished_files(self):
        """Wer eine Datei leergeraeumt hat, nimmt sie aus der Basislinie.

        Sonst waechst der erlaubte Vorrat still wieder an: eine Datei stuende mit
        4 in der Liste, haette 0, und koennte unbemerkt wieder auf 4 klettern.
        """
        erledigt = sorted(d for d in self.basis if d not in self.jetzt)
        self.assertEqual(
            erledigt, [],
            "Diese Dateien haben keine Zeichenfenster mehr — Eintrag aus "
            f"{BASISLINIE.name} entfernen: {erledigt}")

    def test_a_shrunk_file_is_written_down(self):
        geschrumpft = {
            datei: (self.basis[datei], self.jetzt[datei])
            for datei in self.basis
            if datei in self.jetzt and self.jetzt[datei] < self.basis[datei]
        }
        self.assertEqual(
            geschrumpft, {},
            "Fortschritt gehoert in die Basislinie, sonst faellt der naechste "
            f"Rueckschritt nicht auf. Neue Werte fuer {BASISLINIE.name}: "
            f"{geschrumpft}")


if __name__ == "__main__":
    print(json.dumps(bestand(), indent=2, ensure_ascii=False))
