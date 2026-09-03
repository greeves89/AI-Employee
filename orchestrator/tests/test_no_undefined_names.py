"""Kein undefinierter Name im Quelltext — das Gatter gegen eine ganze Fehlerklasse.

Der Anlass ist ein Ausfall beim Kunden: das Anlegen eines Agenten scheiterte mit

    500 — cannot access local variable 'config' where it is not associated with a value

In ``create_agent`` stand ``agent_timezone(config)``, aber ``config`` wird in dieser
Funktion erst viel weiter unten gesetzt. Python merkt das nicht beim Import, sondern
erst, wenn die Zeile läuft — und sie lief bei **jedem** Anlegen. Fünf Tage lang,
seit dem Zeitzonen-Commit vom 7. August, war das Anlegen eines Agenten kaputt.

Kein Test hat es gefunden, weil kein Test einen Agenten anlegt (dafür bräuchte es
Docker). Ein Linter hätte es in Sekunden gefunden: ``F821 Undefined name 'config'``.

Beim ersten Lauf über das ganze Projekt kamen **zwölf** Treffer heraus — zehn echte
Zeitbomben derselben Art:

* ``_TELEGRAM_MAX_FILE_BYTES`` war an drei Stellen benutzt und nirgends definiert:
  jeder Bild-, Video- oder Animationsversand wäre in einen NameError gelaufen,
  statt in die 413-Meldung, die direkt daneben steht.
* ``logger`` fehlte in ``webhooks.py`` (vier Stellen) und ``mcp_agent.py`` — eine
  abgelehnte WhatsApp-Verifizierung hätte 500 statt 403 ergeben.
* ``sa_text`` war in einer Funktion nicht importiert, in den Nachbarfunktionen schon.

Deshalb prüft dieser Test nicht die eine Zeile, sondern die Regel.
"""

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGETS = ["orchestrator/app", "agent/app"]


def _ruff() -> str | None:
    """Pfad zu ruff — als Modul des laufenden Interpreters oder im PATH."""
    import sys

    probe = subprocess.run([sys.executable, "-m", "ruff", "--version"],
                           capture_output=True, cwd=ROOT)
    if probe.returncode == 0:
        return f"{sys.executable} -m ruff"
    return shutil.which("ruff")


class NoUndefinedNamesTests(unittest.TestCase):
    def test_no_undefined_names_anywhere(self):
        ruff = _ruff()
        if not ruff:
            self.skipTest("ruff nicht installiert (pip install ruff)")

        proc = subprocess.run(
            f"{ruff} check --select F821 --output-format concise " + " ".join(TARGETS),
            shell=True, capture_output=True, text=True, cwd=ROOT,
        )
        hits = [
            line for line in proc.stdout.splitlines()
            if ": F821" in line
        ]
        self.assertEqual(
            hits, [],
            "Undefinierte Namen — jede dieser Zeilen wirft zur LAUFZEIT einen "
            "NameError, sobald sie erreicht wird:\n  " + "\n  ".join(hits)
            + "\n\nSteht der Name nur in einer Typangabe, gehoert er unter "
              "``if TYPE_CHECKING:`` statt in einen Kommentar.",
        )


class TheOriginalDefectTests(unittest.TestCase):
    """Die konkrete Zeile, mit der es aufgefallen ist.

    Der Test oben faengt sie ohnehin — dieser hier haelt fest, WARUM ``None``
    dort richtig ist: beim Anlegen hat der Agent noch keine eigene Zeitzone.
    """

    def test_create_agent_uses_the_default_timezone(self):
        src = (ROOT / "orchestrator/app/core/agent_manager.py").read_text()
        block = src.split("async def create_agent")[1].split("\n    async def ")[0]
        self.assertIn('"TZ": agent_timezone(None)', block)

    def test_restart_and_update_use_the_agents_own_timezone(self):
        """Dort GIBT es den Agenten schon — und damit seine Zeitzone. Die Vorgabe
        waere hier falsch: ein Agent, der auf Berlin eingestellt ist, liefe nach
        einem Neustart plötzlich in UTC."""
        src = (ROOT / "orchestrator/app/core/agent_manager.py").read_text()
        for fn in ("async def restart_agent", "async def update_agent"):
            with self.subTest(fn):
                block = src.split(fn)[1].split("\n    async def ")[0]
                self.assertIn('"TZ": agent_timezone(config)', block)


if __name__ == "__main__":
    unittest.main()
