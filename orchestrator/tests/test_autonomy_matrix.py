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

    # --- allowed_categories_from_matrix (C-1: powers the hard tool gate) -------

    def test_categories_l1_only_read_and_web(self):
        cats = am.allowed_categories_from_matrix(am.matrix_for_level("l1"))
        self.assertEqual(cats, {"file_read", "web_search"})

    def test_categories_l3_container_but_no_external(self):
        cats = am.allowed_categories_from_matrix(am.matrix_for_level("l3"))
        self.assertEqual(cats, {"file_read", "file_write", "shell_exec", "system_config", "web_search"})
        self.assertNotIn("custom", cats)     # email/messaging/git stay gated
        self.assertNotIn("purchase", cats)

    def test_categories_l4_covers_all(self):
        cats = am.allowed_categories_from_matrix(am.matrix_for_level("l4"))
        self.assertEqual(cats, {"file_read", "file_write", "shell_exec",
                                "system_config", "web_search", "custom", "purchase"})

    def test_ask_and_deny_are_excluded(self):
        # The exact CRITICAL repro: L3 with purchases hardened to deny.
        m = am.normalize_matrix({"purchases": "deny"}, "l3")
        cats = am.allowed_categories_from_matrix(m)
        self.assertNotIn("purchase", cats)   # deny → NOT whitelisted → hard-blocked
        self.assertIn("shell_exec", cats)    # untouched cells still enforced

    def test_all_denied_yields_empty(self):
        m = {k: am.DENY for k in am.CAPABILITY_KEYS}
        self.assertEqual(am.allowed_categories_from_matrix(m), set())

    def test_shared_custom_bucket(self):
        # Only messaging allowed among the external "custom" group → bucket present.
        m = am.normalize_matrix({"messaging": "allow"}, "l1")
        self.assertIn("custom", am.allowed_categories_from_matrix(m))


if __name__ == "__main__":
    unittest.main()
