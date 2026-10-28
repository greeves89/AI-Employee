"""DLP egress filter (#388) — unit tests for the deterministic scanner/policy core.

Covers:
  * classify(): secret / iban / credit_card (Luhn) / email / de_tax_id detection
    and no-false-positive on benign text.
  * mask(): redacts the requested classes, leaves others intact.
  * resolve_actions(): agent-specific > global > built-in default precedence.
  * decide(): block wins; mask produces masked output; log/allow pass through.
"""

import json
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

    def test_seed_phrase_detected(self):
        # #703: a real 12-word BIP-39 mnemonic (no checksum needed -- this is
        # a format signal, like every other secret pattern here).
        phrase = "abandon ability able about above absent absorb abstract absurd abuse access accident"
        self.assertIn("seed_phrase", dlp.classify(f"hier mein Seed: {phrase}"))

    def test_seed_phrase_needs_twelve_words(self):
        # Eleven wordlist words in a row is NOT a seed phrase (BIP-39's floor
        # is 12) -- must not false-positive on a shorter coincidental run.
        short = "abandon ability able about above absent absorb abstract absurd abuse access"
        self.assertNotIn("seed_phrase", dlp.classify(short))

    def test_seed_phrase_not_triggered_by_ordinary_prose(self):
        # German business prose must not accidentally read as a seed phrase.
        prose = (
            "Bitte das Angebot bis Freitag pruefen und Rueckmeldung geben, "
            "danach kuemmern wir uns um die Rechnung und den naechsten Termin "
            "fuer das Projekt mit dem Kunden und dem Team."
        )
        self.assertNotIn("seed_phrase", dlp.classify(prose))


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

    def test_masks_seed_phrase(self):
        phrase = "abandon ability able about above absent absorb abstract absurd abuse access accident"
        out = dlp.mask(f"Seed: {phrase} -- nicht weitergeben!", {"seed_phrase"})
        self.assertNotIn("abandon", out)
        self.assertIn("[REDACTED_SEED_PHRASE]", out)


class HighConfidenceMarkerScopeTests(unittest.TestCase):
    """#703: new provider/seed-phrase coverage masks and logs, but must not
    (yet) be able to trigger the Sentinel's secret_in_output agent-stop --
    their false-positive rate in this operation is unproven. See the comment
    above HIGH_CONFIDENCE_SECRET_MARKERS in app/core/dlp.py."""

    def test_new_provider_markers_excluded_from_high_confidence(self):
        for marker in (
            "[REDACTED_STRIPE_KEY]",
            "[REDACTED_GOOGLE_KEY]",
            "[REDACTED_HF_TOKEN]",
            "[REDACTED_DISCORD_TOKEN]",
            "[REDACTED_DB_URL]",
        ):
            self.assertNotIn(marker, dlp.HIGH_CONFIDENCE_SECRET_MARKERS)

    def test_new_provider_keys_do_not_trigger_high_confidence_scan(self):
        stripe = "sk_live_" + "A" * 24
        self.assertEqual(dlp.find_high_confidence_secrets(f"STRIPE_KEY={stripe}"), [])

    def test_seed_phrase_is_not_a_dlp_class_marker_either(self):
        # seed_phrase isn't produced by a log_redaction pattern at all, so it
        # cannot leak into find_high_confidence_secrets() by construction --
        # this pins that invariant down explicitly.
        phrase = "abandon ability able about above absent absorb abstract absurd abuse access accident"
        self.assertEqual(dlp.find_high_confidence_secrets(phrase), [])


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


class WerkzeugMaskierungTests(unittest.TestCase):
    """Werkzeug-Eingaben ohne Geheimnisse (#911) — und weiterhin gueltiges JSON."""

    TOKEN = "abcdefghijklmnopqrstuvwxyz0123456789"

    def test_bearer_am_ende_eines_befehls_bleibt_gueltiges_json(self):
        roh = json.dumps({"command": f'curl -H "Authorization: Bearer {self.TOKEN}"'})
        aus = dlp.werkzeug_eingabe_maskieren(roh)
        self.assertNotIn(self.TOKEN, aus)
        self.assertIn("curl", json.loads(aus)["command"])

    def test_geheimnis_unter_sprechendem_schluessel(self):
        aus = json.loads(dlp.werkzeug_eingabe_maskieren(
            json.dumps({"url": "https://example.invalid", "password": "hunter2-geheim"})))
        self.assertNotIn("hunter2-geheim", json.dumps(aus))
        self.assertEqual(aus["url"], "https://example.invalid")

    def test_verschachtelt_und_als_objekt(self):
        geheim = "sk-" + "B" * 30
        aus = dlp.werkzeug_eingabe_maskieren({"schritte": [{"env": {"API_KEY": geheim}}]})
        self.assertIsInstance(aus, dict)
        self.assertNotIn(geheim, json.dumps(aus))

    def test_unveraenderte_eingabe_bleibt_zeichengleich(self):
        roh = '{"command":"ls -la /workspace","n":3}'
        self.assertEqual(dlp.werkzeug_eingabe_maskieren(roh), roh)

    def test_fachliche_daten_bleiben_stehen(self):
        """Nur Geheimnisse: E-Mail und IBAN sind oft der Gegenstand der Arbeit."""
        roh = json.dumps({"to": "kunde@example.com", "iban": "DE89370400440532013000"})
        self.assertEqual(dlp.werkzeug_eingabe_maskieren(roh), roh)

    def test_kein_json_wird_als_text_maskiert(self):
        aus = dlp.werkzeug_eingabe_maskieren(f'{{"command": "Bearer {self.TOKEN}')
        self.assertNotIn(self.TOKEN, aus)

    def test_strom_ereignisse(self):
        geheim = "ghp_" + "C" * 30
        call = {"type": "tool_call", "message_id": "m1",
                "data": {"tool": "Bash", "tool_use_id": "t1",
                         "input": {"command": f"echo {geheim}"}}}
        result = {"type": "tool_result",
                  "data": {"tool_use_id": "t1",
                           "content": [{"type": "text", "text": f"token {geheim}"}]}}
        done = {"type": "done", "data": {"text": "fertig", "tool_calls": [
            {"tool": "Bash", "input": json.dumps({"command": f"echo {geheim}"}),
             "output": geheim}]}}
        for ereignis in (call, result, done):
            aus = dlp.werkzeug_ereignis_maskieren(ereignis)
            self.assertNotIn(geheim, json.dumps(aus))
            self.assertEqual(aus["type"], ereignis["type"])
        json.loads(dlp.werkzeug_ereignis_maskieren(done)["data"]["tool_calls"][0]["input"])
        # Text-Ereignisse bleiben unberuehrt.
        text = {"type": "text", "data": {"text": "hallo"}}
        self.assertIs(dlp.werkzeug_ereignis_maskieren(text), text)

    def test_strom_text(self):
        geheim = "sk-" + "D" * 30
        roh = json.dumps({"type": "tool_call", "data": {"input": {"k": geheim}}})
        self.assertNotIn(geheim, dlp.werkzeug_ereignis_text_maskieren(roh))
        harmlos = json.dumps({"type": "tool_call", "data": {"input": {"k": "v"}}})
        self.assertEqual(dlp.werkzeug_ereignis_text_maskieren(harmlos), harmlos)
        self.assertEqual(dlp.werkzeug_ereignis_text_maskieren("kein json"), "kein json")


if __name__ == "__main__":
    unittest.main()
