"""Guard test: ``_check_due_schedules`` must not access ``schedule.id`` (or any
other attribute of the ORM ``schedule`` object) inside the ``except`` handler
that follows ``await db.rollback()``.

Background: ``db.rollback()`` expires every attribute on ORM objects attached
to that session. The next access to such an attribute (e.g. in a log message)
triggers an implicit lazy-load. Under async SQLAlchemy this lazy-load needs the
greenlet bridge that only wraps explicit ``await``-ed ORM calls -- a bare
attribute access in a synchronous ``logger.warning(...)`` call is not wrapped,
so it raises ``sqlalchemy.exc.MissingGreenlet`` and masks the real exception
being logged (seen recurring in platform-errors.log, e.g. a transient Redis
timeout during ``push_task``).

Fix: capture the id (or any other needed field) into a plain local variable
*before* the try/except block, and reference the plain variable afterward.

Source-level AST guard so it runs without sqlalchemy/a live DB in the container.
"""

import ast
import unittest
from pathlib import Path

_SERVICE = (
    Path(__file__).resolve().parent.parent
    / "app" / "services" / "scheduler_service.py"
)


def _module() -> ast.Module:
    return ast.parse(_SERVICE.read_text())


def _find_method(mod: ast.Module, name: str) -> ast.AsyncFunctionDef:
    for node in ast.walk(mod):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found")


def _find_for_over_schedules(method: ast.AsyncFunctionDef) -> ast.For:
    for node in ast.walk(method):
        if isinstance(node, ast.For) and isinstance(node.target, ast.Name):
            if node.target.id == "schedule":
                return node
    raise AssertionError("for schedule in schedules loop not found")


def _attribute_accesses_on(node: ast.AST, obj_name: str) -> set[str]:
    """All `<obj_name>.<attr>` accesses anywhere under node."""
    found: set[str] = set()
    for n in ast.walk(node):
        if (
            isinstance(n, ast.Attribute)
            and isinstance(n.value, ast.Name)
            and n.value.id == obj_name
        ):
            found.add(n.attr)
    return found


class TestScheduleAttributeNotAccessedAfterRollback(unittest.TestCase):
    def test_except_handler_does_not_touch_schedule_object(self):
        """After db.rollback(), the except handler must only use plain
        local variables captured before the try block -- never `schedule.*`."""
        method = _find_method(_module(), "_check_due_schedules")
        for_node = _find_for_over_schedules(method)

        try_node = next(
            (n for n in ast.walk(for_node) if isinstance(n, ast.Try)), None
        )
        self.assertIsNotNone(try_node, "expected a try/except inside the for-loop")

        for handler in try_node.handlers:
            touched = _attribute_accesses_on(handler, "schedule")
            self.assertEqual(
                touched,
                set(),
                "except handler must not access attributes on the (possibly "
                f"rollback-expired) `schedule` ORM object directly, found: {touched}. "
                "Capture the needed value into a local variable before the try block.",
            )

    def test_schedule_id_captured_before_try_block(self):
        """A plain `schedule_id = schedule.id` (or equivalent) must exist in the
        for-loop body before the try block, so it survives a later rollback."""
        method = _find_method(_module(), "_check_due_schedules")
        for_node = _find_for_over_schedules(method)

        pre_try_stmts = []
        for stmt in for_node.body:
            if isinstance(stmt, ast.Try):
                break
            pre_try_stmts.append(stmt)

        captured = False
        for stmt in pre_try_stmts:
            if isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if isinstance(target, ast.Name):
                        if "id" in _attribute_accesses_on(stmt.value, "schedule"):
                            captured = True
        self.assertTrue(
            captured,
            "expected `schedule_id = schedule.id` (or similar) assigned before "
            "the try block, so the except handler can log it safely",
        )


if __name__ == "__main__":
    unittest.main()
