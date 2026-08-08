"""Ticketsystem-Anschluss (Matrix42) mit Harness-Paritaet.

Die offene Frage in der Roadmap war: eigenen Anschluss bauen oder ueber n8n gehen. Die
n8n-Bruecke waere ein zweites System fuer etwas, das die Plattform selbst kann — mit
eigener Konfiguration, eigenen Zugangsdaten und einer zweiten Stelle, an der etwas
kaputtgehen kann.

Nicht auf Matrix42 festgenagelt: Ticketsysteme unterscheiden sich fast nur in Pfaden
und Feldnamen, beides steht in einem Profil statt im Code.
"""

import unittest
from pathlib import Path

from app.core import ticket_connector as tc

REPO = Path(__file__).resolve().parents[2]
ORCH = REPO / "orchestrator"
AGENT = REPO / "agent"


class ProfileTests(unittest.TestCase):
    def test_matrix42_and_a_generic_fallback(self):
        self.assertIn("matrix42", tc.PROFILES)
        self.assertIn("generic", tc.PROFILES)

    def test_every_profile_maps_the_same_fields(self):
        """Ohne einheitliche Felder muesste jeder Aufrufer das System kennen."""
        required = {"id", "title", "description", "status"}
        for name, profile in tc.PROFILES.items():
            with self.subTest(profile=name):
                self.assertTrue(required.issubset(profile.fields.keys()))

    def test_paths_carry_the_id_placeholder(self):
        for name, profile in tc.PROFILES.items():
            with self.subTest(profile=name):
                self.assertIn("{id}", profile.detail_path)
                self.assertIn("{id}", profile.comment_path)

    def test_unknown_profile_is_none(self):
        self.assertIsNone(tc.get_profile("jira-irgendwas"))
        self.assertIsNone(tc.get_profile(""))


class NormalizeTests(unittest.TestCase):
    PROFILE = tc.PROFILES["matrix42"]

    def test_vendor_fields_become_ours(self):
        raw = {"ObjectID": "T-1", "Subject": "Drucker kaputt", "StateName": "Offen"}
        out = tc.normalize(raw, self.PROFILE)
        self.assertEqual(out["id"], "T-1")
        self.assertEqual(out["title"], "Drucker kaputt")
        self.assertEqual(out["status"], "Offen")

    def test_missing_fields_become_empty_strings(self):
        """None wuerde im Prompt als 'None' auftauchen."""
        out = tc.normalize({"ObjectID": "T-2"}, self.PROFILE)
        self.assertEqual(out["title"], "")
        self.assertNotIn(None, out.values())

    def test_payload_uses_vendor_names(self):
        payload = tc.build_payload(self.PROFILE, title="Neu", description="Text")
        self.assertIn("Subject", payload)
        self.assertIn("Description", payload)
        self.assertNotIn("title", payload)

    def test_priority_is_optional(self):
        self.assertNotIn("PriorityName", tc.build_payload(self.PROFILE, title="X", description=""))
        self.assertIn("PriorityName",
                      tc.build_payload(self.PROFILE, title="X", description="", priority="Hoch"))


class SafetyTests(unittest.TestCase):
    def test_no_close_or_delete(self):
        """Ein Agent, der ein Ticket eigenmaechtig schliesst, erzeugt genau den
        Aerger, den die Automatisierung sparen soll."""
        src = (ORCH / "app/core/ticket_connector.py").read_text()
        for forbidden in ("async def close_ticket", "async def delete_ticket"):
            with self.subTest(method=forbidden):
                self.assertNotIn(forbidden, src)

    def test_api_offers_no_delete_route(self):
        src = (ORCH / "app/api/tickets.py").read_text()
        self.assertNotIn("@router.delete", src)

    def test_endpoints_require_an_agent_token(self):
        src = (ORCH / "app/api/tickets.py").read_text()
        self.assertEqual(src.count("Depends(verify_agent_token)"), 4)

    def test_token_is_a_secret(self):
        from app.services.settings_service import SECRET_KEYS
        self.assertIn("ticket_api_token", SECRET_KEYS)

    def test_agent_never_sees_the_credentials(self):
        """Der Agent geht ueber den Orchestrator; Adresse und Token liegen zentral."""
        src = (AGENT / "app/tools/api_client.py").read_text()
        block = src.split("async def tickets")[1].split("async def browser")[0]
        self.assertIn("/tickets/", block)
        self.assertNotIn("ticket_api_token", block)


class HarnessParityTests(unittest.TestCase):
    """Eine Faehigkeit gilt erst als vorhanden, wenn sie in allen Laufzeiten ist."""

    def test_codex_and_custom_llm(self):
        self.assertIn('"name": "tickets"', (AGENT / "app/tools/definitions.py").read_text())
        self.assertIn("async def tickets", (AGENT / "app/tools/api_client.py").read_text())

    def test_claude_code(self):
        src = (AGENT / "mcp/orchestrator-server.mjs").read_text()
        self.assertIn('name: "tickets"', src)
        self.assertIn('case "tickets"', src)

    def test_same_actions_everywhere(self):
        defs = (AGENT / "app/tools/definitions.py").read_text()
        mjs = (AGENT / "mcp/orchestrator-server.mjs").read_text()
        for action in ("list", "get", "create", "comment"):
            with self.subTest(action=action):
                self.assertIn(action, defs.split('"name": "tickets"')[1][:1500])
                self.assertIn(f'"{action}"', mjs.split('case "tickets"')[1][:3000])


class SettingsPathTests(unittest.TestCase):
    FIELDS = ("ticket_base_url", "ticket_api_token", "ticket_profile")

    def test_allowed_keys(self):
        src = (ORCH / "app/services/settings_service.py").read_text()
        for f in self.FIELDS:
            with self.subTest(field=f):
                self.assertIn(f'"{f}"', src)

    def test_request_schema(self):
        src = (ORCH / "app/schemas/settings.py").read_text()
        for f in self.FIELDS:
            with self.subTest(field=f):
                self.assertIn(f"{f}:", src)

    def test_patch_mapping(self):
        src = (ORCH / "app/api/settings.py").read_text()
        for f in self.FIELDS:
            with self.subTest(field=f):
                self.assertIn(f'"{f}"', src)


if __name__ == "__main__":
    unittest.main()
