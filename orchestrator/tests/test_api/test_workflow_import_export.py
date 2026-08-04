"""Workflow import/export (#470) — unit tests for the portable snapshot format.

DB-free by design (like the other test_api wiring tests): the pure ``_export_dict``
helper is asserted directly, and ``import_workflow`` is driven with a mocked
AsyncSession + user so no real database is needed.
"""
import unittest
from unittest.mock import AsyncMock, MagicMock

from fastapi import HTTPException

from app.api import workflows as wf_api
from app.models.workflow import Workflow


def _wf(name="Mein Workflow"):
    w = Workflow(id="wf_abc123", name=name)
    w.user_id = "u1"
    w.folder_id = "wff_x"
    w.enabled = True
    w.definition = {"start": "s1", "steps": {"s1": {"type": "agent_task", "title": "A", "prompt": "hi", "next": None}}}
    w.trigger = {"cron": "0 7 * * 1"}
    return w


def _db():
    db = MagicMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


_VALID_DEF = {"start": "s1", "steps": {"s1": {"type": "agent_task", "title": "A", "prompt": "x", "next": None}}}


class ExportTests(unittest.TestCase):
    def test_export_dict_is_portable(self):
        d = wf_api._export_dict(_wf())
        self.assertEqual(d["format"], wf_api.WORKFLOW_EXPORT_FORMAT)
        self.assertEqual(d["version"], wf_api.WORKFLOW_EXPORT_VERSION)
        self.assertEqual(d["name"], "Mein Workflow")
        self.assertEqual(d["definition"], _wf().definition)
        self.assertEqual(d["trigger"], {"cron": "0 7 * * 1"})
        self.assertIn("exported_at", d)
        # Owner/folder/run state must NOT leak into a shared snapshot.
        for leak in ("user_id", "folder_id", "id", "role"):
            self.assertNotIn(leak, d)


class ImportTests(unittest.IsolatedAsyncioTestCase):
    async def test_import_creates_disabled_owned_workflow(self):
        user = MagicMock(id="importer")
        body = wf_api.WorkflowImport(
            format=wf_api.WORKFLOW_EXPORT_FORMAT, version=1,
            name="Geteilt", definition=_VALID_DEF, trigger={"cron": "* * * * *"},
        )
        out = await wf_api.import_workflow(body, user=user, db=_db())
        self.assertEqual(out["name"], "Geteilt")
        self.assertEqual(out["user_id"], "importer")
        self.assertFalse(out["enabled"], "imported workflow must be disabled to avoid surprise cron runs")
        self.assertEqual(out["definition"], _VALID_DEF)
        self.assertEqual(out["role"], "owner")

    async def test_import_default_name_when_blank(self):
        out = await wf_api.import_workflow(
            wf_api.WorkflowImport(definition=_VALID_DEF, name="   "),
            user=MagicMock(id="u"), db=_db(),
        )
        self.assertEqual(out["name"], "Importierter Workflow")

    async def test_import_rejects_unknown_format(self):
        with self.assertRaises(HTTPException) as ctx:
            await wf_api.import_workflow(
                wf_api.WorkflowImport(format="something-else", definition=_VALID_DEF),
                user=MagicMock(id="u"), db=_db(),
            )
        self.assertEqual(ctx.exception.status_code, 400)

    async def test_import_rejects_future_version(self):
        with self.assertRaises(HTTPException) as ctx:
            await wf_api.import_workflow(
                wf_api.WorkflowImport(version=wf_api.WORKFLOW_EXPORT_VERSION + 1, definition=_VALID_DEF),
                user=MagicMock(id="u"), db=_db(),
            )
        self.assertEqual(ctx.exception.status_code, 400)

    async def test_import_rejects_invalid_definition(self):
        with self.assertRaises(HTTPException) as ctx:
            await wf_api.import_workflow(
                wf_api.WorkflowImport(definition={"start": "nope", "steps": {"s1": {"type": "agent_task"}}}),
                user=MagicMock(id="u"), db=_db(),
            )
        self.assertEqual(ctx.exception.status_code, 400)


class RouteWiringTests(unittest.TestCase):
    def _paths(self, method):
        return {
            getattr(r, "path", "")
            for r in wf_api.router.routes
            if method in (getattr(r, "methods", set()) or set())
        }

    def test_import_and_export_routes_registered(self):
        self.assertIn("/workflows/import", self._paths("POST"))
        self.assertIn("/workflows/{workflow_id}/export", self._paths("GET"))

    def test_import_requires_auth(self):
        for route in wf_api.router.routes:
            if getattr(route, "path", "") == "/workflows/import" and "POST" in (route.methods or set()):
                names = [d.call.__name__ for d in route.dependant.dependencies if getattr(d, "call", None)]
                self.assertIn("require_auth", names)
                return
        self.fail("import route not found")


if __name__ == "__main__":
    unittest.main()
