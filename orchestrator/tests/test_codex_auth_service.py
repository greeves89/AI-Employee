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

    def test_lock_agent_readonly_keeps_file_root_owned(self):
        # #510: the file must be owned by root (uid 0), NOT the agent uid, so a
        # rogue agent (all agents share uid 1000) cannot overwrite it in place.
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(codex_auth_service.os, "chown") as chown,
            patch.object(codex_auth_service.os, "chmod") as chmod,
        ):
            codex_auth_service._lock_agent_readonly("/shared/.codex/auth.json.tmp")

        chown.assert_called_once_with("/shared/.codex/auth.json.tmp", 0, 1000)
        chmod.assert_called_once_with("/shared/.codex/auth.json.tmp", 0o640)

    def test_lock_agent_readonly_respects_configured_gid(self):
        with (
            patch.dict(os.environ, {"AGENT_CONTAINER_GID": "5678"}, clear=True),
            patch.object(codex_auth_service.os, "chown") as chown,
            patch.object(codex_auth_service.os, "chmod") as chmod,
        ):
            codex_auth_service._lock_agent_readonly("/shared/.codex/auth.json")

        chown.assert_called_once_with("/shared/.codex/auth.json", 0, 5678)
        chmod.assert_called_once_with("/shared/.codex/auth.json", 0o640)

    def test_secure_codex_dir_root_owns_and_denies_agent_write(self):
        # The parent dir must be root-owned and non-agent-writable so an agent
        # cannot unlink the real auth.json and drop a forged one in its place.
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(codex_auth_service.os, "chown") as chown,
            patch.object(codex_auth_service.os, "chmod") as chmod,
        ):
            codex_auth_service._secure_codex_dir("/shared/.codex")

        chown.assert_called_once_with("/shared/.codex", 0, 1000)
        chmod.assert_called_once_with("/shared/.codex", 0o750)


if __name__ == "__main__":
    unittest.main()
