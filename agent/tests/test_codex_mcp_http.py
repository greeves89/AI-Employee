"""Codex nutzt den gemeinsamen MCP-Prozess statt eigener stdio-Prozesse (#638).

Phase 3 hat die eingebauten MCP-Server in EINEN Prozess zusammengelegt: statt
elf node-Prozessen (81 Threads, ~691 MB) laeuft einer (7 Threads, 82 MB). Der
Claude-Code-Pfad war umgestellt, der Codex-Pfad nicht — ein Codex-Agent startete
weiterhin jeden Server einzeln und verbrauchte das Vielfache an Speicher.

Beim Umstellen gibt es drei Fallen, und jede von ihnen nimmt dem Agenten
Werkzeuge, ohne dass irgendetwas rot wird:

1. Der Abschnittsname in der config.toml ist zugleich das Praefix, unter dem der
   Agent seine Werkzeuge sieht. ``read_logs`` muss ``read_logs`` bleiben — auch
   wenn die Route im gemeinsamen Prozess ``read-logs`` heisst, weil dort keine
   Unterstriche erlaubt sind.
2. ``msgraph`` bietet der gemeinsame Prozess nur bei ``MSGRAPH_ENABLED=true`` an.
   Ohne diese Bedingung zeigte Codex auf eine tote Adresse, wo er bisher einen
   laufenden Server hatte.
3. Ohne laufenden gemeinsamen Prozess muss alles unveraendert bleiben. Ein Agent,
   der mehr Speicher braucht, ist besser als einer ohne Werkzeuge.
"""

import json
import os
import re
import tempfile
import unittest
from pathlib import Path
import unittest.mock
from unittest.mock import patch

from app.codex_runner import _MCP_HTTP_ROUTEN, _ensure_codex_mcp_config

_ALL_MJS = (Path(__file__).resolve().parents[1] / "mcp" / "_all.mjs").read_text()

# Was der gemeinsame Prozess ohne Microsoft-Anbindung anbietet.
_ROUTEN_OHNE_MSGRAPH = sorted(set(_MCP_HTTP_ROUTEN.values()) - {"msgraph"})


def _config(env_zusatz: dict, *, routen: list | None = None,
            custom: dict | None = None) -> str:
    """Die Konfiguration wirklich schreiben lassen und zurueckgeben.

    ``routen`` ist die Antwort, die der gemeinsame MCP-Prozess auf /health gibt;
    None steht fuer „antwortet gar nicht".
    """
    env = {
        "AGENT_ID": "a1",
        "AGENT_TOKEN": "t",
        "AGENT_NAME": "Testi",
        "ORCHESTRATOR_URL": "http://orchestrator:8000",
        **env_zusatz,
    }
    if custom is not None:
        env["CUSTOM_MCP_SERVERS"] = json.dumps(custom)
    heim = tempfile.mkdtemp(prefix="codex-home-")
    with patch("os.path.exists", return_value=True), _health(routen):
        _ensure_codex_mcp_config(heim, env)
    with open(os.path.join(heim, "config.toml")) as f:
        return f.read()


def _health(routen: list | None):
    """/health des gemeinsamen Prozesses nachstellen."""
    if routen is None:
        return patch("urllib.request.urlopen", side_effect=OSError("kein Prozess"))
    antwort = unittest.mock.MagicMock()
    antwort.__enter__.return_value.read.return_value = json.dumps(
        {"ok": True, "servers": routen}
    ).encode()
    return patch("urllib.request.urlopen", return_value=antwort)


def _abschnitt(text: str, name: str) -> str:
    """Der Rumpf von [mcp_servers.<name>] bis zum naechsten Abschnitt."""
    treffer = re.search(
        rf"^\[mcp_servers\.{re.escape(name)}\]\n(.*?)(?=^\[|\Z)",
        text, re.M | re.S,
    )
    return treffer.group(1) if treffer else ""


_HTTP_ENV = {"MCP_HTTP_ACTIVE": "1", "MCP_HTTP_PORT": "9100"}


