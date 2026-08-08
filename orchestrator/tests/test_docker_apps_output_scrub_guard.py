"""Guard test for the Sammel-Issue #521 docker_apps.py log-hardening item.

Background: ``_start_core`` and ``_stop_core`` logged the raw ``compose`` command
output on failure without scrubbing it, while ``project_name`` right next to it was
already passed through ``scrub_log()``. Compose output can carry build-arg secrets
or env dumps (see the #513/#520 log-injection review). Both call sites must wrap
``output`` in ``scrub_log(...)`` before it reaches ``logger.*``.

AST-based so it runs without docker/yaml/fastapi installed in this container (same
pattern as test_scheduler_transient_db_guard.py).
"""

import ast
import unittest
from pathlib import Path

_MODULE = Path(__file__).resolve().parent.parent / "app" / "api" / "docker_apps.py"

_GUARDED_FUNCS = ("_start_core", "_stop_core")


def _module() -> ast.Module:
    return ast.parse(_MODULE.read_text())


def _find_func(mod: ast.Module, name: str) -> ast.AsyncFunctionDef:
    for node in ast.walk(mod):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in docker_apps.py")


def _logger_calls(node: ast.AST) -> list[ast.Call]:
    return [
        n for n in ast.walk(node)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and isinstance(n.func.value, ast.Name)
        and n.func.value.id == "logger"
    ]


def _fstring_calls_scrub_log_on(call: ast.Call, var_name: str) -> bool:
    """True if any f-string argument of ``call`` wraps ``var_name`` in scrub_log(...)."""
    for arg in call.args:
        if not isinstance(arg, ast.JoinedStr):
            continue
        for value in arg.values:
            if not isinstance(value, ast.FormattedValue):
                continue
            expr = value.value
            if (
                isinstance(expr, ast.Call)
                and isinstance(expr.func, ast.Name)
                and expr.func.id == "scrub_log"
                and len(expr.args) == 1
                and isinstance(expr.args[0], ast.Name)
                and expr.args[0].id == var_name
            ):
                return True
    return False


class DockerAppsOutputScrubGuardTests(unittest.TestCase):
    def test_output_is_scrubbed_in_failure_logs(self):
        mod = _module()
        for func_name in _GUARDED_FUNCS:
            func = _find_func(mod, func_name)
            calls = [
                c for c in _logger_calls(func)
                if c.func.attr in ("error", "warning")
            ]
            self.assertTrue(
                calls, f"{func_name} should have an error/warning log on compose failure"
            )
            scrubbed = any(_fstring_calls_scrub_log_on(c, "output") for c in calls)
            self.assertTrue(
                scrubbed,
                f"{func_name}: the failure logger.error/warning(...) call must wrap "
                f"`output` in scrub_log(output) — raw compose output can contain "
                f"build-arg secrets or env dumps",
            )


if __name__ == "__main__":
    unittest.main()
