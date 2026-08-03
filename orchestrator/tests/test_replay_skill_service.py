"""Tests for Replay-Modus skill authoring (recording -> SKILL.md)."""

import json
import unittest
from unittest.mock import AsyncMock, patch

from app.services.replay_skill_service import (
    MAX_SCREENSHOTS,
    ReplaySkillError,
    _parse_authoring_response,
    _select_screenshot_indices,
    author_skill_from_steps,
    build_authoring_prompt,
)


def _step(action="click", params=None, shot="AAAA"):
    return {"action": action, "params": params or {}, "screenshot_b64": shot}


class ScreenshotSelectionTests(unittest.TestCase):
    def test_short_recording_keeps_every_screenshot(self):
        self.assertEqual(_select_screenshot_indices(3), {0, 1, 2})

    def test_long_recording_is_capped(self):
        picked = _select_screenshot_indices(50)
        self.assertEqual(len(picked), MAX_SCREENSHOTS)

    def test_long_recording_keeps_first_and_last(self):
        picked = _select_screenshot_indices(50)
        self.assertIn(0, picked)
        self.assertIn(49, picked)


class BuildPromptTests(unittest.TestCase):
    def test_includes_one_image_block_per_kept_screenshot(self):
        blocks = build_authoring_prompt([_step(), _step(), _step()])
        images = [b for b in blocks if b["type"] == "image"]
        self.assertEqual(len(images), 3)

    def test_caps_images_on_long_recordings(self):
        blocks = build_authoring_prompt([_step() for _ in range(40)])
        images = [b for b in blocks if b["type"] == "image"]
        self.assertEqual(len(images), MAX_SCREENSHOTS)

    def test_steps_without_screenshot_contribute_text_only(self):
        blocks = build_authoring_prompt([_step(shot=None)])
        self.assertEqual([b for b in blocks if b["type"] == "image"], [])

    def test_prompt_demands_semantic_targets_not_coordinates(self):
        text = " ".join(b.get("text", "") for b in build_authoring_prompt([_step()]))
        self.assertIn("SEMANTISCH", text)
        self.assertIn("Parameter", text)

    def test_goal_hint_is_passed_through(self):
        text = " ".join(b.get("text", "") for b in build_authoring_prompt([_step()], "Rechnungen ablegen"))
        self.assertIn("Rechnungen ablegen", text)


class ParseResponseTests(unittest.TestCase):
    def test_parses_plain_json(self):
        data = _parse_authoring_response(json.dumps({"name": "x", "description": "y", "content": "z"}))
        self.assertEqual(data["content"], "z")

    def test_parses_fenced_json(self):
        raw = '```json\n{"name": "x", "description": "y", "content": "z"}\n```'
        self.assertEqual(_parse_authoring_response(raw)["content"], "z")

    def test_rejects_non_json(self):
        with self.assertRaises(ReplaySkillError):
            _parse_authoring_response("sorry, I cannot do that")

    def test_rejects_json_without_content(self):
        with self.assertRaises(ReplaySkillError):
            _parse_authoring_response('{"name": "x"}')


class AuthorSkillTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_recording_is_rejected(self):
        with self.assertRaises(ReplaySkillError):
            await author_skill_from_steps([])

    async def test_missing_api_key_is_reported_clearly(self):
        with patch("app.services.replay_skill_service.settings") as s:
            s.anthropic_api_key = ""
            with self.assertRaises(ReplaySkillError) as ctx:
                await author_skill_from_steps([_step()])
        self.assertIn("API key", str(ctx.exception))


class UniqueNameTests(unittest.IsolatedAsyncioTestCase):
    async def test_appends_suffix_when_name_is_taken(self):
        from app.services.replay_skill_service import _unique_skill_name

        seen: list[str] = []

        async def fake_execute(stmt):
            class R:
                def scalar_one_or_none(self_inner):
                    # First lookup finds a collision, second one is free.
                    return object() if len(seen) == 1 else None
            seen.append("call")
            return R()

        db = AsyncMock()
        db.execute = fake_execute
        name = await _unique_skill_name(db, "beleg-ablegen")
        self.assertEqual(name, "beleg-ablegen-2")


if __name__ == "__main__":
    unittest.main()
