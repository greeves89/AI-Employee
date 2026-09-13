"""Codex holt die eingebauten MCP-Server aus dem Sammelprozess (TODO #325).

Der Umbau aus #638 Phase 3 — elf node-Prozesse zu einem — war nur fuer Claude
Code verdrahtet. Ein Codex-Agent schrieb weiterhin je Aufruf zehn stdio-Server
in seine ``config.toml`` und forkte sie alle. Er lief damit als erster in die
harte pids-Grenze des Containers (#628), und ab da scheitert jedes Werkzeug
still.

Zwei Luecken, beide hier festgenagelt:
1. ``main.py`` startete den Sammelprozess im Codex-Zweig gar nicht erst.
2. ``codex_runner`` schrieb die Server unbedingt als stdio.

Die Leitplanke fuer beides: dieser Umbau darf einem Agenten niemals ein
Werkzeug NEHMEN. Er darf nur Prozesse sparen.
"""

import os
import pathlib
import tempfile
import unittest
from unittest import mock

from app.codex_runner import GEMEINSAME_MCP_ROUTEN, _ensure_codex_mcp_config
from app.pids_budget import gemeinsame_mcp_routen
from tests._mcp_umgebung import Sammelprozess, StummerPort, toter_port

_REPO = pathlib.Path(__file__).resolve().parents[1]

#: Was ein vollstaendig hochgefahrener Sammelprozess meldet.
ALLE_ROUTEN = sorted(GEMEINSAME_MCP_ROUTEN.values())


