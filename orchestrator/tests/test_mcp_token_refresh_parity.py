"""OAuth-Token-Auffrischung in ALLEN Laufzeiten (#488).

Ein OAuth-Zugriffstoken lebt ein bis zwei Stunden. CUSTOM_MCP_AUTH wird aber nur
EINMAL gesetzt, beim Erstellen des Containers — ein lang laufender Agent verlor damit
jeden OAuth-geschuetzten MCP-Server innerhalb der ersten Stunde.

Orchestrator-Seite (periodischer Lauf) und Endpunkt waren bereits gebaut. Die
Agenten-Schleife auch — aber MIT AUSNAHME von codex_cli, begruendet damit, Codex nutze
CUSTOM_MCP_* gar nicht.

Das stimmt nicht: ``_ensure_codex_mcp_config`` liest CUSTOM_MCP_SERVERS und
CUSTOM_MCP_AUTH aus ``os.environ.copy()`` und schreibt die config.toml bei JEDEM
Codex-Aufruf neu. Auf einer Anlage, auf der sieben von acht Agenten Codex sind, war
der Fix damit fuer fast niemanden wirksam — wieder ein Weg, der an einer vorhandenen
Faehigkeit vorbeigeht.
"""

import ast
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
AGENT = REPO / "agent"
ORCH = REPO / "orchestrator"


def _ohne_kommentare(block: str) -> str:
    """Kommentare aus einem Quelltextblock tilgen (per tokenize, nicht per
    '#'-Suche). Ein auskommentierter Aufruf stuende sonst weiterhin im Block
    und bestuende jedes `assertIn` — die Blindstelle aus #726."""
    import io
    import textwrap
    import tokenize

    text = textwrap.dedent(block)
    zeilen = text.splitlines(keepends=True)
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                (zeile, von), (_, bis) = tok.start, tok.end
                zeilen[zeile - 1] = zeilen[zeile - 1][:von] + zeilen[zeile - 1][bis:]
    except tokenize.TokenError as e:  # unvollstaendiger Block — lieber laut
        raise AssertionError(f"Block nicht tokenisierbar: {e}")
    return "".join(zeilen)


def _funktionsrumpf(src: str, name: str) -> str:
    """Der RUMPF der Funktion `name` — ohne Signatur, ohne Docstring, ohne
    Kommentare — als syntaktischer Block statt als 4000-Zeichen-Fenster.
    Ein laengerer Kommentar davor verschiebt den Block nicht; ein Wort, das
    nur in Signatur oder Docstring vorkommt, zaehlt nicht als Zuweisung."""
    for knoten in ast.walk(ast.parse(src)):
        if isinstance(knoten, (ast.FunctionDef, ast.AsyncFunctionDef)) and knoten.name == name:
            rumpf = knoten.body
            if (rumpf and isinstance(rumpf[0], ast.Expr)
                    and isinstance(getattr(rumpf[0], "value", None), ast.Constant)
                    and isinstance(rumpf[0].value.value, str)):
                rumpf = rumpf[1:]  # Docstring
            if not rumpf:
                raise AssertionError(f"def {name}: hat ausser dem Docstring keinen Rumpf")
            zeilen = src.splitlines(keepends=True)
            return _ohne_kommentare("".join(zeilen[rumpf[0].lineno - 1:knoten.end_lineno]))
    raise AssertionError(f"def {name}: nicht gefunden")


def _zuweisungen(src: str, name: str, ziel: str) -> list[tuple[int, str, list[str]]]:
    """ALLE Zuweisungen an `ziel` in der Funktion `name`, je als
    (Zeile, ganze Anweisung, `if`-Bedingungen der Ahnen). Nur den ersten Treffer
    zu nehmen liesse eine unbedingte Koeder-Zuweisung durch, hinter der die
    echte unter `if register_via_cli:` rutscht — deshalb die ganze Liste."""
    for fn in ast.walk(ast.parse(src)):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name == name:
            break
    else:
        raise AssertionError(f"def {name}: nicht gefunden")

    gefunden: list[tuple[int, str, list[str]]] = []

    def suche(knoten, ahnen):
        for kind in ast.iter_child_nodes(knoten):
            if (isinstance(kind, ast.Assign)
                    and any(ast.get_source_segment(src, t) == ziel for t in kind.targets)):
                gefunden.append((
                    kind.lineno,
                    ast.get_source_segment(src, kind) or "",
                    [ast.get_source_segment(src, a.test) for a in ahnen if isinstance(a, ast.If)],
                ))
            suche(kind, ahnen + [kind])

    suche(fn, [])
    if not gefunden:
        raise AssertionError(f"{ziel} = ... nicht in {name} gefunden")
    return gefunden


def _schleifenstart(src: str) -> tuple[str, str]:
    """(Startbedingung, Ausdruck fuer register_via_cli) des Aufrufs von
    refresh_mcp_credentials_loop im Agenten-Start — als Quelltext der AST-Knoten,
    damit die Tests sie mit Werten AUSWERTEN koennen statt Woerter zu suchen."""
    for knoten in ast.walk(ast.parse(src)):
        if not isinstance(knoten, ast.If):
            continue
        for aufruf in ast.walk(knoten):
            if (isinstance(aufruf, ast.Call) and isinstance(aufruf.func, ast.Name)
                    and aufruf.func.id == "refresh_mcp_credentials_loop"):
                flag = next(k.value for k in aufruf.keywords if k.arg == "register_via_cli")
                return (ast.get_source_segment(src, knoten.test),
                        ast.get_source_segment(src, flag))
    raise AssertionError("Start von refresh_mcp_credentials_loop nicht gefunden")


