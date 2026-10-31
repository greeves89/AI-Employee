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

import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from app import main
from app.pids_budget import DEFAULT_COST_PER_RUN, DEFAULT_RESERVE, max_concurrent_runs

_MCP = Path(__file__).resolve().parents[1] / "mcp"
_MAIN = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text()
_TRANSPORT = (_MCP / "_transport.mjs").read_text()
_ALL = (_MCP / "_all.mjs").read_text()


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


#: `main.subprocess` ist dasselbe Modulobjekt wie das hier importierte. Ein
#: `mock.patch.object(main.subprocess, "Popen", ...)` trifft deshalb auch den
#: Aufruf INNERHALB des Ersatzes — ohne diesen festgehaltenen Originalverweis
#: ruft der Ersatz sich selbst auf (RecursionError statt Testergebnis).
_ECHTES_POPEN = subprocess.Popen


def _fake_popen(script: str):
    """Ersetzt den echten `node _all.mjs`-Aufruf durch ein Python-Skript, das
    dasselbe Verhalten zeigt (Port oeffnen/sofort sterben) — ohne node oder das
    eigentliche MCP-Bundle zu brauchen."""
    return lambda *a, **kw: _ECHTES_POPEN(
        [sys.executable, "-c", script], stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE, text=True,
    )

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
        # 10: Microsoft 365 und Gmail laufen seit 1.408 am Server (/mcp/msgraph,
        # /mcp/email), die lokalen Dateien mit dem rohen Token gibt es nicht mehr.
        self.assertGreaterEqual(len(_serverdateien()), 10)


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

    def test_microsoft_und_gmail_nie_lokal(self):
        """Ein lokaler Server holte sich das rohe Token des Besitzers — mit Bash
        und curl sendete der Agent dann an jeder Regel vorbei."""
        for name in ("msgraph-server", "email-server", "MSGRAPH_ENABLED"):
            self.assertNotIn(name, _ALL)
            self.assertNotIn(name, _MAIN)


