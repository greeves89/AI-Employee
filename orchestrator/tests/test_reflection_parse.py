"""Tests for _parse_reflection_stdout — the claude-CLI reflection output parser (#272).

Guards the paths that previously raised the generic "Expecting value: line 1 column 1"
and masked the real auth/quota failure in the platform log.

Also covers the fulfilled/gap false-completion verdict added alongside the star
rating: the judge now sees the original request + actual result (not just run
metrics) and separately judges whether the result really fulfills the request.
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
        self.assertEqual(
            _parse_reflection_stdout(out, b"", 0), (4, "Solid run.", None, "")
        )

    def test_rating_is_clamped(self):
        out = _envelope('{"rating": 9, "reflection": "x"}')
        self.assertEqual(_parse_reflection_stdout(out, b"", 0)[0], 5)
        out = _envelope('{"rating": -3, "reflection": "x"}')
        self.assertEqual(_parse_reflection_stdout(out, b"", 0)[0], 1)

    def test_markdown_fenced_json(self):
        out = _envelope('```json\n{"rating": 3, "reflection": "Ok."}\n```')
        self.assertEqual(
            _parse_reflection_stdout(out, b"", 0), (3, "Ok.", None, "")
        )

    def test_json_wrapped_in_prose(self):
        out = _envelope('Sure! Here is my rating: {"rating": 5, "reflection": "Great."} Done.')
        self.assertEqual(
            _parse_reflection_stdout(out, b"", 0), (5, "Great.", None, "")
        )

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

    def test_fulfilled_true_is_parsed(self):
        out = _envelope(
            '{"rating": 5, "reflection": "Gut.", "fulfilled": true, "gap": ""}'
        )
        rating, reflection, fulfilled, gap = _parse_reflection_stdout(out, b"", 0)
        self.assertTrue(fulfilled)
        self.assertEqual(gap, "")

    def test_fulfilled_false_carries_the_gap(self):
        out = _envelope(
            '{"rating": 3, "reflection": "Lief durch.", "fulfilled": false, '
            '"gap": "Der Agent hat nur angekuendigt, X zu tun, X aber nie geliefert."}'
        )
        rating, reflection, fulfilled, gap = _parse_reflection_stdout(out, b"", 0)
        self.assertFalse(fulfilled)
        self.assertIn("nie geliefert", gap)

    def test_missing_fulfilled_is_none_not_false(self):
        """A model/prompt that omits the field must read as 'no verdict', never as
        a false-completion warning — that would fire on every task rated by an
        older/uncooperative model."""
        out = _envelope('{"rating": 4, "reflection": "Ok."}')
        _, _, fulfilled, gap = _parse_reflection_stdout(out, b"", 0)
        self.assertIsNone(fulfilled)
        self.assertEqual(gap, "")

    def test_gap_is_dropped_when_fulfilled_is_true(self):
        """A stray 'gap' text alongside fulfilled=true must not leak into a
        notification as a false warning."""
        out = _envelope(
            '{"rating": 5, "reflection": "Gut.", "fulfilled": true, '
            '"gap": "sollte nie erscheinen"}'
        )
        _, _, fulfilled, gap = _parse_reflection_stdout(out, b"", 0)
        self.assertTrue(fulfilled)
        self.assertEqual(gap, "")


class BuildReflectionPromptTests(unittest.TestCase):
    def _prompt(self, **kw):
        base = dict(
            title="Fix bug", status="completed", duration_s=12.3,
            num_turns=4, cost_usd=0.02, error="",
            prompt_text="Behebe den Login-Bug", result_preview="Bug behoben, Tests gruen.",
        )
        base.update(kw)
        return _build_reflection_prompt(**base)

    def test_forbids_asking_for_context(self):
        # The 00:20 prod failure: model replied "I don't have enough context..." with no
        # JSON. The prompt must explicitly forbid that so a rating is always produced.
        p = self._prompt().lower()
        self.assertIn("do not ask for more information", p)
        self.assertIn("only the information provided", p)

    def test_demands_bare_json_object_with_fulfilled_and_gap(self):
        p = self._prompt()
        self.assertIn(
            '{"rating": <1-5>, "reflection": "<one sentence>", '
            '"fulfilled": <true|false>, "gap": "<one sentence, empty string if fulfilled>"}',
            p,
        )
        self.assertIn("Output ONLY this JSON object", p)

    def test_includes_metrics_and_rubric(self):
        p = self._prompt(title="Deploy", status="failed", error="boom")
        self.assertIn("Deploy", p)
        self.assertIn("failed", p)
        self.assertIn("boom", p)
        self.assertIn("Scoring guide", p)

    def test_empty_title_falls_back_to_untitled(self):
        self.assertIn("Task: Untitled", self._prompt(title=""))

    def test_includes_the_original_request_and_actual_result(self):
        """The core fix: the judge used to see ONLY run metrics, never the actual
        ask or the actual delivered result — it could not judge fulfillment at
        all. Both must now reach the prompt verbatim."""
        p = self._prompt(
            prompt_text="Schreibe eine Zusammenfassung von X",
            result_preview="Ich kuemmere mich gleich darum.",
        )
        self.assertIn("Schreibe eine Zusammenfassung von X", p)
        self.assertIn("Ich kuemmere mich gleich darum.", p)
        self.assertIn("fulfills the original request", p)

    def test_missing_request_and_result_fall_back_to_placeholders(self):
        p = self._prompt(prompt_text="", result_preview="")
        self.assertIn("(none)", p)
        self.assertIn("(empty)", p)


if __name__ == "__main__":
    unittest.main()
