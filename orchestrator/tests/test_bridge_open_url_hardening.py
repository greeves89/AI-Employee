"""`open_url` in der Bridge darf niemals eine Kommandozeile bauen.

Regressionstest zum Injection-Fund vom 2026-08-04: Die erste Fassung oeffnete
Adressen auf Windows ueber `cmd /c start "" <url>`. cmd.exe parst seine Argumente
ERNEUT — ein `&` in der URL wird damit zum Befehlstrenner, und `&` steht in jeder
zweiten Query. `https://x/?a=1&calc.exe` haette calc.exe gestartet. Die URL stammt
vom Sprachmodell, das wiederum von Inhalten beeinflussbar ist, die es liest.

Geprueft wird die QUELLE, weil der Windows-Zweig auf macOS/Linux nicht ausfuehrbar ist.
"""

import re
import unittest
from pathlib import Path

_SRC = (Path(__file__).resolve().parents[2] / "computer-use-bridge" / "bridge.py").read_text()


def _open_url_code() -> str:
    """Der open_url-Zweig, OHNE Kommentare — sonst schlagen Erwaehnungen im
    Fliesstext an, die genau erklaeren, was hier verboten ist."""
    block = _SRC[_SRC.index('elif action == "open_url"'):]
    block = block[:block.index('elif action == "close_app"')]
    return "\n".join(
        line for line in block.splitlines() if not line.lstrip().startswith("#")
    )


class OpenUrlHardeningTests(unittest.TestCase):
    def setUp(self):
        self.code = _open_url_code()

    def test_no_cmd_exe(self):
        for needle in ('"cmd"', "'cmd'", "/c"):
            self.assertNotIn(needle, self.code, f"{needle} ist wieder im Code")

    def test_windows_uses_startfile(self):
        """os.startfile geht direkt an die Shell-API — keine Kommandozeile dazwischen."""
        self.assertIn("os.startfile(url)", self.code)

    def test_no_shell_true(self):
        self.assertNotIn("shell=True", self.code)

    def test_only_http_schemes(self):
        self.assertIn('("http://", "https://")', self.code)

    def test_whitespace_and_control_chars_rejected(self):
        self.assertIn("isspace()", self.code)
        self.assertIn("ord(ch) < 0x20", self.code)

    def test_every_subprocess_call_is_list_form(self):
        """Ein String-Kommando wuerde die Shell zurueckholen."""
        calls = re.findall(r"subprocess\.run\(\s*([A-Za-z_\[])", self.code)
        self.assertTrue(calls, "kein subprocess-Aufruf gefunden — Test veraltet?")
        for first in calls:
            self.assertIn(first, "c[", f"subprocess.run beginnt mit '{first}' statt einer Liste")


if __name__ == "__main__":
    unittest.main()
