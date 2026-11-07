"""Zugänge nur für den Agenten-Server, nicht für seine Werkzeuge (app/serverwerte.py).

Vorher erbte jeder Kindprozess — CLI, jeder Bash-Befehl, ``npm install`` samt
Paket-Skripten — die Redis-Anmeldung, den Browserprofil-Schlüssel und die Zugänge zu
eigenen MCP-Servern. Ein ``env`` in einer Build-Ausgabe trug sie in Chat, Commits, Tickets.
"""

import ast
import json
import os
import pathlib
import subprocess
import sys
import unittest
from unittest.mock import patch

from app import serverwerte

APP = pathlib.Path(__file__).resolve().parents[1] / "app"


class Basis(unittest.TestCase):
    def setUp(self):
        serverwerte._werte.clear()
        self._env = patch.dict(os.environ, {}, clear=False)
        self._env.start()

    def tearDown(self):
        self._env.stop()
        serverwerte._werte.clear()


class SpeicherTests(Basis):

    def test_uebernehmen_nimmt_aus_der_umgebung(self):
        os.environ.update(REDIS_URL="redis://agent-x:geheim@redis:6379", BROWSER_PROFIL_KEY="k" * 44,
                          CUSTOM_MCP_AUTH='{"a": "t"}', AGENT_TOKEN="a" * 32)
        genommen = serverwerte.uebernehmen()
        self.assertEqual(set(genommen), {"REDIS_URL", "BROWSER_PROFIL_KEY", "CUSTOM_MCP_AUTH"})
        for name in genommen:
            self.assertNotIn(name, os.environ)
        self.assertEqual(serverwerte.get("REDIS_URL"), "redis://agent-x:geheim@redis:6379")
        self.assertEqual(os.environ["AGENT_TOKEN"], "a" * 32, "Werkzeuge brauchen das Agenten-Token")

    def test_kindprozess_erbt_sie_nicht(self):
        os.environ.update(REDIS_URL="redis://agent-x:geheim@redis:6379", CUSTOM_MCP_HEADERS='{"a": {"k": "v"}}')
        serverwerte.uebernehmen()
        aus = subprocess.run([sys.executable, "-c", "import os; print(sorted(k for k in os.environ "
                              "if k in ('REDIS_URL', 'CUSTOM_MCP_HEADERS')))"],
                             capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(aus, "[]")

    def test_ohne_uebernahme_gilt_die_umgebung(self):
        os.environ["BROWSER_PROFIL_KEY"] = "x"
        self.assertEqual(serverwerte.get("BROWSER_PROFIL_KEY"), "x")
        self.assertEqual(serverwerte.get("CUSTOM_MCP_AUTH", "leer"), "leer")

    def test_setzen_nie_in_die_umgebung(self):
        os.environ["CUSTOM_MCP_AUTH"] = "alt"
        serverwerte.setzen("CUSTOM_MCP_AUTH", "neu")
        self.assertEqual(serverwerte.get("CUSTOM_MCP_AUTH"), "neu")
        self.assertNotIn("CUSTOM_MCP_AUTH", os.environ)

    def test_setzen_nur_fuer_serverwerte(self):
        with self.assertRaises(ValueError):
            serverwerte.setzen("AGENT_TOKEN", "x")

    def test_agenten_token_bleibt_draussen(self):
        self.assertNotIn("AGENT_TOKEN", serverwerte.NUR_SERVER)


class LeserTests(Basis):
    """Jeder Leser holt die Werte aus dem Speicher — nach der Übernahme stehen sie nicht mehr in der Umgebung."""

    def _uebernommen(self, **werte):
        os.environ.update(werte)
        serverwerte.uebernehmen()

    def test_mcp_zugaenge_beim_start(self):
        from app import main
        self._uebernommen(CUSTOM_MCP_AUTH='{"crm": "tok"}', CUSTOM_MCP_HEADERS='{"crm": {"x-api-key": "k"}}')
        self.assertEqual(main._load_custom_mcp_auth(), {"crm": "tok"})
        self.assertEqual(main._load_custom_mcp_headers(), {"crm": {"x-api-key": "k"}})

    def test_mcp_client(self):
        from app.tools.mcp_client import MCPHTTPClient
        self._uebernommen(CUSTOM_MCP_AUTH='{"crm": "tok"}', CUSTOM_MCP_HEADERS='{"crm": {"x-api-key": "k"}}')
        os.environ["CUSTOM_MCP_SERVERS"] = json.dumps({"crm": "https://crm.example.com/mcp"})
        c = MCPHTTPClient()
        c._load_servers()
        self.assertEqual(c._auth, {"crm": "tok"})
        self.assertEqual(c._headers, {"crm": {"x-api-key": "k"}})

    def test_browserprofil_schluessel(self):
        from cryptography.fernet import Fernet

        from app import profile_crypto
        k = Fernet.generate_key().decode()
        self._uebernommen(BROWSER_PROFIL_KEY=k)
        self.assertEqual(profile_crypto._schluessel(), k.encode())

    def test_codex_konfiguration_bekommt_den_mcp_zugang(self):
        import tempfile

        from app import codex_runner
        self._uebernommen(CUSTOM_MCP_AUTH='{"crm": "tok-123"}')
        env = {"CUSTOM_MCP_SERVERS": json.dumps({"crm": "https://crm.example.com/mcp"})}
        with tempfile.TemporaryDirectory() as home:
            codex_runner._ensure_codex_mcp_config(home, env)
            toml = pathlib.Path(home, "config.toml").read_text()
        self.assertIn('bearer_token_env_var = "MCP_TOKEN_crm"', toml)
        self.assertEqual(env["MCP_TOKEN_crm"], "tok-123")
        self.assertNotIn("CUSTOM_MCP_AUTH", env)

    def test_codex_eigener_zugang(self):
        import tempfile

        from app import main
        self._uebernommen(CODEX_AUTH_JSON='{"tokens": {"access_token": "x"}}')
        with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, {"CODEX_HOME": home}), \
                patch("app.codex_auth_sync.record_start"):
            main.setup_codex_auth()
            self.assertIn("access_token", pathlib.Path(home, "auth.json").read_text())
        self.assertEqual(serverwerte.get("CODEX_AUTH_JSON"), "", "nach dem Schreiben auch aus dem Speicher")

    def test_erneuerung_schreibt_nicht_in_die_umgebung(self):
        src = (APP / "main.py").read_text(encoding="utf-8")
        start = src.index("async def refresh_mcp_credentials_loop")
        teil = src[start:src.index("\nasync def ", start + 10) if "\nasync def " in src[start + 10:] else None]
        self.assertNotIn('os.environ["CUSTOM_MCP_AUTH"]', teil)
        self.assertNotIn('os.environ["CUSTOM_MCP_HEADERS"]', teil)
        self.assertIn('serverwerte.setzen("CUSTOM_MCP_AUTH"', teil)


