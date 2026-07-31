"""Guard test: the two bare DB-touching sub-ticks in ``SchedulerService.run``
(``_check_due_schedules`` and ``_gc_expired_tasks``) must catch transient
connect-level DB errors as a clean WARNING instead of letting them fall through
to the outer loop handler as a full-traceback ERROR.

Background: resilient_session already retries connect blips; when its retries are
exhausted during a real DB restart it re-raises (e.g. TimeoutError/OperationalError).
Every other sub-tick in run() wraps its own await in try/except -> WARNING, but these
two were unwrapped, producing recurring alarming "[Scheduler] ERROR" tracebacks for
transient, self-healing conditions (seen ~25x in platform-errors.log).

Source-level AST guard so it runs without sqlalchemy/a live DB in the container.
"""

import ast
import unittest
from pathlib import Path

_SERVICE = (
    Path(__file__).resolve().parent.parent
    / "app" / "services" / "scheduler_service.py"
)

# Sub-tick calls that touch the DB directly and must be guarded against transient
# connect errors in run().
_GUARDED_CALLS = ("_check_due_schedules", "_gc_expired_tasks")


def _module() -> ast.Module:
    return ast.parse(_SERVICE.read_text())


def _find_run(mod: ast.Module) -> ast.AsyncFunctionDef:
    for node in ast.walk(mod):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "run":
            return node
    raise AssertionError("SchedulerService.run not found")


def _calls_in(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
            names.add(n.func.attr)
    return names


def _handler_names(handler: ast.ExceptHandler) -> set[str]:
    names: set[str] = set()
    if handler.type is not None:
        for n in ast.walk(handler.type):
            if isinstance(n, ast.Name):
                names.add(n.id)
    return names


class TestSchedulerTransientDbGuard(unittest.TestCase):
    def test_transient_db_errors_constant_defined(self):
        mod = _module()
        found = False
        for node in ast.walk(mod):
            if isinstance(node, ast.Assign):
                targets = {t.id for t in node.targets if isinstance(t, ast.Name)}
                if "_TRANSIENT_DB_ERRORS" in targets:
                    found = True
                    members = {
                        n.id for n in ast.walk(node.value) if isinstance(n, ast.Name)
                    }
                    self.assertIn(
                        "OperationalError", members,
                        "_TRANSIENT_DB_ERRORS must include OperationalError",
                    )
        self.assertTrue(found, "_TRANSIENT_DB_ERRORS constant not defined")

    def test_bare_db_subticks_are_guarded(self):
        run = _find_run(_module())
        for call in _GUARDED_CALLS:
            guarded = False
            for try_node in (n for n in ast.walk(run) if isinstance(n, ast.Try)):
                if call not in _calls_in_body(try_node):
                    continue
                for handler in try_node.handlers:
                    if "_TRANSIENT_DB_ERRORS" in _handler_names(handler):
                        guarded = True
                        break
                if guarded:
                    break
            self.assertTrue(
                guarded,
                f"{call}() in run() must be wrapped in a try/except "
                f"_TRANSIENT_DB_ERRORS handler so a transient DB blip logs a "
                f"WARNING instead of a full-traceback ERROR",
            )


def _calls_in_body(try_node: ast.Try) -> set[str]:
    names: set[str] = set()
    for stmt in try_node.body:
        names |= _calls_in(stmt)
    return names


if __name__ == "__main__":
    unittest.main()
