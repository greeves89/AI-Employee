"""Die Codex-Konfiguration darf keinen Abschnitt zweimal enthalten.

Nutzerbericht vom 2026-08-16: ein Agent nahm per Sprache einen Auftrag an,
meldete dann „es gab ein technisches Problem mit der Konfiguration" und tat
nichts. Im Container stand:

    Error loading config.toml:
    /home/agent/.codex/config.toml:99:14: duplicate key
    99 | [mcp_servers.msgraph]

Codex bricht beim ERSTEN doppelten Schluessel ab und laedt dann **gar keine**
Konfiguration. Der Agent hatte danach kein einziges Werkzeug — er konnte nur
noch reden. Genau deshalb wirkte er wie ein Schwaetzer: er wollte arbeiten und
konnte nachweislich nicht. Ein Auftrag scheiterte woertlich mit „Task failed at
startup due to a configuration error (duplicate key in config.toml) and never
executed."

Ursache ist die Ueberschneidung zweier Listen: ``msgraph`` ist seit dem
2026-08-12 ein EINGEBAUTER Server (fuer die Paritaet mit Claude Code) und wird
zusaetzlich als HTTP-Server eingeschleust, sobald ein Agent die
Microsoft-Integration zugewiesen bekommt. Beide schreiben denselben Abschnitt.

Ein einzelner doppelter Name legt also einen ganzen Agenten lahm. Das darf nicht
davon abhaengen, dass jemand zwei Listen im Kopf gegeneinander prueft.
"""

import json
import os
import re
import tempfile
import unittest
from unittest.mock import patch

from app.codex_runner import _ensure_codex_mcp_config


def _config(custom: dict, auth: dict | None = None) -> str:
    """Die Konfiguration wirklich schreiben lassen und zurueckgeben."""
    env = {
        "AGENT_ID": "a1",
        "AGENT_TOKEN": "t",
        "AGENT_NAME": "Testi",
        "ORCHESTRATOR_URL": "http://orchestrator:8000",
        "CUSTOM_MCP_SERVERS": json.dumps(custom),
        "CUSTOM_MCP_AUTH": json.dumps(auth or {}),
    }
    heim = tempfile.mkdtemp(prefix="codex-home-")
    # Im Test gibt es die .mjs-Dateien nicht — ohne das hier waere die Schleife
    # ueber die eingebauten Server leer und der Zusammenstoss traete nie auf.
    with patch("os.path.exists", return_value=True):
        _ensure_codex_mcp_config(heim, env)
    with open(os.path.join(heim, "config.toml")) as f:
        return f.read()


def _abschnitte(text: str) -> list[str]:
    return re.findall(r"^\[mcp_servers\.([a-z_0-9]+)\]$", text, re.MULTILINE)


class NoSectionAppearsTwiceTests(unittest.TestCase):
    def test_the_reported_collision(self):
        """Genau der Fall aus dem Bericht: msgraph eingebaut UND eingeschleust."""
        text = _config(
            {"msgraph": "http://orchestrator:8000/api/v1/mcp/msgraph/a1"},
            {"msgraph": "tok"},
        )
        namen = _abschnitte(text)
        self.assertEqual(namen.count("msgraph"), 1, f"doppelt: {namen}")

    def test_no_name_at_all_appears_twice(self):
        text = _config({
            "msgraph": "http://orchestrator:8000/api/v1/mcp/msgraph/a1",
            "brain": "http://example.com/brain",
            "email": "http://example.com/email",
            "eigener": "http://example.com/x",
        })
        namen = _abschnitte(text)
        self.assertEqual(len(namen), len(set(namen)), f"doppelt: {namen}")

    def test_a_dash_in_the_name_collides_too(self):
        """Namen werden entschaerft (``-`` wird ``_``) — der Zusammenstoss
        entsteht also erst NACH der Umschrift und muss auch dort auffallen."""
        text = _config({"read-logs": "http://example.com/logs"})
        self.assertEqual(_abschnitte(text).count("read_logs"), 1)

    def test_a_genuinely_new_server_still_lands(self):
        """Die Sperre darf nicht alles Eingeschleuste verschlucken."""
        text = _config({"eigener": "http://example.com/x"})
        self.assertIn("eigener", _abschnitte(text))

    def test_the_builtin_version_is_the_one_that_survives(self):
        """Der eingebaute gewinnt: er ist in jedem Agenten vorhanden und in
        beiden Laufzeiten derselbe."""
        text = _config({"msgraph": "http://example.com/msgraph"})
        block = text.split("[mcp_servers.msgraph]", 1)[1].split("[mcp_servers.", 1)[0]
        self.assertIn("command =", block)
        self.assertNotIn("url =", block)


class TheWholeFileStaysLoadableTests(unittest.TestCase):
    """Ein einziger doppelter Schluessel macht die GANZE Datei unbrauchbar —
    nicht nur den betroffenen Server. Deshalb wird hier geparst, nicht gezaehlt.
    """

    def test_it_parses_as_toml(self):
        try:
            import tomllib
        except ModuleNotFoundError:  # pragma: no cover
            self.skipTest("tomllib fehlt")
        text = _config(
            {
                "msgraph": "http://example.com/msgraph",
                "brain": "http://example.com/brain",
                "eigener": "http://example.com/x",
            },
            {"msgraph": "tok"},
        )
        geladen = tomllib.loads(text)   # wirft bei doppeltem Schluessel
        self.assertIn("eigener", geladen["mcp_servers"])

    def test_the_agent_keeps_its_tools(self):
        """Die eigentliche Folge des Fehlers: kein Werkzeug mehr."""
        try:
            import tomllib
        except ModuleNotFoundError:  # pragma: no cover
            self.skipTest("tomllib fehlt")
        geladen = tomllib.loads(_config({"msgraph": "http://example.com/msgraph"}))
        self.assertGreater(len(geladen["mcp_servers"]), 5)


if __name__ == "__main__":
    unittest.main()
