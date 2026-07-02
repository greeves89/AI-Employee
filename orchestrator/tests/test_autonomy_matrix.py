"""Tests for the 3-state autonomy capability matrix (security-relevant)."""

import unittest

from app.core import autonomy_matrix as am


class AutonomyMatrixTests(unittest.TestCase):
    def test_presets_shape(self):
        for lvl in ("l1", "l2", "l3", "l4"):
            m = am.matrix_for_level(lvl)
            self.assertEqual(set(m), set(am.CAPABILITY_KEYS))
            self.assertTrue(all(v in am.STATES for v in m.values()))

    def test_level_semantics(self):
        l1 = am.matrix_for_level("l1")
        self.assertEqual(l1["file_read"], am.ALLOW)
        self.assertEqual(l1["file_write"], am.ASK)
        self.assertEqual(l1["email_m365"], am.ASK)
        l3 = am.matrix_for_level("l3")
        self.assertEqual(l3["shell_exec"], am.ALLOW)      # container allowed
        self.assertEqual(l3["email_m365"], am.ASK)        # external asks
        self.assertEqual(l3["purchases"], am.ASK)

    def test_l4_is_unrestricted(self):
        self.assertTrue(am.is_unrestricted(am.matrix_for_level("l4")))
        self.assertFalse(am.is_unrestricted(am.matrix_for_level("l3")))

    def test_normalize_fills_and_validates(self):
        n = am.normalize_matrix({"purchases": "deny", "file_read": "bogus", "junk": "x"}, "l3")
        self.assertEqual(set(n), set(am.CAPABILITY_KEYS))   # complete
        self.assertEqual(n["purchases"], am.DENY)           # valid state kept
        self.assertEqual(n["file_read"], am.ALLOW)          # invalid → preset (l3 allow)
        self.assertNotIn("junk", n)                         # unknown dropped

    def test_normalize_none_returns_preset(self):
        self.assertEqual(am.normalize_matrix(None, "l1"), am.matrix_for_level("l1"))

    def test_prompt_full_autonomy(self):
        p = am.matrix_to_prompt(am.matrix_for_level("l4"))
        self.assertIn("FULLY AUTONOMOUS", p)
        self.assertIn("Do NOT call", p.replace("`", ""))

    def test_prompt_mixed_lists_allow_ask_deny(self):
        m = am.normalize_matrix({"purchases": "deny"}, "l3")
        p = am.matrix_to_prompt(m)
        self.assertIn("ALLOWED without asking", p)
        self.assertIn("Requires approval", p)
        self.assertIn("FORBIDDEN", p)          # deny surfaces
        self.assertNotIn("FULLY AUTONOMOUS", p)

    def test_taxonomy_payload(self):
        t = am.taxonomy_payload()
        self.assertEqual({g["key"] for g in t["groups"]}, {"container", "external"})
        self.assertEqual(len(t["capabilities"]), 10)
        self.assertEqual(set(t["presets"]), {"l1", "l2", "l3", "l4"})


if __name__ == "__main__":
    unittest.main()