class VerdrahtungTests(unittest.TestCase):

    def test_niemand_liest_serverwerte_an_serverwerte_vorbei(self):
        """Kein ``os.environ``/``os.getenv`` mit einem reinen Server-Wert außerhalb von serverwerte.py."""
        funde = []
        for datei in APP.rglob("*.py"):
            if datei.name == "serverwerte.py":
                continue
            for knoten in ast.walk(ast.parse(datei.read_text(encoding="utf-8"))):
                if isinstance(knoten, ast.Constant) and knoten.value in serverwerte.NUR_SERVER:
                    eltern = ast.unparse(knoten)
                    funde.append((datei.relative_to(APP), knoten.lineno, eltern))
        src_zeilen = {}
        verdaechtig = []
        for datei, zeile, _ in funde:
            text = src_zeilen.setdefault(datei, (APP / datei).read_text(encoding="utf-8").splitlines())[zeile - 1]
            if "os.environ" in text or "os.getenv" in text:
                verdaechtig.append(f"{datei}:{zeile}: {text.strip()}")
        self.assertEqual(verdaechtig, [])

    def test_uebernahme_vor_dem_ersten_kindprozess(self):
        src = (APP / "main.py").read_text(encoding="utf-8")
        haupt = src[src.index("async def main() -> None:"):]
        uebernahme = haupt.index("serverwerte.uebernehmen()")
        for erster in ("setup_github_credentials()", "setup_codex_auth()", "register_mcp_servers()",
                       "discover_custom_mcp_tools()", "start_health_server("):
            self.assertLess(uebernahme, haupt.index(erster), erster)

    def test_startskript_kennt_dieselbe_liste(self):
        """entrypoint.sh nimmt npm dieselben Werte weg wie serverwerte dem Server."""
        import re
        skript = (APP.parent / "entrypoint.sh").read_text(encoding="utf-8")
        zeile = re.search(r'^OHNE_ZUGAENGE="env ([^"]+)"', skript, re.M).group(1)
        self.assertEqual(set(re.findall(r"-u (\S+)", zeile)), set(serverwerte.NUR_SERVER) | {"AGENT_TOKEN"})

    def test_einstellungen_vor_der_uebernahme_geladen(self):
        """settings.redis_url liest REDIS_URL beim Import — main.py importiert settings oben."""
        src = (APP / "main.py").read_text(encoding="utf-8")
        self.assertIn("from app.config import", src[:src.index("async def main()")])


if __name__ == "__main__":
    unittest.main()