class OhneGemeinsamenProzessBleibtAllesWieBisher(unittest.TestCase):
    def test_ohne_flag_stdio(self):
        text = _config({}, routen=_ROUTEN_OHNE_MSGRAPH)
        self.assertIn('command = "node"', _abschnitt(text, "brain"))
        self.assertNotIn("url =", _abschnitt(text, "brain"))

    def test_port_ohne_flag_reicht_nicht(self):
        # MCP_HTTP_PORT allein sagt nur, was gewuenscht ist. Ob der Prozess
        # ueberhaupt gestartet wurde, weiss nur main.py — und setzt dann das Flag.
        text = _config({"MCP_HTTP_PORT": "9100"}, routen=_ROUTEN_OHNE_MSGRAPH)
        self.assertIn('command = "node"', _abschnitt(text, "brain"))

    def test_kaputter_port_faellt_auf_stdio_zurueck(self):
        text = _config({"MCP_HTTP_ACTIVE": "1", "MCP_HTTP_PORT": "keine-zahl"},
                       routen=_ROUTEN_OHNE_MSGRAPH)
        self.assertIn('command = "node"', _abschnitt(text, "brain"))

    def test_gestorbener_prozess_faellt_auf_stdio_zurueck(self):
        # Das Flag stammt vom Hochfahren. Stirbt der Prozess danach — bei knappem
        # Speicher der Regelfall —, darf der naechste Lauf nicht weiter auf einen
        # Port zeigen, an dem niemand mehr horcht.
        text = _config(_HTTP_ENV, routen=None)
        self.assertIn('command = "node"', _abschnitt(text, "brain"))
        self.assertNotIn("url =", text.split("[mcp_servers.brain]", 1)[1][:200])

    def test_fehlende_route_bleibt_stdio(self):
        # _all.mjs laedt jeden Server einzeln. Ein misslungener Import nimmt genau
        # eine Route weg, ohne dass der Port das merken liesse.
        text = _config(_HTTP_ENV, routen=[r for r in _ROUTEN_OHNE_MSGRAPH
                                          if r != "brain"])
        self.assertIn('command = "node"', _abschnitt(text, "brain"))
        self.assertIn("url =", _abschnitt(text, "memory"))


class MitGemeinsamemProzess(unittest.TestCase):
    def test_eingebaute_server_zeigen_auf_den_gemeinsamen_prozess(self):
        text = _config(_HTTP_ENV, routen=_ROUTEN_OHNE_MSGRAPH)
        for name in ("brain", "memory", "orchestrator", "desktop"):
            with self.subTest(name=name):
                rumpf = _abschnitt(text, name)
                self.assertIn("http://127.0.0.1:9100/mcp/", rumpf)
                self.assertNotIn('command = "node"', rumpf)

    def test_abschnittsnamen_bleiben_unveraendert(self):
        # Aendert sich hier ein Name, heissen fuer den Agenten schlagartig alle
        # Werkzeuge dieses Servers anders — und jede gespeicherte Anweisung, die
        # sie beim Namen nennt, laeuft ins Leere.
        text = _config(_HTTP_ENV, routen=_ROUTEN_OHNE_MSGRAPH)
        for name in _MCP_HTTP_ROUTEN:
            with self.subTest(name=name):
                self.assertIn(f"[mcp_servers.{name}]", text)

    def test_read_logs_behaelt_unterstrich_aber_route_hat_bindestrich(self):
        text = _config(_HTTP_ENV, routen=_ROUTEN_OHNE_MSGRAPH)
        self.assertIn(
            'url = "http://127.0.0.1:9100/mcp/read-logs"',
            _abschnitt(text, "read_logs"),
        )

    def test_kein_agent_token_mehr_in_den_http_abschnitten(self):
        # Der gemeinsame Prozess hat die Umgebung des Agenten geerbt. Ein
        # [env]-Block waere wirkungslos und legte den Token nur erneut ab.
        text = _config(_HTTP_ENV, routen=_ROUTEN_OHNE_MSGRAPH)
        self.assertNotIn("[mcp_servers.brain.env]", text)

    def test_msgraph_bleibt_stdio_solange_der_prozess_ihn_nicht_anbietet(self):
        text = _config(_HTTP_ENV, routen=_ROUTEN_OHNE_MSGRAPH)
        self.assertIn('command = "node"', _abschnitt(text, "msgraph"))

    def test_msgraph_per_http_sobald_der_prozess_ihn_anbietet(self):
        text = _config(_HTTP_ENV, routen=[*_ROUTEN_OHNE_MSGRAPH, "msgraph"])
        self.assertIn(
            'url = "http://127.0.0.1:9100/mcp/msgraph"',
            _abschnitt(text, "msgraph"),
        )

    def test_eingeschleuster_msgraph_erzeugt_keinen_doppelten_abschnitt(self):
        # Der Ausfall vom 2026-08-16 in genau der Lage, die es vorher nicht gab:
        # der eingebaute msgraph steht jetzt als HTTP-Adresse in der Datei, und
        # ueber CUSTOM_MCP_SERVERS kommt derselbe Name ein zweites Mal. Ein
        # doppelter Schluessel — und Codex laedt GAR KEINE Konfiguration mehr.
        import tomllib
        text = _config(_HTTP_ENV, routen=[*_ROUTEN_OHNE_MSGRAPH, "msgraph"],
                       custom={"msgraph": "http://orchestrator:8000/mcp/msgraph"})
        self.assertEqual(1, text.count("[mcp_servers.msgraph]"))
        tomllib.loads(text)

    def test_konfiguration_bleibt_lesbar(self):
        import tomllib
        daten = tomllib.loads(_config(_HTTP_ENV, routen=_ROUTEN_OHNE_MSGRAPH))
        self.assertGreater(len(daten["mcp_servers"]), 5)