class DieUmstellungIstAbschaltbarTests(unittest.TestCase):
    """Der Umbau muss jederzeit zurueckdrehbar sein — auf einer Kundenanlage
    darf eine Speicheroptimierung nicht die Werkzeuge kosten."""

    def test_ohne_port_bleibt_alles_wie_bisher(self):
        self.assertIn('_http_port = int(os.environ.get("MCP_HTTP_PORT") or 0)', _MAIN)
        self.assertIn("if _http_port and _start_combined_mcp(_http_port):", _MAIN)

    def test_ein_fehlschlag_faellt_auf_einzelprozesse_zurueck(self):
        """Ein Agent ohne Werkzeuge waere schlimmer als einer, der mehr
        Speicher braucht — echter Fallback, nicht nur die Textstelle."""
        calls = []
        with tempfile.TemporaryDirectory() as workspace, \
                mock.patch.object(main.settings, "workspace_dir", workspace), \
                mock.patch.object(main.settings, "agent_token", "test-token"), \
                mock.patch.object(main, "_start_combined_mcp", return_value=False), \
                mock.patch.object(main, "_run_mcp_add", side_effect=lambda a: calls.append(a) or True), \
                mock.patch.dict(os.environ, {
                    "MCP_HTTP_PORT": str(_free_port()),
                    "MSGRAPH_ENABLED": "true", "COMPUTER_USE_BROWSER": "",
                    "COMPUTER_USE_BRIDGE_MCP_URL": "", "CUSTOM_MCP_SERVERS": "",
                }, clear=False):
            main.register_mcp_servers()
            config = json.loads((Path(workspace) / ".mcp.json").read_text())
            self.assertTrue(os.environ["MCP_HTTP_PORT"])
        # Nicht hart getippt: die Zahl ist die LAENGE der Serverliste. Eine
        # festgetippte Zahl faerbt sonst jeden neuen Server rot, ohne einen
        # Fehler zu zeigen — und ``test_eine_mcp_liste`` haelt beide Listen
        # ohnehin gleich. Microsoft kommt nie lokal dazu (laeuft am Server).
        self.assertEqual(len(calls), len(main._COMBINED_MCP_NAMES))
        for args in calls:
            self.assertIn("MCP_HTTP_PORT=", args)
        self.assertEqual(len(config["mcpServers"]), 6)
        for entry in config["mcpServers"].values():
            self.assertEqual(entry["env"]["MCP_HTTP_PORT"], "")
        # Trotz Port-Umgebungsvariable wurden die einzelnen stdio-Server
        # angemeldet — der gemeinsame Prozess ist NICHT gestartet.
        self.assertTrue(any("bash-approval-server.mjs" in " ".join(a) for a in calls))
        self.assertFalse(any("--transport" in a for a in calls))

    def test_im_gemeinsamen_modus_keine_doppelregistrierung(self):
        """Dieselben Server zweimal anzumelden — einmal als Adresse, einmal als
        eigener Prozess — brachte den Gewinn wieder zunichte."""
        self.assertIn("builtin_servers.items() if _einzeln else []", _MAIN)

    def test_er_wartet_bis_der_dienst_antwortet(self):
        """Sonst meldet Claude Code Adressen an, die es noch nicht gibt, und der
        erste Werkzeugaufruf scheitert. Der Fake-Prozess oeffnet den Port erst
        nach einer Verzoegerung — ein sofortiges `return True` waere zu frueh."""
        port = _free_port()
        script = (
            "import socket, time\n"
            "time.sleep(0.3)\n"
            "s = socket.socket(); s.bind(('127.0.0.1', %d)); s.listen(1)\n"
            "time.sleep(2)\n" % port
        )
        with mock.patch.object(main.subprocess, "Popen", _fake_popen(script)):
            begin = time.monotonic()
            ok = main._start_combined_mcp(port)
            dauer = time.monotonic() - begin
        self.assertTrue(ok)
        self.assertGreaterEqual(dauer, 0.25)

    def test_er_merkt_wenn_der_prozess_sofort_stirbt(self):
        """Stirbt der Prozess sofort, darf die volle 5-Sekunden-Wartezeit nicht
        ausgeschoepft werden — sonst haengt jeder Lauf ohne Grund."""
        port = _free_port()
        script = "import sys; sys.exit(1)"
        with mock.patch.object(main.subprocess, "Popen", _fake_popen(script)):
            begin = time.monotonic()
            ok = main._start_combined_mcp(port)
            dauer = time.monotonic() - begin
        self.assertFalse(ok)
        self.assertLess(dauer, 3.0)


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


class MicrosoftNurUeberDenServerTests(unittest.TestCase):
    """Microsoft 365 kommt ausschliesslich als Adresse des Orchestrators
    (CUSTOM_MCP_SERVERS → /mcp/msgraph). Ein lokaler msgraph-Server holte sich
    frueher das rohe Token und umging Nur-Lesen, Entwurf und Mail-Freigabe.

    Ruft ``register_mcp_servers()`` wirklich auf und liest aus den tatsaechlichen
    ``_run_mcp_add``-Aufrufen ab, was registriert wurde.
    """

    def _lauf(self, *, custom_mcp_servers: str):
        calls = []
        port = _free_port()
        with mock.patch.object(main, "_start_combined_mcp", return_value=True), \
                mock.patch.object(main, "_run_mcp_add", side_effect=lambda a: calls.append(a) or True), \
                mock.patch.object(main, "_write_mcp_json_fallback", lambda *_: None), \
                mock.patch.dict(os.environ, {
                    "MCP_HTTP_PORT": str(port),
                    "CUSTOM_MCP_SERVERS": custom_mcp_servers,
                    "COMPUTER_USE_BROWSER": "", "COMPUTER_USE_BRIDGE_MCP_URL": "",
                }, clear=False):
            main.register_mcp_servers()
        lokal = any(f"127.0.0.1:{port}/mcp/msgraph" in " ".join(a) for a in calls)
        return calls, lokal

    def test_msgraph_kommt_ueber_die_adresse_des_orchestrators(self):
        calls, lokal = self._lauf(
            custom_mcp_servers='{"msgraph": "http://orchestrator.example.invalid/api/v1/mcp/msgraph/a1"}',
        )
        self.assertFalse(lokal)
        self.assertTrue(any("orchestrator.example.invalid" in " ".join(a) for a in calls))

    def test_ohne_adresse_gibt_es_keinen_lokalen_msgraph(self):
        _, lokal = self._lauf(custom_mcp_servers="")
        self.assertFalse(lokal)

    def test_eine_kaputte_liste_legt_den_start_nicht_lahm(self):
        """CUSTOM_MCP_SERVERS kommt als Text aus der Umgebung — ein Tippfehler
        darf nicht den ganzen Agenten kosten."""
        try:
            _, lokal = self._lauf(custom_mcp_servers="{kaputt")
        except Exception as exc:  # pragma: no cover - Beweis, dass es NICHT passiert
            self.fail(f"register_mcp_servers ist an der kaputten Liste gescheitert: {exc}")
        self.assertFalse(lokal)


