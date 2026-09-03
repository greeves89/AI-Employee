"""Die eingebauten MCP-Server laufen in EINEM Prozess statt in elf (#638).

Gemessen im laufenden Container waehrend eines einzigen Laufs:

| | Prozesse | Threads | Speicher |
|---|---|---|---|
| vorher | 10 (11 mit Microsoft) | 81 | ~691 MB |
| nachher | 1 | **7** | **82 MB** |

Rund 609 MB je Lauf. Das war der Grund fuer die Deckelung auf vier gleichzeitige
Laeufe (#628) und auf einem Host mit knappem Speicher der Grund fuer
abbrechende Laeufe (#653): die Werkzeugserver kosteten das 2,4-fache des
eigentlichen Modell-Prozesses.

Zwei Dinge, die der Umbau beachten musste:

* Ein `Server` laesst sich nur an EINEN Transport binden (SDK
  `shared/protocol.js`: „Already connected to a transport"). Ein gemeinsamer
  Prozess bedient aber mehrere gleichzeitige Laeufe — deshalb liefert jede
  Datei jetzt eine FABRIK statt einer fertigen Instanz.
* Der Sammelstart importiert die Serverdateien, um an ihre Fabriken zu kommen.
  Dabei laeuft deren letzte Zeile mit; ohne Sperre wollte jede ihren eigenen
  Dienst auf demselben Port oeffnen.
"""

import os
import re
import unittest
from pathlib import Path

from app.pids_budget import DEFAULT_COST_PER_RUN, DEFAULT_RESERVE, max_concurrent_runs

_MCP = Path(__file__).resolve().parents[1] / "mcp"
_MAIN = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text()
_TRANSPORT = (_MCP / "_transport.mjs").read_text()
_ALL = (_MCP / "_all.mjs").read_text()

#: Die eingebauten Server. `hyperframes-server-http` ist die alte HTTP-Referenz
#: und keine eigene Serverdatei im Sinne dieser Umstellung.
def _serverdateien():
    return [p for p in sorted(_MCP.glob("*-server.mjs"))
            if p.name != "hyperframes-server-http.mjs"]


class JedeDateiLiefertEineFabrikTests(unittest.TestCase):
    def test_keine_modul_instanz_mehr(self):
        """`const server = new Server(` auf Modulebene ist genau das, was sich
        nicht zweimal verbinden laesst."""
        funde = [p.name for p in _serverdateien()
                 if re.search(r"^const server = new Server\(", p.read_text(), re.M)]
        self.assertEqual(funde, [], f"Noch Modul-Instanzen: {funde}")

    def test_jede_datei_hat_eine_fabrik(self):
        fehlend = [p.name for p in _serverdateien()
                   if "export function buildServer" not in p.read_text()]
        self.assertEqual(fehlend, [], f"Ohne Fabrik: {fehlend}")

    def test_jede_datei_startet_ueber_den_gemeinsamen_bootstrap(self):
        fehlend = [p.name for p in _serverdateien()
                   if "startServer(" not in p.read_text()]
        self.assertEqual(fehlend, [], f"Ohne startServer: {fehlend}")

    def test_niemand_baut_seinen_stdio_transport_mehr_selbst(self):
        """Sonst haengt die Datei an stdio fest und kann nicht mitgeteilt werden."""
        funde = [p.name for p in _serverdateien()
                 if "StdioServerTransport" in p.read_text()]
        self.assertEqual(funde, [], f"Eigener stdio-Transport: {funde}")

    def test_es_sind_alle_erwarteten_server(self):
        self.assertGreaterEqual(len(_serverdateien()), 11)


class DerSammelstartTests(unittest.TestCase):
    def test_er_sperrt_die_einzelstarts(self):
        """Ohne die Sperre oeffnet jede importierte Datei ihren eigenen Dienst
        auf demselben Port — alle bis auf die erste scheitern mit EADDRINUSE."""
        self.assertIn('process.env.MCP_COMBINED = "1"', _ALL)
        self.assertIn('if (process.env.MCP_COMBINED === "1") return;', _TRANSPORT)

    def test_die_sperre_steht_vor_den_importen(self):
        """Statische Importe werden vorgezogen — deshalb sind die Server-Importe
        dynamisch und die Zuweisung steht davor."""
        sperre = _ALL.index('process.env.MCP_COMBINED = "1"')
        laden = _ALL.index("await import(pfad)")
        self.assertLess(sperre, laden)
        self.assertIn("await import(pfad)", _ALL)

    def test_ein_kaputter_server_reisst_die_uebrigen_nicht_mit(self):
        """Sonst kostet ein einzelner Fehler die ganze Ausstattung."""
        block = _ALL.split("for (const [name, pfad] of kandidaten)", 1)[1][:700]
        self.assertIn("try {", block)
        self.assertIn("catch (e)", block)
        self.assertIn("fehlend.push", block)

    def test_ohne_einen_einzigen_server_bricht_er_ab(self):
        self.assertIn("kein einziger Server geladen", _ALL)

    def test_microsoft_nur_wenn_eingerichtet(self):
        self.assertIn('MSGRAPH_ENABLED || ""', _ALL)


