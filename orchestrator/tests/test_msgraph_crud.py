"""Tests for the MS Graph MCP CRUD completeness + write-gating.

Covers the CRUD expansion (Planner/To-Do/Calendar/Mail/OneDrive/Contacts):
  - catalog integrity: every tool has a handler and vice versa
  - write tools are hidden + refused in read-only mode (AuthZ)
  - Planner PATCH/DELETE send the required If-Match ETag
  - new handlers hit the correct Graph method + path
"""

import asyncio
import unittest

from app.core import msgraph_mcp
from app.core.msgraph_mcp import (
    GraphError,
    MSGRAPH_TOOLS,
    WRITE_TOOLS,
    handle_mcp_request,
    handle_tool,
)


def _run(coro):
    return asyncio.run(coro)


class _GraphRecorder:
    """Drop-in async replacement for ``_graph`` that records every call and
    returns canned data (incl. an @odata.etag for Planner GETs)."""

    def __init__(self):
        self.calls = []

    async def __call__(self, method, path, token, **kwargs):
        self.calls.append({"method": method, "path": path, "kwargs": kwargs})
        if method == "GET" and path.startswith("/planner/tasks/"):
            return {"@odata.etag": 'W/"etag-123"'}
        if method == "GET":
            return {"value": []}
        return {"id": "NEW", "displayName": "Contact X", "name": "renamed.txt", "webUrl": "http://x"}


class CatalogIntegrityTests(unittest.TestCase):
    def test_every_tool_has_handler_and_vice_versa(self):
        names = {t["name"] for t in MSGRAPH_TOOLS}
        # No duplicate tool names
        self.assertEqual(len(names), len(MSGRAPH_TOOLS), "duplicate tool names in MSGRAPH_TOOLS")
        # Every WRITE_TOOL is an actual tool
        self.assertTrue(WRITE_TOOLS.issubset(names), f"WRITE_TOOLS not in catalog: {WRITE_TOOLS - names}")
        # Every tool dispatches to a real handler (no 'Unknown tool')
        rec = _GraphRecorder()
        orig = msgraph_mcp._graph
        msgraph_mcp._graph = rec
        args = {
            "email_id": "1", "to": "a@b.de", "folder": "archive",
            "event_id": "1", "response": "accept",
            "subject": "S", "start": "2026-07-01T09:00:00", "end": "2026-07-01T10:00:00", "body": "B",
            "list_id": "1", "task_id": "1", "plan_id": "1",
            "title": "T", "path": "x/y.txt", "name": "f",
            "content": "c", "parent_path": "", "new_name": "z",
            "contact_id": "1", "given_name": "A", "query": "q",
            "percent_complete": 100,
        }
        try:
            for name in names:
                # A returned "Unknown tool" is the only failure we care about here;
                # an exception means the handler WAS reached (arg validation is
                # covered by the dedicated shape tests), so that still proves the
                # tool name dispatches.
                try:
                    out = _run(handle_tool(name, dict(args), "tok"))
                except Exception:
                    continue
                self.assertNotIn("Unknown tool", out, f"{name} has no handler")
        finally:
            msgraph_mcp._graph = orig

    def test_full_crud_present_for_core_domains(self):
        names = {t["name"] for t in MSGRAPH_TOOLS}
        for required in [
            "ms_update_planner_task", "ms_delete_planner_task",   # the customer bug
            "ms_update_task", "ms_delete_task", "ms_complete_task",
            "ms_update_calendar_event", "ms_cancel_event", "ms_respond_event",
            "ms_delete_email", "ms_forward_email", "ms_move_email", "ms_mark_email_read",
            "ms_delete_item", "ms_move_item",
            "ms_list_contacts", "ms_create_contact", "ms_update_contact", "ms_delete_contact",
        ]:
            self.assertIn(required, names, f"missing CRUD tool: {required}")