class RoutenStimmenMitDemGemeinsamenProzessUeberein(unittest.TestCase):
    """Zwei Listen, die niemand gegeneinander prueft, laufen auseinander.

    Genau so fehlten einem Codex-Agenten am 2026-08-12 Microsoft 365, Mail und
    Video. Eine Route, die _all.mjs nicht anbietet, ist hier dasselbe: der
    Abschnitt sieht gesund aus und der erste Werkzeugaufruf schlaegt fehl.
    """

    def test_jede_route_existiert_auch_in_all_mjs(self):
        vorhanden = set(re.findall(r'\[\s*"([a-z0-9-]+)"\s*,\s*"\./', _ALL_MJS))
        self.assertIn("brain", vorhanden, "Routen aus _all.mjs nicht erkannt")
        fehlend = set(_MCP_HTTP_ROUTEN.values()) - vorhanden
        self.assertEqual(set(), fehlend)

    def test_kein_eingebauter_server_bleibt_ohne_route_zurueck(self):
        # Kommt spaeter ein eingebauter Server dazu und wird die Karte oben
        # vergessen, liefe er als einziger weiter als eigener Prozess — eine
        # Mischform, die niemandem auffaellt und die Ersparnis stillschweigend
        # auffrisst.
        text = _config(_HTTP_ENV, routen=[*_ROUTEN_OHNE_MSGRAPH, "msgraph"])
        stdio = re.findall(r"^\[mcp_servers\.(\w+)\]\ncommand = ", text, re.M)
        self.assertEqual([], stdio)

    def test_bash_approval_bekommt_codex_nicht_nebenbei(self):
        # bash-approval steht bewusst nicht in der Codex-Liste. Die Umstellung
        # des Transports darf keine Faehigkeit hinzufuegen.
        self.assertNotIn("bash-approval", _MCP_HTTP_ROUTEN.values())


class DasFlagKommtVomTatsaechlichLaufendenProzess(unittest.TestCase):
    """MCP_HTTP_ACTIVE ist die einzige Bruecke zwischen main.py und codex_runner.

    codex_runner schreibt die config.toml, sieht aber nicht, ob der gemeinsame
    Prozess hochgekommen ist. Deshalb darf das Flag ausschliesslich auf dem
    Erfolgspfad gesetzt werden — sonst zeigt Codex auf einen Port, an dem
    niemand horcht, und hat kein einziges Werkzeug.
    """

    def setUp(self):
        os.environ.pop("MCP_HTTP_ACTIVE", None)
        self.addCleanup(os.environ.pop, "MCP_HTTP_ACTIVE", None)

    def _starten(self, *, prozess_laeuft: bool, port_antwortet: bool) -> bool:
        from app import main as app_main

        prozess = unittest.mock.MagicMock()
        prozess.poll.return_value = None if prozess_laeuft else 1
        prozess.stderr.read.return_value = "boom"
        buchse = unittest.mock.MagicMock()
        buchse.__enter__.return_value.connect_ex.return_value = 0 if port_antwortet else 1
        with patch.object(app_main.subprocess, "Popen", return_value=prozess), \
                patch("socket.socket", return_value=buchse), \
                patch("time.sleep"):
            return app_main._start_combined_mcp(9100)

    def test_flag_nur_wenn_der_port_antwortet(self):
        self.assertTrue(self._starten(prozess_laeuft=True, port_antwortet=True))
        self.assertEqual("1", os.environ.get("MCP_HTTP_ACTIVE"))

    def test_kein_flag_wenn_der_prozess_sofort_stirbt(self):
        self.assertFalse(self._starten(prozess_laeuft=False, port_antwortet=True))
        self.assertIsNone(os.environ.get("MCP_HTTP_ACTIVE"))

    def test_codex_pfad_startet_den_gemeinsamen_prozess(self):
        # Ohne diesen Aufruf lief der gemeinsame Prozess nur im
        # Claude-Code-Zweig — genau das war die Luecke.
        quelle = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text()
        zweig = quelle.split('elif mode == "codex_cli":', 1)[1].split("    else:", 1)[0]
        self.assertIn("_start_combined_mcp(", zweig)
        self.assertIn('os.environ.get("MCP_HTTP_PORT")', zweig)


if __name__ == "__main__":
    unittest.main()