class DieUmstellungIstAbschaltbarTests(unittest.TestCase):
    """Der Umbau muss jederzeit zurueckdrehbar sein — auf einer Kundenanlage
    darf eine Speicheroptimierung nicht die Werkzeuge kosten."""

    def test_ohne_port_bleibt_alles_wie_bisher(self):
        self.assertIn('_http_port = int(os.environ.get("MCP_HTTP_PORT") or 0)', _MAIN)
        self.assertIn("if _http_port and _start_combined_mcp(_http_port):", _MAIN)

    def test_ein_fehlschlag_faellt_auf_einzelprozesse_zurueck(self):
        """Ein Agent ohne Werkzeuge waere schlimmer als einer, der mehr
        Speicher braucht."""
        block = _MAIN.split("def _start_combined_mcp", 1)[1][:2200]
        self.assertIn("return False", block)
        self.assertIn("_einzeln = True", _MAIN)

    def test_im_gemeinsamen_modus_keine_doppelregistrierung(self):
        """Dieselben Server zweimal anzumelden — einmal als Adresse, einmal als
        eigener Prozess — brachte den Gewinn wieder zunichte."""
        self.assertIn("builtin_servers.items() if _einzeln else []", _MAIN)
        self.assertIn('if _einzeln and os.environ.get("MSGRAPH_ENABLED", "").lower()', _MAIN)

    def test_er_wartet_bis_der_dienst_antwortet(self):
        """Sonst meldet Claude Code Adressen an, die es noch nicht gibt, und der
        erste Werkzeugaufruf scheitert."""
        block = _MAIN.split("def _start_combined_mcp", 1)[1][:2200]
        self.assertIn("connect_ex", block)

    def test_er_merkt_wenn_der_prozess_sofort_stirbt(self):
        block = _MAIN.split("def _start_combined_mcp", 1)[1][:2200]
        self.assertIn("proc.poll() is not None", block)


if __name__ == "__main__":
    unittest.main()


class DieProzessgrenzeSteigtMitTests(unittest.TestCase):
    """Der Punkt, an dem der Umbau ueberhaupt erst wirkt.

    Die Nebenlaeufigkeit war auf vier Laeufe gedeckelt, weil jeder Lauf elf
    Serverprozesse mitbrachte (#628). Laufen sie gemeinsam, gehoeren sie zur
    Grundlast des Containers statt zu jedem Lauf — aus 88 Plaetzen je Lauf
    werden 8.
    """

    def setUp(self):
        self._alt = os.environ.get("MCP_HTTP_PORT")

    def tearDown(self):
        if self._alt is None:
            os.environ.pop("MCP_HTTP_PORT", None)
        else:
            os.environ["MCP_HTTP_PORT"] = self._alt

    def test_einzeln_bleibt_es_bei_der_alten_rechnung(self):
        os.environ.pop("MCP_HTTP_PORT", None)
        self.assertEqual(max_concurrent_runs(pids_max=512), 4)

    def test_gemeinsam_steigt_sie_deutlich(self):
        os.environ["MCP_HTTP_PORT"] = "8899"
        self.assertGreaterEqual(max_concurrent_runs(pids_max=512), 40)

    def test_die_teure_annahme_ist_die_vorgabe(self):
        """Zu billig gerechnet, waehrend die Server doch einzeln laufen, erstickt
        der Container am pids-Limit. Fehlt die Variable, gilt der alte Wert."""
        os.environ.pop("MCP_HTTP_PORT", None)
        self.assertEqual(max_concurrent_runs(pids_max=512),
                         max_concurrent_runs(pids_max=512, cost_per_run=DEFAULT_COST_PER_RUN,
                                             reserve=DEFAULT_RESERVE))

    def test_ein_leerer_port_zaehlt_nicht_als_eingeschaltet(self):
        os.environ["MCP_HTTP_PORT"] = "0"
        self.assertEqual(max_concurrent_runs(pids_max=512), 4)

    def test_ausdrueckliche_werte_gewinnen_weiterhin(self):
        """Die Aufrufer, die eigene Zahlen uebergeben, duerfen sich nicht
        ploetzlich anders verhalten."""
        os.environ["MCP_HTTP_PORT"] = "8899"
        self.assertEqual(max_concurrent_runs(pids_max=512, reserve=120, cost_per_run=88), 4)

    def test_mindestens_ein_lauf_bleibt_immer(self):
        os.environ["MCP_HTTP_PORT"] = "8899"
        self.assertGreaterEqual(max_concurrent_runs(pids_max=100), 1)


class DerSchalterKommtVomOrchestratorTests(unittest.TestCase):
    """Der Agent kann sich nicht selbst umstellen — die Umgebung setzt der
    Orchestrator beim Erstellen des Containers. Ohne diesen Weg bliebe der
    gemeinsame Modus totes Werkzeug."""

    MGR = (Path(__file__).resolve().parents[2] / "orchestrator" / "app" / "core"
           / "agent_manager.py").read_text()
    CFG = (Path(__file__).resolve().parents[2] / "orchestrator" / "app"
           / "config.py").read_text()

    def test_es_gibt_eine_einstellung(self):
        self.assertIn("mcp_http_port: int = 0", self.CFG)

    def test_die_vorgabe_ist_aus(self):
        """Eine Speicheroptimierung darf sich nicht von selbst einschalten."""
        self.assertIn("mcp_http_port: int = 0", self.CFG)

    def test_sie_erreicht_beide_erstellungswege(self):
        """Agenten werden an zwei Stellen gebaut (Neuanlage und Aktualisierung).
        Nur eine zu treffen hiesse: der Modus haengt davon ab, wie der Agent
        zuletzt entstanden ist."""
        self.assertEqual(self.MGR.count('"MCP_HTTP_PORT": str(settings.mcp_http_port)'), 2)

    def test_bei_null_wird_die_variable_gar_nicht_gesetzt(self):
        """Eine gesetzte 0 waere zweideutig — der Agent prueft auf Vorhandensein
        und Wert. Sauberer ist, sie wegzulassen."""
        self.assertIn("if settings.mcp_http_port else {}", self.MGR)
