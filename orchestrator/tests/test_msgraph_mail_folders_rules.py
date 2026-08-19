"""Mail-Ordner + Posteingangsregeln über MS Graph (nativ statt Bridge-Klicks).

Vorher scheiterte „Jira-Mails in den Ordner JIRA sortieren": ms_move_email
konnte NUR in feste Standardordner (inbox/sent/…), ein eigener Ordner fiel
still auf „inbox" zurück; es gab kein Tool, Mail-Ordner aufzulisten/anzulegen
oder eine Regel zu erstellen. Diese Tools schliessen die Lücke — mit dem schon
vorhandenen Mail.ReadWrite-Scope, ohne neue Zustimmung.
"""

import asyncio
import json
import unittest

from app.core import msgraph_mcp
from app.core.msgraph_mcp import MSGRAPH_TOOLS, WRITE_TOOLS, handle_tool


def _run(coro):
    return asyncio.run(coro)


class _Recorder:
    def __init__(self, get_value=None, created_id="NEW"):
        self.calls = []
        self._get_value = get_value if get_value is not None else []
        self._created_id = created_id

    async def __call__(self, method, path, token, **kwargs):
        self.calls.append({"method": method, "path": path, "kwargs": kwargs})
        if method == "GET":
            return {"value": self._get_value}
        return {"id": self._created_id}


class MailFolderRuleTests(unittest.TestCase):
    def setUp(self):
        self._orig = msgraph_mcp._graph

    def tearDown(self):
        msgraph_mcp._graph = self._orig

    def _body(self, call):
        return json.loads(call["kwargs"]["content"])

    # ── Registrierung / Gating ──────────────────────────────────────────────
    def test_tools_registered_and_write_gated(self):
        names = {t["name"] for t in MSGRAPH_TOOLS}
        for t in ("ms_list_mail_folders", "ms_create_mail_folder",
                  "ms_list_mail_rules", "ms_create_mail_rule"):
            self.assertIn(t, names)
        # Lesen = kein Write; Anlegen = Write.
        self.assertNotIn("ms_list_mail_folders", WRITE_TOOLS)
        self.assertNotIn("ms_list_mail_rules", WRITE_TOOLS)
        self.assertIn("ms_create_mail_folder", WRITE_TOOLS)
        self.assertIn("ms_create_mail_rule", WRITE_TOOLS)

    # ── move_email in Custom-Ordner ─────────────────────────────────────────
    def test_move_email_into_custom_folder_id(self):
        rec = _Recorder()
        msgraph_mcp._graph = rec
        _run(handle_tool("ms_move_email", {"email_id": "M1", "folder": "AAMk-JIRA-ID"}, "tok"))
        move = rec.calls[-1]
        self.assertEqual(move["method"], "POST")
        self.assertIn("/me/messages/M1/move", move["path"])
        # Custom-ID muss unveraendert als destinationId ankommen (nicht 'inbox').
        self.assertEqual(self._body(move)["destinationId"], "AAMk-JIRA-ID")

    def test_move_email_wellknown_folder_still_works(self):
        rec = _Recorder()
        msgraph_mcp._graph = rec
        _run(handle_tool("ms_move_email", {"email_id": "M1", "folder": "Archive"}, "tok"))
        self.assertEqual(self._body(rec.calls[-1])["destinationId"], "archive")

    # ── Ordner anlegen ──────────────────────────────────────────────────────
    def test_create_top_level_folder(self):
        rec = _Recorder(created_id="F-NEW")
        msgraph_mcp._graph = rec
        out = _run(handle_tool("ms_create_mail_folder", {"name": "Jira"}, "tok"))
        call = rec.calls[-1]
        self.assertEqual(call["path"], "/me/mailFolders")
        self.assertEqual(self._body(call)["displayName"], "Jira")
        self.assertIn("F-NEW", out)

    def test_create_subfolder_under_inbox(self):
        rec = _Recorder()
        msgraph_mcp._graph = rec
        _run(handle_tool("ms_create_mail_folder", {"name": "Jira", "parent_id": "inbox"}, "tok"))
        self.assertIn("/childFolders", rec.calls[-1]["path"])

    # ── Ordner auflisten ────────────────────────────────────────────────────
    def test_list_folders_expands_children(self):
        rec = _Recorder(get_value=[{"id": "IB", "displayName": "Posteingang",
                                    "unreadItemCount": 2, "totalItemCount": 10,
                                    "childFolders": [{"id": "JR", "displayName": "Jira",
                                                      "unreadItemCount": 0, "totalItemCount": 0}]}])
        msgraph_mcp._graph = rec
        out = _run(handle_tool("ms_list_mail_folders", {}, "tok"))
        self.assertIn("Jira", out)
        self.assertIn("JR", out)  # die ID muss dabei sein (fürs Verschieben)
        self.assertIn("$expand", str(rec.calls[0]["kwargs"]["params"]))

    # ── Regel anlegen ───────────────────────────────────────────────────────
    def test_create_rule_subject_moves_to_folder(self):
        rec = _Recorder(created_id="R1")
        msgraph_mcp._graph = rec
        out = _run(handle_tool("ms_create_mail_rule", {
            "name": "Jira sortieren", "subject_contains": "Jira",
            "move_to_folder_id": "JR", "mark_as_read": True,
        }, "tok"))
        post = rec.calls[-1]
        self.assertEqual(post["path"], "/me/mailFolders/inbox/messageRules")
        body = self._body(post)
        self.assertEqual(body["conditions"]["subjectContains"], ["Jira"])
        self.assertEqual(body["actions"]["moveToFolder"], "JR")
        self.assertTrue(body["actions"]["markAsRead"])
        self.assertIn("R1", out)

    def test_create_rule_requires_a_condition(self):
        rec = _Recorder()
        msgraph_mcp._graph = rec
        out = _run(handle_tool("ms_create_mail_rule", {
            "name": "leer", "move_to_folder_id": "JR",
        }, "tok"))
        self.assertIn("Bedingung", out)
        # Ohne Bedingung darf KEINE Regel erzeugt werden.
        self.assertFalse(any(c["method"] == "POST" for c in rec.calls))


if __name__ == "__main__":
    unittest.main()
