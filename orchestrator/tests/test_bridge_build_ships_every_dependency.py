"""Der Bridge-Build muss ALLES ausliefern, was die Bridge braucht.

Befund 2026-08-18: Der Workflow installierte eine von Hand gepflegte
Paketliste, die von ``requirements.txt`` abgewichen war — in beide Richtungen.
Im ausgelieferten Programm fehlten dadurch:

* ``uiautomation`` (Windows) — die Bridge konnte dort KEINE Elemente finden,
  nur blind auf Koordinaten klicken. Ausgerechnet in der Spec stand das Modul
  bereits, installiert wurde es nie.
* ``pynput`` — Replay-Modus tot.
* ``sounddevice``/``numpy`` — Mikrofon tot.
* ``pyobjc-framework-Quartz`` (macOS) — Bildschirmfoto im eigenen Prozess tot,
  Rueckfall auf einen Fremdprozess, der bei JEDEM Foto neu nach Freigabe fragt.

Auffallen konnte das nicht: ``bridge.py`` faengt fehlende Importe ab und gibt
eine freundliche Meldung zurueck. Die Faehigkeit war nicht kaputt, sie war
still nicht da.

Deshalb prueft dieser Test das PRINZIP statt einer Liste: Der Build installiert
aus ``requirements.txt``. Dann kann die Drift gar nicht erst entstehen.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/build-bridge.yml"
REQUIREMENTS = ROOT / "computer-use-bridge/requirements.txt"
SPEC_MAC = ROOT / "computer-use-bridge/bridge_macos.spec"
SPEC_WIN = ROOT / "computer-use-bridge/bridge_windows.spec"


class BridgeBuildDependencyTests(unittest.TestCase):
    def test_both_builds_install_from_requirements(self):
        """Kein zweiter, handgepflegter Paketkatalog im Workflow."""
        wf = WORKFLOW.read_text(encoding="utf-8")
        installs = re.findall(r"pip install[^\n]*", wf)
        self.assertTrue(installs, "keine pip-install-Schritte gefunden")

        from_req = [i for i in installs if "-r requirements.txt" in i]
        self.assertGreaterEqual(
            len(from_req), 2,
            "macOS- und Windows-Build muessen beide aus requirements.txt "
            f"installieren. Gefunden: {installs}",
        )

        # Alles ausser pyinstaller/pip selbst gehoert in requirements.txt.
        for line in installs:
            if "-r requirements.txt" in line:
                continue
            rest = line.replace("pip install", "").strip()
            self.assertIn(
                rest, {"pyinstaller", "--upgrade pip", "-U pip"},
                f"Handgepflegte Paketliste im Workflow: '{line}'. Neue "
                "Abhaengigkeiten gehoeren in requirements.txt, sonst laufen "
                "beide Listen wieder auseinander.",
            )

    def test_specs_bundle_playwright_as_data_not_just_import(self):
        """Playwright bringt einen Node-Treiber als Daten mit. Ein blosser
        Eintrag in hiddenimports wuerde ihn nicht einpacken — die
        Browser-Steuerung waere im gebauten Programm tot."""
        for spec in (SPEC_MAC, SPEC_WIN):
            with self.subTest(spec=spec.name):
                src = spec.read_text(encoding="utf-8")
                self.assertIn("collect_all('playwright')", src)
                self.assertIn("pw_datas", src)
                self.assertIn("pw_binaries", src)

    def test_capability_modules_are_declared_in_both_specs(self):
        """Module, deren Import abgefangen wird, muessen ausdruecklich in die
        Spec — PyInstaller findet sie sonst nicht und der Ausfall ist still."""
        for spec in (SPEC_MAC, SPEC_WIN):
            with self.subTest(spec=spec.name):
                src = spec.read_text(encoding="utf-8")
                for module in ("pynput", "sounddevice", "numpy"):
                    self.assertIn(f"'{module}'", src,
                                  f"{module} fehlt in {spec.name}")

    def test_requirements_covers_what_the_specs_expect(self):
        """Was die Spec einpacken will, muss auch installiert werden."""
        req = REQUIREMENTS.read_text(encoding="utf-8").lower()
        for module, package in (
            ("uiautomation", "uiautomation"),
            ("customtkinter", "customtkinter"),
            ("pynput", "pynput"),
            ("sounddevice", "sounddevice"),
        ):
            with self.subTest(module=module):
                self.assertIn(package, req,
                              f"{module} wird gebuendelt, steht aber nicht in "
                              "requirements.txt — wird also nie installiert")


if __name__ == "__main__":
    unittest.main()
