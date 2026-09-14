"""Issue #715: a secret with importance=5 must not vanish from the prompt.

``get_memory_preload`` skips items in the ``critical`` bucket whose category
looks like a credential, on the assumption they are "already listed above" in
the ``credentials`` bucket. That assumption only holds if the orchestrator's
``collect_preload`` puts the credentials bucket together correctly — before
this fix, an overlapping item (importance>=5 AND a credential category) landed
ONLY in ``critical`` and was then silently dropped by this exact skip.

This test exercises the CONSUMER side with the data shape ``collect_preload``
now produces after the fix: the credential appears only in "credentials", not
duplicated into "critical". It must render, exactly once, in the prompt block.
"""

import json
import unittest
from unittest.mock import MagicMock, patch

from app import runner_hooks
from app.config import settings

FAKE_SECRET = "sk-test-fake-12345"  # obviously-fake placeholder, not a real credential


def _fake_response(payload: dict):
    resp = MagicMock()
    resp.read.return_value = json.dumps(payload).encode("utf-8")
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


class MemoryPreloadCredentialsRegressionTest(unittest.TestCase):
    def test_importance_5_credential_is_rendered_once_not_dropped(self):
        data = {
            # Correctly categorized (post-fix shape): the credential is NOT
            # duplicated here, only in "credentials".
            "critical": [
                {"key": "user-pref", "category": "preference",
                 "content": "likes short replies", "importance": 5},
            ],
            "credentials": [
                {"key": "prod-api-key", "category": "api_key",
                 "content": FAKE_SECRET, "importance": 5},
            ],
            "recent_learnings": [],
            "task_relevant": [],
        }
        with patch.object(settings, "orchestrator_url", "http://orchestrator.invalid"), \
             patch.object(settings, "agent_id", "agent-1"), \
             patch.object(settings, "agent_token", "tok"), \
             patch("urllib.request.urlopen", return_value=_fake_response(data)):
            out = runner_hooks.get_memory_preload()

        self.assertIn(FAKE_SECRET, out)
        self.assertEqual(out.count(FAKE_SECRET), 1)
        self.assertIn("## Credentials & Keys", out)

    def test_credential_leaking_into_critical_is_not_rendered_twice(self):
        """Belt-and-braces: even if a credential-category item DID end up
        duplicated into ``critical`` (the pre-fix bucket shape), the skip
        logic in get_memory_preload must still suppress the duplicate in the
        Critical section rather than printing the secret a second time there.
        """
        data = {
            "critical": [
                {"key": "prod-api-key", "category": "api_key",
                 "content": FAKE_SECRET, "importance": 5},
            ],
            "credentials": [
                {"key": "prod-api-key", "category": "api_key",
                 "content": FAKE_SECRET, "importance": 5},
            ],
            "recent_learnings": [],
            "task_relevant": [],
        }
        with patch.object(settings, "orchestrator_url", "http://orchestrator.invalid"), \
             patch.object(settings, "agent_id", "agent-1"), \
             patch.object(settings, "agent_token", "tok"), \
             patch("urllib.request.urlopen", return_value=_fake_response(data)):
            out = runner_hooks.get_memory_preload()

        self.assertEqual(out.count(FAKE_SECRET), 1)


if __name__ == "__main__":
    unittest.main()
