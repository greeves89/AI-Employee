"""DLP egress filter (#388) — unit tests for the deterministic scanner/policy core.

Covers:
  * classify(): secret / iban / credit_card (Luhn) / email / de_tax_id detection
    and no-false-positive on benign text.
  * mask(): redacts the requested classes, leaves others intact.
  * resolve_actions(): agent-specific > global > built-in default precedence.
  * decide(): block wins; mask produces masked output; log/allow pass through.
"""

import unittest
from unittest.mock import MagicMock

from app.core import dlp


class ClassifyTests(unittest.TestCase):
    def test_benign_text_matches_nothing(self):
        self.assertEqual(dlp.classify("Treffen um 15 Uhr zum Projekt X, bitte Bescheid geben."), {})

    def test_secret_detected(self):
        self.assertIn("secret", dlp.classify("hier der key: sk-abcdEFGH1234567890xyz"))
        self.assertIn("secret", dlp.classify("Authorization: Bearer abcdef1234567890abcdef"))

    def test_iban_detected(self):
        c = dlp.classify("Bitte an DE89370400440532013000 überweisen.")
        self.assertEqual(c.get("iban"), 1)

    def test_credit_card_luhn(self):
        self.assertIn("credit_card", dlp.classify("Karte 4111 1111 1111 1111"))   # valid Luhn
        # invalid Luhn 16-digit number must NOT be flagged as a credit card
        self.assertNotIn("credit_card", dlp.classify("Nummer 4111 1111 1111 1112"))

    def test_email_detected(self):
        self.assertEqual(dlp.classify("schreib an max.mustermann@example.com bitte").get("email"), 1)

    def test_de_tax_id_detected(self):
        self.assertIn("de_tax_id", dlp.classify("Steuer-ID 12345678901 im Anhang"))

    def test_iban_not_misread_as_credit_card(self):
        c = dlp.classify("IBAN DE89370400440532013000")
        self.assertIn("iban", c)
        self.assertNotIn("credit_card", c)


class SampleTests(unittest.TestCase):
    def test_mask_sample_format(self):
        self.assertEqual(dlp.mask_sample("dfghjkas"), "df***as")   # first2 *** last2
        self.assertEqual(dlp.mask_sample("ab"), "a***")            # short -> reveal less
        self.assertEqual(dlp.mask_sample(""), "***")

    def test_mask_sample_never_full_value(self):
        secret = "sk-abcdEFGH1234567890xyzKLMN"
        masked = dlp.mask_sample(secret)
        self.assertNotIn("abcdEFGH1234567890", masked)   # middle removed
        self.assertTrue(masked.startswith("sk"))
        self.assertIn("***", masked)

    def test_samples_of_masks_and_caps(self):
        matches = dlp.scan_matches("Karte 4111 1111 1111 1111 und Mail a@b.de, c@d.de")
        s = dlp.samples_of(matches, per_class=3)
        self.assertIn("credit_card", s)
        self.assertTrue(all("***" in x for xs in s.values() for x in xs))
        # full card number never appears in a sample
        for x in s.get("credit_card", []):
            self.assertNotIn("4111111111111111", x.replace(" ", ""))

    def test_scan_matches_counts_match_classify(self):
        text = "IBAN DE89370400440532013000 mail x@y.de"
        matches = dlp.scan_matches(text)
        counts = dlp.classify(text)
        self.assertEqual({c: len(v) for c, v in matches.items()}, counts)


class MaskTests(unittest.TestCase):
    def test_masks_only_requested_classes(self):
        text = "Mail max@example.com und IBAN DE89370400440532013000"
        out = dlp.mask(text, {"email"})
        self.assertIn("[REDACTED_EMAIL]", out)
        self.assertIn("DE89370400440532013000", out)     # iban left intact
        out2 = dlp.mask(text, {"email", "iban"})
        self.assertIn("[REDACTED_IBAN]", out2)
        self.assertNotIn("DE89370400440532013000", out2)


def _rule(pii_class, action, agent_id=None, enabled=True):
    r = MagicMock()
    r.pii_class = pii_class
    r.action = action
    r.agent_id = agent_id
    r.enabled = enabled
    return r


class ResolveAndDecideTests(unittest.TestCase):
    def test_defaults_when_no_rules(self):
        actions = dlp.resolve_actions({"secret": 1, "email": 1}, [])
        self.assertEqual(actions["secret"], "block")   # built-in default
        self.assertEqual(actions["email"], "allow")

    def test_agent_rule_beats_global(self):
        rules = [_rule("email", "allow"), _rule("email", "block", agent_id="a1")]
        actions = dlp.resolve_actions({"email": 1}, rules)
        self.assertEqual(actions["email"], "block")     # agent-specific wins

    def test_disabled_rule_ignored(self):
        rules = [_rule("iban", "allow", enabled=False)]
        actions = dlp.resolve_actions({"iban": 1}, rules)
        self.assertEqual(actions["iban"], "mask")       # falls back to default

    def test_decide_block_wins(self):
        v = dlp.decide("x", {"secret": 1, "email": 1}, {"secret": "block", "email": "allow"})
        self.assertFalse(v.allowed)
        self.assertEqual(v.effective, "block")
        self.assertEqual(v.output, "")

    def test_decide_mask_produces_masked_output(self):
        text = "IBAN DE89370400440532013000"
        v = dlp.decide(text, {"iban": 1}, {"iban": "mask"})
        self.assertTrue(v.allowed)
        self.assertIn("[REDACTED_IBAN]", v.output)

    def test_decide_log_passes_through(self):
        v = dlp.decide("Steuer 12345678901", {"de_tax_id": 1}, {"de_tax_id": "log"})
        self.assertTrue(v.allowed)
        self.assertEqual(v.output, "Steuer 12345678901")
        self.assertEqual(v.effective, "log")

    def test_no_classes_is_allow(self):
        v = dlp.decide("hallo", {}, {})
        self.assertTrue(v.allowed)
        self.assertEqual(v.effective, "allow")


if __name__ == "__main__":
    unittest.main()