class WriteGatingTests(unittest.TestCase):
    """Read-only agents must neither see nor be able to call write tools."""

    def test_readonly_list_hides_write_tools(self):
        async def resolver():
            return "tok"

        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        res, status = _run(handle_mcp_request(body, resolver, write_enabled=False))
        names = {t["name"] for t in res["result"]["tools"]}
        self.assertEqual(status, 200)
        self.assertNotIn("ms_update_planner_task", names)
        self.assertNotIn("ms_delete_email", names)
        # read tools still present
        self.assertIn("ms_list_planner_tasks", names)
        self.assertIn("ms_list_contacts", names)

    def test_write_enabled_list_shows_write_tools(self):
        async def resolver():
            return "tok"

        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        res, _ = _run(handle_mcp_request(body, resolver, write_enabled=True))
        names = {t["name"] for t in res["result"]["tools"]}
        self.assertIn("ms_update_planner_task", names)
        self.assertIn("ms_delete_contact", names)

    def test_write_call_refused_without_token_resolution(self):
        """A refused write must short-circuit BEFORE the token is resolved."""
        resolved = {"n": 0}

        async def resolver():
            resolved["n"] += 1
            return "tok"

        body = {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "ms_delete_planner_task", "arguments": {"task_id": "X"}},
        }
        res, status = _run(handle_mcp_request(body, resolver, write_enabled=False))
        self.assertEqual(status, 200)
        self.assertTrue(res["result"]["isError"])
        self.assertEqual(resolved["n"], 0, "token was resolved for a refused write tool")


class PlannerEtagTests(unittest.TestCase):
    def setUp(self):
        self.rec = _GraphRecorder()
        self._orig = msgraph_mcp._graph
        msgraph_mcp._graph = self.rec

    def tearDown(self):
        msgraph_mcp._graph = self._orig

    def test_update_planner_task_sends_if_match(self):
        out = _run(handle_tool("ms_update_planner_task",
                               {"task_id": "T1", "percent_complete": 100, "title": "Done"}, "tok"))
        # 1) GET etag, 2) PATCH with If-Match
        self.assertEqual(self.rec.calls[0]["method"], "GET")
        self.assertIn("/planner/tasks/T1", self.rec.calls[0]["path"])
        patch = self.rec.calls[1]
        self.assertEqual(patch["method"], "PATCH")
        self.assertEqual(patch["kwargs"]["headers"], {"If-Match": 'W/"etag-123"'})
        self.assertIn("percentComplete", patch["kwargs"]["content"])
        self.assertIn("aktualisiert", out)

    def test_delete_planner_task_sends_if_match(self):
        _run(handle_tool("ms_delete_planner_task", {"task_id": "T2"}, "tok"))
        delete = self.rec.calls[1]
        self.assertEqual(delete["method"], "DELETE")
        self.assertEqual(delete["kwargs"]["headers"], {"If-Match": 'W/"etag-123"'})


class NewHandlerShapeTests(unittest.TestCase):
    def setUp(self):
        self.rec = _GraphRecorder()
        self._orig = msgraph_mcp._graph
        msgraph_mcp._graph = self.rec

    def tearDown(self):
        msgraph_mcp._graph = self._orig

    def test_delete_email(self):
        _run(handle_tool("ms_delete_email", {"email_id": "M1"}, "tok"))
        self.assertEqual(self.rec.calls[0]["method"], "DELETE")
        self.assertIn("/me/messages/M1", self.rec.calls[0]["path"])

    def test_delete_item_uses_path_addressing(self):
        _run(handle_tool("ms_delete_item", {"path": "Projekte/alt.txt"}, "tok"))
        call = self.rec.calls[0]
        self.assertEqual(call["method"], "DELETE")
        self.assertIn("/me/drive/root:/Projekte/alt.txt", call["path"])

    def test_create_contact_posts_to_me_contacts(self):
        out = _run(handle_tool("ms_create_contact",
                               {"given_name": "Max", "email": "max@firma.de"}, "tok"))
        call = self.rec.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertEqual(call["path"], "/me/contacts")
        self.assertIn("givenName", call["kwargs"]["content"])
        self.assertIn("erstellt", out)

    def test_update_task_no_fields_is_guarded(self):
        out = _run(handle_tool("ms_update_task", {"list_id": "L", "task_id": "T"}, "tok"))
        self.assertIn("Error", out)
        self.assertEqual(self.rec.calls, [])  # no Graph call when nothing to update


