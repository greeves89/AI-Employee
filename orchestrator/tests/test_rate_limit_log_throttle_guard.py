"""Guard test for the Sammel-Issue #521 rate-limit log throttle.

Background: APIRateLimitMiddleware logged a full WARNING line for every single
rejected request once a caller was over the limit — observed 126x for one user in
one 60s window on 2026-08-05, flooding /shared/platform-errors.log. The fix adds
``_should_log(key, now)``, a per-key dedup gate (log at most once per window), and
both call sites (Redis path + in-memory fallback path) must go through it.

``_should_log`` has no external dependencies (dict + time arithmetic only), so its
real source is extracted via AST and exercised directly against a minimal stub —
this runs without fastapi/sqlalchemy/redis/docker, none of which are installed in
this container (see test_scheduler_transient_db_guard.py for the same pattern).
"""

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace

_MAIN = Path(__file__).resolve().parent.parent / "app" / "main.py"


def _module() -> ast.Module:
    return ast.parse(_MAIN.read_text())


def _find_class(mod: ast.Module, name: str) -> ast.ClassDef:
    for node in ast.walk(mod):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"class {name} not found in main.py")


def _find_method(cls: ast.ClassDef, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in cls.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"method {name} not found on {cls.name}")


def _load_should_log():
    """Compile the real ``_should_log`` source into a standalone callable."""
    cls = _find_class(_module(), "APIRateLimitMiddleware")
    method = _find_method(cls, "_should_log")
    wrapper = ast.Module(body=[method], type_ignores=[])
    ast.fix_missing_locations(wrapper)
    ns: dict = {}
    exec(compile(wrapper, str(_MAIN), "exec"), ns)
    return ns["_should_log"]


def _calls_in(node: ast.AST) -> list[ast.Call]:
    return [n for n in ast.walk(node) if isinstance(n, ast.Call)]


class RateLimitThrottleSourceTests(unittest.TestCase):
    def test_both_warning_call_sites_are_gated_by_should_log(self):
        call_meth = _find_method(_find_class(_module(), "APIRateLimitMiddleware"), "__call__")
        warning_calls = [
            n for n in ast.walk(call_meth)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "warning"
        ]
        self.assertEqual(
            len(warning_calls), 2,
            "expected exactly 2 logger.warning(...) call sites (Redis + in-memory fallback)",
        )
        for warn_call in warning_calls:
            # Walk up: find the nearest enclosing If whose test calls _should_log.
            enclosing_if = None
            for node in ast.walk(call_meth):
                if isinstance(node, ast.If) and warn_call in ast.walk(node):
                    if any(
                        isinstance(c.func, ast.Attribute) and c.func.attr == "_should_log"
                        for c in _calls_in(node.test)
                    ):
                        enclosing_if = node
                        break
            self.assertIsNotNone(
                enclosing_if,
                "each 'Rate limit exceeded' logger.warning(...) call must be gated "
                "by an `if self._should_log(...)` check",
            )


class ShouldLogBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.should_log = _load_should_log()

    def _stub(self, window=60):
        return SimpleNamespace(window=window, _last_logged={})

    def test_first_call_for_a_key_logs(self):
        self_ = self._stub()
        self.assertTrue(self.should_log(self_, "user:1", 1000.0))

    def test_second_call_within_window_is_suppressed(self):
        self_ = self._stub(window=60)
        self.assertTrue(self.should_log(self_, "user:1", 1000.0))
        self.assertFalse(self.should_log(self_, "user:1", 1010.0))

    def test_call_after_window_elapses_logs_again(self):
        self_ = self._stub(window=60)
        self.assertTrue(self.should_log(self_, "user:1", 1000.0))
        self.assertTrue(self.should_log(self_, "user:1", 1061.0))

    def test_keys_are_independent(self):
        self_ = self._stub(window=60)
        self.assertTrue(self.should_log(self_, "user:1", 1000.0))
        self.assertTrue(self.should_log(self_, "user:2", 1000.0))
        self.assertFalse(self.should_log(self_, "user:1", 1005.0))


if __name__ == "__main__":
    unittest.main()
