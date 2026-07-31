"""Guard test for issue #377: ClaudeTokenService.write_initial_token() must
follow the SAME token priority order as refresh_access_token() — DB first —
instead of jumping straight from the Keychain file to the ``.env`` value.

Background: write_initial_token() runs at orchestrator startup and writes the
result to the persistent shared volume. The old implementation skipped priority 1
(the DB) and fell back to settings.claude_code_oauth_token (env), so a stale
``.env`` token could overwrite an already-valid /shared/.auth/token.json.

Also guards the sanity check in _write_shared_token(): it must not clobber an
existing token file with an obviously unusable (short placeholder) value.

Source-level AST guards so they run without sqlalchemy / a live DB in the
agent container (full module-import tests only run in CI).
"""

import ast
import unittest
from pathlib import Path

_SERVICE = (
    Path(__file__).resolve().parent.parent
    / "app" / "services" / "claude_token_service.py"
)


def _module() -> ast.Module:
    return ast.parse(_SERVICE.read_text())


def _find_func(mod: ast.Module, name: str):
    for node in ast.walk(mod):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} not found")


def _calls_in(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
            names.add(n.func.attr)
    return names


class TestClaudeTokenInitialPriority(unittest.TestCase):
    def test_write_initial_token_is_async(self):
        fn = _find_func(_module(), "write_initial_token")
        self.assertIsInstance(
            fn, ast.AsyncFunctionDef,
            "write_initial_token must be async so it can consult the DB (_get_db_token)",
        )

    def test_write_initial_token_consults_db_first(self):
        fn = _find_func(_module(), "write_initial_token")
        calls = _calls_in(fn)
        self.assertIn(
            "_get_db_token", calls,
            "write_initial_token must call _get_db_token() (priority 1) — issue #377",
        )
        self.assertIn(
            "_read_token_data", calls,
            "write_initial_token must still consult the Keychain file (priority 2)",
        )

    def test_call_sites_await_write_initial_token(self):
        main = (
            Path(__file__).resolve().parent.parent / "app" / "main.py"
        ).read_text()
        mod = ast.parse(main)
        awaited = 0
        called = 0
        for node in ast.walk(mod):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr == "write_initial_token":
                called += 1
        for node in ast.walk(mod):
            if isinstance(node, ast.Await):
                for c in ast.walk(node.value if isinstance(node.value, ast.AST) else node):
                    if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) \
                            and c.func.attr == "write_initial_token":
                        awaited += 1
                        break
        self.assertGreater(called, 0, "expected write_initial_token call sites in main.py")
        self.assertEqual(
            awaited, called,
            "every write_initial_token() call site must be awaited now that it is async",
        )

    def test_write_shared_token_guards_placeholder(self):
        fn = _find_func(_module(), "_write_shared_token")
        called_names = {
            n.func.id
            for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        self.assertIn(
            "_is_plausible_token", called_names,
            "_write_shared_token must sanity-check the value via _is_plausible_token "
            "before overwriting an existing shared token file — issue #377",
        )
        # The guard must reference the existing file so it only refuses to CLOBBER
        # a known-good file (not block the very first write).
        src = ast.get_source_segment(_SERVICE.read_text(), fn) or ""
        self.assertIn(
            "SHARED_TOKEN_PATH", src,
            "the placeholder guard must key off the existing SHARED_TOKEN_PATH file",
        )

    def test_min_plausible_token_len_defined(self):
        mod = _module()
        found = False
        for node in ast.walk(mod):
            if isinstance(node, ast.Assign):
                targets = {t.id for t in node.targets if isinstance(t, ast.Name)}
                if "_MIN_PLAUSIBLE_TOKEN_LEN" in targets:
                    found = True
        self.assertTrue(found, "_MIN_PLAUSIBLE_TOKEN_LEN constant must be defined")


if __name__ == "__main__":
    unittest.main()
