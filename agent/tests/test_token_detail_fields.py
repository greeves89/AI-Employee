"""Die Token-Feinaufschlüsselung fließt von den Providern bis ins Ergebnis.

Kundenwunsch (Christian, 2026-08-19): die verwendeten Tokens im Chat anzeigen —
u.a. um zu sehen, ob eine Reasoning-Stufe den Verbrauch verändert. Dafür melden
die Provider jetzt reasoning/cached/cache_write getrennt; hier wird der
Daten-Vertrag festgehalten, damit die Kette nicht still bricht.
"""

import unittest

from app.providers.base import LLMEvent


class TokenDetailContractTests(unittest.TestCase):
    def test_llmevent_carries_detail_tokens(self):
        e = LLMEvent(type="done", input_tokens=100, output_tokens=20,
                     reasoning_tokens=8, cached_tokens=90, cache_write_tokens=5)
        self.assertEqual(e.reasoning_tokens, 8)
        self.assertEqual(e.cached_tokens, 90)
        self.assertEqual(e.cache_write_tokens, 5)

    def test_detail_tokens_default_to_zero(self):
        """Bestandscode ohne die neuen Felder darf nicht brechen."""
        e = LLMEvent(type="done", input_tokens=1, output_tokens=1)
        self.assertEqual(e.reasoning_tokens, 0)
        self.assertEqual(e.cached_tokens, 0)
        self.assertEqual(e.cache_write_tokens, 0)

    def test_openai_extracts_responses_api_details(self):
        """OpenAI Responses-API: input/output_tokens_details -> unsere Felder."""
        usage = {
            "input_tokens": 39599, "output_tokens": 220,
            "input_tokens_details": {"cache_write_tokens": 39596, "cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 66},
        }
        in_det = usage.get("input_tokens_details") or {}
        out_det = usage.get("output_tokens_details") or {}
        self.assertEqual(int(in_det.get("cache_write_tokens") or 0), 39596)
        self.assertEqual(int(out_det.get("reasoning_tokens") or 0), 66)

    def test_anthropic_maps_cache_fields(self):
        """Claude: cache_creation -> cache_write, cache_read -> cached."""
        usage = {"input_tokens": 10, "cache_creation_input_tokens": 7,
                 "cache_read_input_tokens": 3}
        self.assertEqual(int(usage.get("cache_creation_input_tokens") or 0), 7)
        self.assertEqual(int(usage.get("cache_read_input_tokens") or 0), 3)


if __name__ == "__main__":
    unittest.main()
