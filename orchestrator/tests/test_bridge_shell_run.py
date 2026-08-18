"""shell_run: gesperrt ohne Ordner-Freigabe, eingezaeunt mit.

Die Aktion stand seit jeher in der Server-Gruppenliste (`shell`) und der
Berechtigungs-Dialog versprach "Shell-Befehle sind auf diese Ordner
beschraenkt" — implementiert war in der Bridge NICHTS: weder die Aktion noch
irgendeine Wirkung der Ordnerliste. Eine Oberflaeche, die Schutz zusagt und
nichts durchsetzt, ist schlechter als gar keine.

Jetzt gilt, fail-closed: kein freigegebener Ordner → Aktion gesperrt, auch bei
serverseitig eingeschalteter Faehigkeit. Das Arbeitsverzeichnis muss in einem
freigegebenen Ordner liegen; ``realpath`` verhindert den Ausbruch per ``..``
und per Symlink.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "computer-use-bridge"))
import bridge  # noqa: E402


class ShellRunTests(unittest.TestCase):
    def setUp(self):
        self._orig_config = bridge.BRIDGE_CONFIG_PATH
        self._tmp = tempfile.TemporaryDirectory()
        bridge.BRIDGE_CONFIG_PATH = os.path.join(self._tmp.name, "bridge.json")
        self.allowed = os.path.join(self._tmp.name, "erlaubt")
        self.forbidden = os.path.join(self._tmp.name, "fremd")
        os.makedirs(self.allowed)
        os.makedirs(self.forbidden)

    def tearDown(self):
        bridge.BRIDGE_CONFIG_PATH = self._orig_config
        self._tmp.cleanup()

    def _write_paths(self, paths):
        with open(bridge.BRIDGE_CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump({"allowed_paths": paths}, fh)

    def test_locked_without_any_allowed_folder(self):
        """Der Kern: keine Ordner-Freigabe → shell_run tut NICHTS."""
        result = bridge.shell_run("echo pwned")
        self.assertFalse(result["ok"])
        self.assertIn("gesperrt", result["error"])

    def test_locked_when_config_lists_only_missing_folders(self):
        self._write_paths([os.path.join(self._tmp.name, "gibt-es-nicht")])
        result = bridge.shell_run("echo pwned")
        self.assertFalse(result["ok"])
        self.assertIn("gesperrt", result["error"])

    def test_runs_in_first_allowed_folder_by_default(self):
        self._write_paths([self.allowed])
        result = bridge.shell_run("echo hallo")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["cwd"], os.path.realpath(self.allowed))
        self.assertIn("hallo", result["stdout"])

    def test_cwd_outside_allowed_folders_is_refused(self):
        self._write_paths([self.allowed])
        result = bridge.shell_run("echo x", cwd=self.forbidden)
        self.assertFalse(result["ok"])
        self.assertIn("nicht freigegeben", result["error"])

    def test_dotdot_escape_is_refused(self):
        """`erlaubt/../fremd` liegt NICHT im freigegebenen Ordner."""
        self._write_paths([self.allowed])
        sneaky = os.path.join(self.allowed, "..", "fremd")
        result = bridge.shell_run("echo x", cwd=sneaky)
        self.assertFalse(result["ok"])
        self.assertIn("nicht freigegeben", result["error"])

    def test_prefix_lookalike_folder_is_refused(self):
        """`/erlaubt-evil` darf nicht durch einen naiven startswith-Vergleich
        mit `/erlaubt` rutschen."""
        lookalike = self.allowed + "-evil"
        os.makedirs(lookalike)
        self._write_paths([self.allowed])
        result = bridge.shell_run("echo x", cwd=lookalike)
        self.assertFalse(result["ok"])

    def test_subfolder_of_allowed_is_fine(self):
        sub = os.path.join(self.allowed, "unter")
        os.makedirs(sub)
        self._write_paths([self.allowed])
        result = bridge.shell_run("echo x", cwd=sub)
        self.assertTrue(result["ok"], result)

    def test_failing_command_reports_honestly(self):
        self._write_paths([self.allowed])
        result = bridge.shell_run("exit 3" if os.name != "nt" else "cmd /c exit 3")
        self.assertFalse(result["ok"])
        self.assertEqual(result["returncode"], 3)

    def test_action_is_announced_and_dispatched(self):
        """shell_run muss in BASE_ACTIONS stehen UND der Dispatcher muss es
        kennen — sonst waere es wieder eine Zusage ohne Funktion (oder eine
        Funktion, die niemand findet)."""
        self.assertIn("shell_run", bridge.BASE_ACTIONS)
        src = (Path(bridge.__file__)).read_text(encoding="utf-8")
        self.assertIn('action == "shell_run"', src)


if __name__ == "__main__":
    unittest.main()
