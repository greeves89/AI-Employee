"""Tests for Codex CLI auth materialization."""

import os
import unittest
from unittest.mock import patch

from app.services import codex_auth_service


class CodexAuthServiceTests(unittest.TestCase):
    def test_agent_file_owner_defaults_to_agent_uid(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(codex_auth_service._agent_file_owner(), (1000, 1000))

    def test_agent_file_owner_uses_configured_uid_gid(self):
        with patch.dict(
            os.environ,
            {"AGENT_CONTAINER_UID": "1234", "AGENT_CONTAINER_GID": "5678"},
            clear=True,
        ):
            self.assertEqual(codex_auth_service._agent_file_owner(), (1234, 5678))

    def test_agent_file_owner_ignores_invalid_env_values(self):
        with patch.dict(
            os.environ,
            {"AGENT_CONTAINER_UID": "nope", "AGENT_CONTAINER_GID": "5678"},
            clear=True,
        ):
            self.assertEqual(codex_auth_service._agent_file_owner(), (1000, 1000))

    def test_make_agent_readable_chowns_before_locking_permissions(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(codex_auth_service.os, "chown") as chown,
            patch.object(codex_auth_service.os, "chmod") as chmod,
        ):
            codex_auth_service._make_agent_readable("/shared/.codex/auth.json.tmp")

        chown.assert_called_once_with("/shared/.codex/auth.json.tmp", 1000, 1000)
        chmod.assert_called_once_with("/shared/.codex/auth.json.tmp", 0o600)


if __name__ == "__main__":
    unittest.main()