class DieSondeFragtDieAnlageTests(unittest.TestCase):
    """``MCP_HTTP_PORT`` ist eine Absicht von frueher, keine Zusage fuer jetzt."""

    def test_ohne_variable_keine_routen(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MCP_HTTP_PORT", None)
            self.assertEqual(gemeinsame_mcp_routen(), (0, set()))

    def test_ein_gesunder_prozess_nennt_seine_routen(self):
        with Sammelprozess(ALLE_ROUTEN) as port, mock.patch.dict(
            os.environ, {"MCP_HTTP_PORT": str(port)}
        ):
            self.assertEqual(gemeinsame_mcp_routen(), (port, set(ALLE_ROUTEN)))

    def test_ein_toter_port_gilt_als_nicht_vorhanden(self):
        """Variable gesetzt, Prozess gestorben — die config.toml zeigte sonst
        ins Leere, und der Agent haette kein einziges Werkzeug."""
        with mock.patch.dict(os.environ, {"MCP_HTTP_PORT": str(toter_port())}):
            self.assertEqual(gemeinsame_mcp_routen(), (0, set()))

    def test_ein_stummer_port_zaehlt_nicht(self):
        """Genau hier reichte ein blosses ``connect`` nicht: es lauscht etwas,
        aber es ist nicht der Sammelprozess."""
        with StummerPort() as port, mock.patch.dict(
            os.environ, {"MCP_HTTP_PORT": str(port)}
        ):
            self.assertEqual(gemeinsame_mcp_routen(), (0, set()))

    def test_eine_fehlerantwort_zaehlt_nicht(self):
        with Sammelprozess(kaputt=True) as port, mock.patch.dict(
            os.environ, {"MCP_HTTP_PORT": str(port)}
        ):
            self.assertEqual(gemeinsame_mcp_routen(), (0, set()))

    def test_eine_leere_liste_zaehlt_nicht(self):
        with Sammelprozess(routen=[]) as port, mock.patch.dict(
            os.environ, {"MCP_HTTP_PORT": str(port)}
        ):
            self.assertEqual(gemeinsame_mcp_routen(), (0, set()))

    def test_unsinn_in_der_variablen_bricht_nichts(self):
        with mock.patch.dict(os.environ, {"MCP_HTTP_PORT": "achttausend"}):
            self.assertEqual(gemeinsame_mcp_routen(), (0, set()))

    def test_null_heisst_ausgeschaltet(self):
        with mock.patch.dict(os.environ, {"MCP_HTTP_PORT": "0"}):
            self.assertEqual(gemeinsame_mcp_routen(), (0, set()))


class DieKonfigurationNutztDenSammelprozessTests(unittest.TestCase):
    ENV = {
        "ORCHESTRATOR_URL": "http://example.invalid:8000",
        "AGENT_ID": "agent-1",
        "AGENT_TOKEN": "geheim",
    }

    def _schreiben(self, port: int | None = None, env_extra: dict | None = None) -> str:
        """Konfiguration in ein Wegwerfverzeichnis schreiben und zurueckgeben.

        ``os.path.exists`` wird auf True gezwungen: ob ``/opt/mcp`` in DIESER
        Umgebung liegt, darf das Ergebnis nicht bestimmen — genau solche
        Umgebungsabhaengigkeiten haben #326 jahrelang verdeckt.
        """
        env = {**self.ENV, **(env_extra or {})}
        umgebung = {"MCP_HTTP_PORT": str(port)} if port else {}
        with tempfile.TemporaryDirectory() as d:
            with mock.patch("app.codex_runner.os.path.exists", return_value=True), \
                    mock.patch.dict(os.environ, umgebung, clear=False):
                if not port:
                    os.environ.pop("MCP_HTTP_PORT", None)
                _ensure_codex_mcp_config(d, env)
            return (pathlib.Path(d) / "config.toml").read_text()

    def test_mit_sammelprozess_stehen_adressen_statt_prozessen(self):
        with Sammelprozess() as port:
            text = self._schreiben(port)
        self.assertIn(f'url = "http://127.0.0.1:{port}/mcp/orchestrator"', text)
        self.assertNotIn('command = "node"', text)

    def test_die_namen_bleiben_die_alten(self):
        """Der Werkzeug-Namensraum haengt an ihnen — ``mcp__orchestrator.*``."""
        with Sammelprozess() as port:
            text = self._schreiben(port)
        for name in GEMEINSAME_MCP_ROUTEN:
            with self.subTest(name=name):
                self.assertIn(f"[mcp_servers.{name}]", text)

    def test_die_umbenannten_routen_stimmen(self):
        """Drei Namen unterscheiden sich zwischen Codex und dem Sammelprozess.
        Ein Tippfehler hier ergibt ein 404 statt eines Werkzeugs — ohne Fehler."""
        with Sammelprozess() as port:
            text = self._schreiben(port)
        for name, route in (("skill", "skills"), ("notification", "notifications"),
                            ("read_logs", "read-logs")):
            with self.subTest(name=name):
                self.assertIn(f'url = "http://127.0.0.1:{port}/mcp/{route}"', text)
                self.assertIn(f"[mcp_servers.{name}]", text)

    def test_ohne_sammelprozess_bleibt_alles_wie_bisher(self):
        text = self._schreiben(None)
        self.assertIn('command = "node"', text)
        self.assertIn("[mcp_servers.orchestrator.env]", text)
        # Nicht auf "/mcp/orchestrator" pruefen: das steht auch im stdio-Pfad
        # ``/opt/mcp/orchestrator-server.mjs``.
        self.assertNotIn("url = ", text)

    def test_ein_nicht_geladener_server_behaelt_seinen_prozess(self):
        """Der Fall, der den Umbau sonst teuer gemacht haette: ``_all.mjs``
        laeuft bewusst weiter, wenn ein einzelner Server nicht laedt. Wer
        daraufhin trotzdem alle Adressen schreibt, taeuscht Werkzeuge vor, die
        mit 404 antworten — also WENIGER als mit Einzelprozessen."""
        ohne_brain = [r for r in ALLE_ROUTEN if r != "brain"]
        with Sammelprozess(routen=ohne_brain) as port:
            text = self._schreiben(port)
        brain = text.split("[mcp_servers.brain]", 1)[1].split("[mcp_servers.", 1)[0]
        self.assertIn('command = "node"', brain)
        self.assertNotIn("url = ", brain)
        # Die uebrigen laufen trotzdem gemeinsam — die Entscheidung faellt
        # pro Server, nicht fuer alle zusammen.
        self.assertIn(f'url = "http://127.0.0.1:{port}/mcp/memory"', text)

    def test_msgraph_ohne_anbindung_behaelt_seinen_prozess(self):
        """Der Sammelprozess haengt msgraph nur bei eingeschalteter
        Microsoft-Anbindung ein. Dass die Liste das schon sagt, macht eine
        zweite Abfrage von ``MSGRAPH_ENABLED`` hier ueberfluessig."""
        ohne_msgraph = [r for r in ALLE_ROUTEN if r != "msgraph"]
        with Sammelprozess(routen=ohne_msgraph) as port:
            text = self._schreiben(port)
        self.assertIn("[mcp_servers.msgraph.env]", text)
        self.assertNotIn(f"{port}/mcp/msgraph", text)

    def test_msgraph_mit_anbindung_kommt_ueber_die_adresse(self):
        with Sammelprozess() as port:
            text = self._schreiben(port)
        self.assertIn(f'url = "http://127.0.0.1:{port}/mcp/msgraph"', text)

    def test_kein_abschnitt_steht_doppelt(self):
        """Codex bricht beim ERSTEN doppelten Schluessel ab und laedt dann GAR
        KEINE Konfiguration — der Agent haette danach kein einziges Werkzeug."""
        with Sammelprozess() as port:
            text = self._schreiben(port, {
                "CUSTOM_MCP_SERVERS": '{"orchestrator": "http://example.invalid/x"}',
            })
        self.assertEqual(text.count("[mcp_servers.orchestrator]"), 1)

    def test_kein_zugangstoken_in_den_adressabschnitten(self):
        """Ueber HTTP braucht Codex den Token nicht — der Sammelprozess hat ihn
        aus der Container-Umgebung. Was nicht in der Datei steht, kann auch
        nicht daraus entwischen."""
        with Sammelprozess() as port:
            text = self._schreiben(port)
        self.assertNotIn("geheim", text)


class DieRoutenGibtEsWirklichTests(unittest.TestCase):
    """Gegenprobe gegen den Sammelprozess selbst, nicht gegen eine zweite Liste.

    Zwei von Hand gepflegte Listen laufen auseinander, ohne dass es jemand
    merkt — genau so fehlten einem Codex-Agenten im August Microsoft 365, Mail
    und Video.
    """

    def test_jede_route_wird_vom_sammelprozess_bedient(self):
        quelle = (_REPO / "mcp" / "_all.mjs").read_text()
        for name, route in GEMEINSAME_MCP_ROUTEN.items():
            with self.subTest(name=name):
                self.assertIn(f'["{route}", "./', quelle)


class DerCodexZweigStartetDenSammelprozessTests(unittest.TestCase):
    """Luecke 1: ohne diesen Start zeigt die config.toml auf einen Port, den
    im Codex-Container nie jemand geoeffnet hat."""

    def test_der_start_steht_im_codex_zweig(self):
        quelle = (_REPO / "app" / "main.py").read_text()
        zweig = quelle.split('elif mode == "codex_cli":', 1)[1].split("\n    else:", 1)[0]
        self.assertIn("_start_combined_mcp", zweig)
        self.assertIn("MCP_HTTP_PORT", zweig)


if __name__ == "__main__":
    unittest.main()
