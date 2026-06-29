"""Tests for the on-prem Exchange (EWS) MCP core.

exchangelib is NOT required to run these — the EWS layer (`_run_tool`) is the test
seam and is either inspected statically or monkeypatched. Covers:
  - catalog integrity: every tool has a handler branch; WRITE_TOOLS are real tools
  - write gating / AuthZ: read-only hides + refuses write tools (no context resolve)
  - per-user context is resolved and passed through to the tool layer
  - "not configured" path when no context
"""

import asyncio
import inspect
import unittest

from app.core import exchange_mcp
from app.core.exchange_mcp import EXCHANGE_TOOLS, WRITE_TOOLS, handle_mcp_request


def _run(coro):
    return asyncio.run(coro)


async def _ctx_ok():
    return {"mode": "service_account", "server": "mail.example.de",
            "user_email": "joe@example.de", "sa_user": "svc", "sa_password": "x"}


async def _ctx_none():
    return None


class CatalogIntegrityTests(unittest.TestCase):
    def test_no_duplicate_tool_names(self):
        names = [t["name"] for t in EXCHANGE_TOOLS]
        self.assertEqual(len(names), len(set(names)))

    def test_write_tools_are_real_tools(self):
        names = {t["name"] for t in EXCHANGE_TOOLS}
        self.assertTrue(WRITE_TOOLS.issubset(names), f"WRITE_TOOLS not in catalog: {WRITE_TOOLS - names}")

    def test_every_tool_has_a_handler_branch(self):
        # Static check: no exchangelib needed. Every tool name must appear as a
        # dispatch branch in _run_tool's source.
        src = inspect.getsource(exchange_mcp._run_tool)
        for t in EXCHANGE_TOOLS:
            self.assertIn(f'"{t["name"]}"', src, f"{t['name']} has no branch in _run_tool")

    def test_mail_and_calendar_crud_present(self):
        names = {t["name"] for t in EXCHANGE_TOOLS}
        for required in [
            "ex_whoami",
            "ex_list_emails", "ex_read_email", "ex_send_email", "ex_reply_email",
            "ex_forward_email", "ex_delete_email", "ex_move_email", "ex_mark_email_read",
            "ex_list_calendar_events", "ex_create_calendar_event",
            "ex_update_calendar_event", "ex_cancel_calendar_event",
        ]:
            self.assertIn(required, names)


class WriteGatingTests(unittest.TestCase):
    def test_readonly_list_hides_write_tools(self):
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        res, status = _run(handle_mcp_request(body, _ctx_ok, write_enabled=False))
        names = {t["name"] for t in res["result"]["tools"]}
        self.assertEqual(status, 200)
        self.assertNotIn("ex_send_email", names)
        self.assertNotIn("ex_cancel_calendar_event", names)
        self.assertIn("ex_list_emails", names)
        self.assertIn("ex_whoami", names)

    def test_write_enabled_list_shows_write_tools(self):
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        res, _ = _run(handle_mcp_request(body, _ctx_ok, write_enabled=True))
        names = {t["name"] for t in res["result"]["tools"]}
        self.assertIn("ex_send_email", names)
        self.assertIn("ex_create_calendar_event", names)

    def test_write_call_refused_without_context_resolution(self):
        resolved = {"n": 0}

        async def resolver():
            resolved["n"] += 1
            return await _ctx_ok()

        body = {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "ex_send_email", "arguments": {"to": "a@b.de", "subject": "s", "body": "b"}},
        }
        res, status = _run(handle_mcp_request(body, resolver, write_enabled=False))
        self.assertEqual(status, 200)
        self.assertTrue(res["result"]["isError"])
        self.assertEqual(resolved["n"], 0, "context resolved for a refused write tool")


class ContextDispatchTests(unittest.TestCase):
    def setUp(self):
        self.captured = {}

        def fake_run_tool(name, args, ctx, write_enabled):
            self.captured.update(name=name, args=args, ctx=ctx, write_enabled=write_enabled)
            return "OK"

        self._orig = exchange_mcp._run_tool
        exchange_mcp._run_tool = fake_run_tool

    def tearDown(self):
        exchange_mcp._run_tool = self._orig

    def test_read_tool_passes_per_user_context_through(self):
        body = {
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "ex_list_emails", "arguments": {"folder": "inbox"}},
        }
        res, status = _run(handle_mcp_request(body, _ctx_ok, write_enabled=False))
        self.assertEqual(status, 200)
        self.assertFalse(res["result"].get("isError"))
        # per-user: the agent owner's email is in the context handed to the EWS layer
        self.assertEqual(self.captured["ctx"]["user_email"], "joe@example.de")
        self.assertEqual(self.captured["name"], "ex_list_emails")

    def test_write_tool_dispatches_when_enabled(self):
        body = {
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {"name": "ex_send_email", "arguments": {"to": "a@b.de", "subject": "s", "body": "b"}},
        }
        res, _ = _run(handle_mcp_request(body, _ctx_ok, write_enabled=True))
        self.assertFalse(res["result"].get("isError"))
        self.assertEqual(self.captured["name"], "ex_send_email")
        self.assertTrue(self.captured["write_enabled"])

    def test_not_configured_returns_error(self):
        body = {
            "jsonrpc": "2.0", "id": 5, "method": "tools/call",
            "params": {"name": "ex_list_emails", "arguments": {}},
        }
        res, status = _run(handle_mcp_request(body, _ctx_none, write_enabled=False))
        self.assertEqual(status, 200)
        self.assertTrue(res["result"]["isError"])
        self.assertIn("not configured", res["result"]["content"][0]["text"].lower())


if __name__ == "__main__":
    unittest.main()
