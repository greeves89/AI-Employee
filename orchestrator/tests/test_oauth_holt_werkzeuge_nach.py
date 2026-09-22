"""Nach dem Verbinden eines OAuth-Servers muss die Werkzeugliste da sein.

Am 22.09.2026 meldete ein Nutzer, ein frisch verbundener Konnektor finde
"0 Tools". Nachgemessen: Der Server war einwandfrei verbunden -- dynamische
Client-Registrierung, Access- und Refresh-Token gespeichert, ``last_status``
auf ``ok`` -- und lieferte auf direkte Nachfrage sofort 98 Werkzeuge.

Die Ursache lag im Ablauf, nicht im Server:

1. Beim ANLEGEN gibt es noch kein Token. Der Versuch, Werkzeuge zu lesen,
   ergibt zwangslaeufig eine leere Liste.
2. Der OAuth-Rueckweg speicherte danach das Token, setzte den Status auf "ok"
   -- und fragte die Werkzeuge NIE erneut ab.

Ergebnis: "verbunden, 0 Werkzeuge". Der Knopf "Aktualisieren" behebt es, aber
niemand kann wissen, dass man ihn genau dann druecken muss.

Wichtig ist auch der umgekehrte Fall: Scheitert das Abrufen, darf das
Verbinden trotzdem nicht zurueckgenommen werden -- das Token ist gueltig und
gespeichert, nur die Liste fehlt.
"""

import ast
import unittest
from pathlib import Path

_QUELLE = Path(__file__).resolve().parents[1] / "app" / "api" / "mcp_servers.py"


def _funktion(name: str) -> str:
    text = _QUELLE.read_text(encoding="utf-8")
    baum = ast.parse(text)
    for k in ast.walk(baum):
        if isinstance(k, (ast.AsyncFunctionDef, ast.FunctionDef)) and k.name == name:
            return ast.get_source_segment(text, k) or ""
    raise AssertionError(f"{name} nicht gefunden")


class OAuthRueckwegTest(unittest.TestCase):

    def setUp(self):
        self.quelle = _funktion("oauth_callback")

    def test_die_werkzeuge_werden_nach_dem_verbinden_geholt(self):
        self.assertIn(
            "_discover_tools", self.quelle,
            "Der OAuth-Rueckweg holt die Werkzeugliste nicht — der Server steht "
            "danach auf 'verbunden, 0 Werkzeuge' und sieht kaputt aus.",
        )

    def test_die_liste_wird_auch_gespeichert(self):
        """Eine geaenderte JSON-Spalte erkennt SQLAlchemy nur mit flag_modified."""
        stelle = self.quelle.index("_discover_tools")
        rest = self.quelle[stelle:]
        self.assertIn('flag_modified(server, "tools")', rest)
        self.assertIn("await db.commit()", rest)

    def test_ein_fehlschlag_nimmt_das_verbinden_nicht_zurueck(self):
        """Das Token ist gueltig und gespeichert — nur die Liste fehlt dann."""
        stelle = self.quelle.index("_discover_tools")
        rest = self.quelle[stelle:]
        self.assertIn("except Exception", rest,
                      "Ohne Auffangen wuerde ein Abrufproblem das ganze "
                      "Verbinden scheitern lassen.")
        self.assertIn('_integrations_redirect("connected"', rest,
                      "Der Nutzer muss trotzdem als verbunden zurueckkommen.")

    def test_das_token_steht_vor_dem_abruf(self):
        """Sonst liefe der Abruf wieder ohne Zugangsdaten — der alte Fehler."""
        self.assertLess(
            self.quelle.index("apply_token_to_server"),
            self.quelle.index("_discover_tools"),
            "Die Werkzeuge duerfen erst NACH dem Speichern des Tokens geholt "
            "werden, sonst ist die Liste erneut leer.",
        )


if __name__ == "__main__":
    unittest.main()