class McpJsonFallbackFolgtDemModus(unittest.TestCase):
    """`.mcp.json` im Workspace ist ein PROJEKT-Eintrag und schlaegt die
    Nutzer-Registrierung gleichen Namens. Traegt sie im gemeinsamen Modus noch
    die stdio-Kommandos, startet Claude Code je Server einen Einzelprozess, der
    sich an denselben Port haengen will (EADDRINUSE) und sofort stirbt —
    memory/orchestrator/notifications/skills melden dann "Connection closed",
    waehrend brain/read-logs (nur per CLI angemeldet) weiter funktionieren."""

    def _schreibe(self, tmp: str, port: int | None) -> dict:
        import json as _json
        with mock.patch.object(main.settings, "workspace_dir", tmp):
            main._write_mcp_json_fallback(port)
        with open(os.path.join(tmp, ".mcp.json")) as f:
            return _json.load(f)["mcpServers"]

    def test_gemeinsamer_modus_schreibt_adressen_statt_kommandos(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            server = self._schreibe(tmp, 8790)
        for name in main._COMBINED_MCP_NAMES:
            self.assertEqual(server[name], {"type": "http",
                                            "url": f"http://127.0.0.1:8790/mcp/{name}"})
        self.assertFalse(any(e.get("command") == "node" for e in server.values()))

    def test_einzelmodus_schreibt_weiterhin_stdio(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            server = self._schreibe(tmp, None)
        self.assertEqual(server["memory"]["command"], "node")
        self.assertEqual(server["memory"]["args"], ["/opt/mcp/memory-server.mjs"])
        self.assertNotIn("url", server["memory"])

    def test_register_reicht_den_modus_durch(self):
        """Nicht nur die Funktion, auch der Aufrufer muss den Port kennen —
        sonst bleibt der Fehler bei unveraenderter Signatur bestehen."""
        gesehen = []
        with mock.patch.object(main, "_start_combined_mcp", return_value=True), \
                mock.patch.object(main, "_run_mcp_add", return_value=True), \
                mock.patch.object(main, "_write_mcp_json_fallback", gesehen.append), \
                mock.patch.dict(os.environ, {
                    "MCP_HTTP_PORT": "8790", "MSGRAPH_ENABLED": "", "COMPUTER_USE_BROWSER": "",
                    "COMPUTER_USE_BRIDGE_MCP_URL": "", "CUSTOM_MCP_SERVERS": "",
                }, clear=False):
            main.register_mcp_servers()
        self.assertEqual(gesehen, [8790])
        gesehen.clear()
        with mock.patch.object(main, "_start_combined_mcp", return_value=False), \
                mock.patch.object(main, "_run_mcp_add", return_value=True), \
                mock.patch.object(main, "_write_mcp_json_fallback", gesehen.append), \
                mock.patch.dict(os.environ, {
                    "MCP_HTTP_PORT": "8790", "MSGRAPH_ENABLED": "", "COMPUTER_USE_BROWSER": "",
                    "COMPUTER_USE_BRIDGE_MCP_URL": "", "CUSTOM_MCP_SERVERS": "",
                }, clear=False):
            main.register_mcp_servers()
        self.assertEqual(gesehen, [None])