def _umgebung(**env):
    import types
    return types.SimpleNamespace(environ=dict(env))


class OrchestratorSideTests(unittest.TestCase):
    def test_periodic_sweep_runs(self):
        """Nicht nur beim Bauen eines Containers."""
        src = (ORCH / "app/main.py").read_text()
        self.assertIn("refresh_all_oauth_servers", src)
        self.assertIn("_refresh_mcp_oauth_tokens", src)

    def test_endpoint_exists_for_running_agents(self):
        from app.api import agents

        paths = {r.path for r in agents.router.routes}
        self.assertIn("/agents/{agent_id}/mcp-credentials", paths)


class AgentSideTests(unittest.TestCase):
    SRC = (AGENT / "app/main.py").read_text()

    def test_loop_exists(self):
        self.assertIn("async def refresh_mcp_credentials_loop", self.SRC)

    def test_codex_is_no_longer_excluded(self):
        """Der Kern des Fixes: die Startbedingung, mit `mode = "codex_cli"`
        ausgewertet, muss wahr sein, sobald eigene Server konfiguriert sind."""
        bedingung, _flag = _schleifenstart(self.SRC)
        for mode in ("codex_cli", "custom_llm", "claude_code"):
            with self.subTest(mode):
                self.assertTrue(eval(bedingung, {
                    "os": _umgebung(CUSTOM_MCP_SERVERS='{"srv": "https://example.invalid/mcp"}'),
                    "mode": mode,
                }))

    def test_loop_starts_whenever_there_are_custom_servers(self):
        """… und NUR dann — ohne Server gibt es nichts aufzufrischen."""
        bedingung, _flag = _schleifenstart(self.SRC)
        self.assertFalse(eval(bedingung, {"os": _umgebung(), "mode": "claude_code"}))

    def test_codex_does_not_register_via_the_claude_cli(self):
        """Codex verwaltet seine Server ueber die config.toml; ein `claude mcp add`
        waere dort wirkungslos. custom_llm liest die Umgebung direkt."""
        _bedingung, flag = _schleifenstart(self.SRC)
        self.assertFalse(eval(flag, {"mode": "codex_cli"}))
        self.assertFalse(eval(flag, {"mode": "custom_llm"}))
        self.assertTrue(eval(flag, {"mode": "claude_code"}))

    def test_env_is_updated_for_every_mode(self):
        """Das Auffrischen der Umgebung ist der Teil, von dem Codex lebt — es
        darf nicht unter `if register_via_cli:` (fuer Codex aus) rutschen."""
        for ziel, wert in (('os.environ["CUSTOM_MCP_AUTH"]', "auth"),
                           ('os.environ["CUSTOM_MCP_SERVERS"]', "servers")):
            with self.subTest(ziel):
                alle = _zuweisungen(self.SRC, "refresh_mcp_credentials_loop", ziel)
                self.assertIn(f"{ziel} = json.dumps({wert})", [anw for _, anw, _ in alle])
                for zeile, anweisung, bedingungen in alle:
                    self.assertEqual(bedingungen, [],
                                     f"Zeile {zeile}: `{anweisung}` steht unter if {bedingungen}")


class CodexReadsTheEnvTests(unittest.TestCase):
    """Der Beleg, dass die alte Begruendung nicht stimmte."""

    SRC = (AGENT / "app/codex_runner.py").read_text()

    def test_codex_reads_custom_mcp_auth(self):
        self.assertIn('env.get("CUSTOM_MCP_AUTH"', self.SRC)
        self.assertIn('env.get("CUSTOM_MCP_SERVERS"', self.SRC)

    def test_codex_env_is_a_live_copy_of_os_environ(self):
        """Deshalb wirkt eine aufgefrischte Umgebung ueberhaupt."""
        block = _funktionsrumpf(self.SRC, "_codex_env")
        self.assertIn("env = os.environ.copy()", block)

    def test_config_is_rewritten_per_invocation(self):
        """Sonst wuerde selbst eine frische Umgebung nichts aendern."""
        self.assertIn("Called once per Codex invocation", self.SRC)


class OrderingTests(unittest.TestCase):
    def test_servers_are_written_last(self):
        """#502: Jeder Leser nimmt zuerst CUSTOM_MCP_SERVERS und sucht dann die
        passenden Zugangsdaten. Neue Server vor neuen Tokens zu schreiben ergaebe
        einen 401 aus einem halb gelesenen Zustand."""
        src = (AGENT / "app/main.py").read_text()
        auth = _zuweisungen(src, "refresh_mcp_credentials_loop", 'os.environ["CUSTOM_MCP_AUTH"]')
        servers = _zuweisungen(src, "refresh_mcp_credentials_loop", 'os.environ["CUSTOM_MCP_SERVERS"]')
        # JEDE AUTH-Zuweisung vor JEDER SERVERS-Zuweisung — nicht nur die erste
        # gefundene, sonst deckt ein unbedingter Koeder die echte Reihenfolge zu.
        letzte_auth = max(zeile for zeile, _, _ in auth)
        erste_servers = min(zeile for zeile, _, _ in servers)
        self.assertLess(letzte_auth, erste_servers,
                        f"AUTH-Zeilen {[z for z, _, _ in auth]} vs. SERVERS-Zeilen {[z for z, _, _ in servers]}")


if __name__ == "__main__":
    unittest.main()