class MailSendSemanticsTests(unittest.TestCase):
    """In write mode outbound mail is sent for REAL (no draft detour)."""

    def setUp(self):
        self.rec = _GraphRecorder()
        self._orig = msgraph_mcp._graph
        msgraph_mcp._graph = self.rec

    def tearDown(self):
        msgraph_mcp._graph = self._orig

    def test_send_email_really_sends(self):
        out = _run(handle_tool("ms_send_email",
                               {"to": "a@b.de", "subject": "S", "body": "B"}, "tok"))
        call = self.rec.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertEqual(call["path"], "/me/sendMail")  # NOT /me/messages (draft)
        self.assertIn("sent", out.lower())

    def test_reply_email_really_sends_with_body(self):
        out = _run(handle_tool("ms_reply_email",
                               {"email_id": "M1", "body": "Danke!"}, "tok"))
        call = self.rec.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertIn("/me/messages/M1/reply", call["path"])
        self.assertIn("Danke!", call["kwargs"]["content"])  # body must survive
        self.assertIn("sent", out.lower())

    def test_reply_all_hits_replyAll(self):
        _run(handle_tool("ms_reply_email",
                         {"email_id": "M1", "body": "x", "reply_all": True}, "tok"))
        self.assertIn("/me/messages/M1/replyAll", self.rec.calls[0]["path"])

    # --- per-call draft option (the USER decides send-vs-draft individually) ---
    def test_send_draft_creates_draft_not_sent(self):
        out = _run(handle_tool("ms_send_email",
                               {"to": "a@b.de", "subject": "S", "body": "B", "draft": True}, "tok"))
        call = self.rec.calls[0]
        self.assertEqual(call["path"], "/me/messages")  # draft, NOT /me/sendMail
        self.assertIn("NICHT gesendet", out)

    def test_reply_draft_keeps_body(self):
        _run(handle_tool("ms_reply_email",
                         {"email_id": "M1", "body": "Danke!", "draft": True}, "tok"))
        call = self.rec.calls[0]
        self.assertIn("/me/messages/M1/createReply", call["path"])
        self.assertIn("Danke!", call["kwargs"]["content"])  # body must survive into the draft

    def test_reply_all_draft_uses_createReplyAll(self):
        _run(handle_tool("ms_reply_email",
                         {"email_id": "M1", "body": "x", "reply_all": True, "draft": True}, "tok"))
        self.assertIn("/me/messages/M1/createReplyAll", self.rec.calls[0]["path"])

    def test_forward_draft_uses_createForward(self):
        out = _run(handle_tool("ms_forward_email",
                               {"email_id": "M1", "to": "c@d.de", "draft": True}, "tok"))
        self.assertIn("/me/messages/M1/createForward", self.rec.calls[0]["path"])
        self.assertIn("NICHT gesendet", out)


class RobustnessTests(unittest.TestCase):
    """Central arg validation + status/error handling fixes."""

    def setUp(self):
        self.rec = _GraphRecorder()
        self._orig = msgraph_mcp._graph
        msgraph_mcp._graph = self.rec

    def tearDown(self):
        msgraph_mcp._graph = self._orig

    def test_missing_required_arg_short_circuits_before_token(self):
        resolved = {"n": 0}

        async def resolver():
            resolved["n"] += 1
            return "tok"

        body = {
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "ms_send_email", "arguments": {"subject": "S", "body": "B"}},
        }  # 'to' missing
        res, status = _run(handle_mcp_request(body, resolver, write_enabled=True))
        self.assertEqual(status, 200)
        self.assertTrue(res["result"]["isError"])
        self.assertIn("missing required", res["result"]["content"][0]["text"].lower())
        self.assertEqual(resolved["n"], 0, "token resolved despite missing arg")
        self.assertEqual(self.rec.calls, [])

    def test_update_task_invalid_status_rejected(self):
        out = _run(handle_tool("ms_update_task",
                               {"list_id": "L", "task_id": "T", "status": "Completed"}, "tok"))
        self.assertIn("Error", out)
        self.assertEqual(self.rec.calls, [])  # no PATCH with a bogus status


class CancelEventFallbackTests(unittest.TestCase):
    """Organizer-only /cancel: fall back to DELETE ONLY on 400/403, never on
    transient (429/5xx) errors — those must surface."""

    def _install(self, fail_status):
        calls = []

        async def fake(method, path, token, **kwargs):
            calls.append((method, path))
            if path.endswith("/cancel"):
                raise GraphError(fail_status, "boom")
            return {}

        self._orig = msgraph_mcp._graph
        msgraph_mcp._graph = fake
        return calls

    def tearDown(self):
        if hasattr(self, "_orig"):
            msgraph_mcp._graph = self._orig

    def test_not_organizer_403_falls_back_to_delete(self):
        calls = self._install(403)
        out = _run(handle_tool("ms_cancel_event", {"event_id": "E1"}, "tok"))
        self.assertIn("entfernt", out)
        self.assertTrue(any(m == "DELETE" for m, _ in calls))

    def test_transient_5xx_is_not_silently_deleted(self):
        calls = self._install(503)
        with self.assertRaises(GraphError):
            _run(handle_tool("ms_cancel_event", {"event_id": "E1"}, "tok"))
        self.assertFalse(any(m == "DELETE" for m, _ in calls), "deleted on a transient error")


if __name__ == "__main__":
    unittest.main()
