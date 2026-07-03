"""Tests for _parse_reflection_stdout — the claude-CLI reflection output parser (#272).

Guards the paths that previously raised the generic "Expecting value: line 1 column 1"
and masked the real auth/quota failure in the platform log.
"""

import json
import unittest

from app.core.task_router import _build_reflection_prompt, _parse_reflection_stdout


def _envelope(result, is_error=False, subtype="success"):
    return json.dumps(
        {"type": "result", "subtype": subtype, "is_error": is_error, "result": result}
    ).encode()


class ParseReflectionStdoutTests(unittest.TestCase):
    def test_clean_json_result(self):
        out = _envelope('{"rating": 4, "reflection": "Solid run."}')
        self.assertEqual(_parse_reflection_stdout(out, b"", 0), (4, "Solid run."))

    def test_rating_is_clamped(self):
        out = _envelope('{"rating": 9, "reflection": "x"}')
        self.assertEqual(_parse_reflection_stdout(out, b"", 0)[0], 5)
        out = _envelope('{"rating": -3, "reflection": "x"}')
        self.assertEqual(_parse_reflection_stdout(out, b"", 0)[0], 1)

    def test_markdown_fenced_json(self):
        out = _envelope('```json\n{"rating": 3, "reflection": "Ok."}\n```')
        self.assertEqual(_parse_reflection_stdout(out, b"", 0), (3, "Ok."))

    def test_json_wrapped_in_prose(self):
        out = _envelope('Sure! Here is my rating: {"rating": 5, "reflection": "Great."} Done.')
        self.assertEqual(_parse_reflection_stdout(out, b"", 0), (5, "Great."))

    def test_empty_stdout_surfaces_stderr(self):
        with self.assertRaises(ValueError) as cm:
            _parse_reflection_stdout(b"", b"Invalid API key", 1)
        self.assertIn("Invalid API key", str(cm.exception))
        self.assertIn("empty stdout", str(cm.exception))

    def test_non_json_stdout(self):
        with self.assertRaises(ValueError) as cm:
            _parse_reflection_stdout(b"segfault: core dumped", b"", 139)
        self.assertIn("not JSON", str(cm.exception))
        # Must NOT be the old cryptic JSONDecodeError message.
        self.assertNotIn("Expecting value", str(cm.exception))

    def test_error_envelope_surfaces_reason(self):
        out = _envelope("Credit balance too low", is_error=True, subtype="error_during_execution")
        with self.assertRaises(ValueError) as cm:
            _parse_reflection_stdout(out, b"", 0)
        msg = str(cm.exception)
        self.assertIn("Credit balance too low", msg)
        self.assertIn("is_error=True", msg)

    def test_empty_result_field(self):
        # The exact #272 failure: valid envelope, empty result → used to crash on json.loads("").
        out = _envelope("")
        with self.assertRaises(ValueError) as cm:
            _parse_reflection_stdout(out, b"", 0)
        self.assertIn("<empty result>", str(cm.exception))
        self.assertNotIn("Expecting value", str(cm.exception))

    def test_result_without_json_object(self):
        out = _envelope("I cannot rate this task.")
        with self.assertRaises(ValueError) as cm:
            _parse_reflection_stdout(out, b"", 0)
        self.assertIn("no JSON object", str(cm.exception))


class BuildReflectionPromptTests(unittest.TestCase):
    def _prompt(self, **kw):
        base = dict(
            title="Fix bug", status="completed", duration_s=12.3,
            num_turns=4, cost_usd=0.02, error="",
        )
        base.update(kw)
        return _build_reflection_prompt(**base)

    def test_forbids_asking_for_context(self):
        # The 00:20 prod failure: model replied "I don't have enough context..." with no
        # JSON. The prompt must explicitly forbid that so a rating is always produced.
        p = self._prompt().lower()
        self.assertIn("do not ask for more information", p)
        self.assertIn("only the metrics provided", p)

    def test_demands_bare_json_object(self):
        p = self._prompt()
        self.assertIn('{"rating": <1-5>, "reflection": "<one sentence>"}', p)
        self.assertIn("Output ONLY this JSON object", p)

    def test_includes_metrics_and_rubric(self):
        p = self._prompt(title="Deploy", status="failed", error="boom")
        self.assertIn("Deploy", p)
        self.assertIn("failed", p)
        self.assertIn("boom", p)
        self.assertIn("Scoring guide", p)

    def test_empty_title_falls_back_to_untitled(self):
        self.assertIn("Task: Untitled", self._prompt(title=""))


if __name__ == "__main__":
    unittest.main()
