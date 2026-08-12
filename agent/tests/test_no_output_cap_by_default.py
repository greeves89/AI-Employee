"""Keine eigene Obergrenze für die Antwortlänge mehr.

``LLM_MAX_TOKENS`` stand auf **4096** — eine Zahl aus der Zeit, als Modelle nicht
mehr konnten. Für einen Agenten, der ein Code-Review, eine Spezifikation oder eine
fertige Datei liefern soll, ist das zu wenig. Das Tückische daran: die Antwort
bricht **mitten im Satz** ab und sieht trotzdem aus wie ein fertiges Ergebnis —
niemand bekommt eine Fehlermeldung, und der Rest fehlt einfach.

Jetzt gilt 0 = keine eigene Grenze. Dann entscheidet das Modell, und das ist fast
immer das Richtige.

**Anthropic ist der Sonderfall:** dort ist ``max_tokens`` ein Pflichtfeld. Weglassen
geht nicht, es muss eine Zahl hinein — und zwar keine beliebig hohe, denn ein Wert
oberhalb des Modellmaximums wird mit 400 abgewiesen. Deshalb je Modellfamilie der
dort erlaubte Wert.
"""

import unittest

from app.providers.anthropic_provider import AnthropicProvider


def _anthropic(model: str, max_tokens: int = 0) -> AnthropicProvider:
    return AnthropicProvider(
        api_endpoint="https://example.invalid", api_key="x",
        model_name=model, max_tokens=max_tokens,
    )


class TheDefaultIsNoCapTests(unittest.TestCase):
    def test_the_setting_defaults_to_zero(self):
        from app.config import Settings

        self.assertEqual(Settings.model_fields["llm_max_tokens"].default, 0)


class AnthropicNeedsANumberTests(unittest.TestCase):
    """Pflichtfeld — hier kann nicht weggelassen werden."""

    def test_an_explicit_limit_is_respected(self):
        self.assertEqual(_anthropic("claude-sonnet-5", 1234)._max_tokens_for_request(), 1234)

    def test_without_a_limit_the_family_maximum_is_used(self):
        self.assertEqual(_anthropic("claude-sonnet-5")._max_tokens_for_request(), 64_000)

    def test_older_families_get_their_lower_maximum(self):
        """Zu hoch ist keine sichere Wahl: oberhalb des Modellmaximums antwortet
        die API mit 400, und dann liefert der Agent gar nichts mehr."""
        self.assertEqual(_anthropic("claude-3-5-sonnet-20241022")._max_tokens_for_request(), 8_192)
        self.assertEqual(_anthropic("claude-3-opus")._max_tokens_for_request(), 4_096)

    def test_an_unknown_model_gets_the_universally_safe_value(self):
        """Ein Tippfehler im Modellnamen darf nicht in ein 400 laufen."""
        self.assertEqual(_anthropic("gibt-es-nicht")._max_tokens_for_request(), 8_192)

    def test_the_longest_matching_family_wins_over_a_shorter_one(self):
        """``claude-3-5-haiku`` darf nicht als ``claude-3`` durchgehen."""
        self.assertEqual(_anthropic("claude-3-5-haiku-latest")._max_tokens_for_request(), 8_192)


class OtherProvidersJustOmitItTests(unittest.TestCase):
    """Bei OpenAI und Google heisst „kein Schluessel" schlicht: Modellmaximum."""

    def _openai_body(self, max_tokens: int) -> dict:
        from app.providers.openai_provider import OpenAIProvider

        p = OpenAIProvider(api_endpoint="https://example.invalid", api_key="x",
                           model_name="gpt-4o", max_tokens=max_tokens)
        return p._build_legacy_body([])

    def test_no_key_without_a_limit(self):
        self.assertNotIn("max_tokens", self._openai_body(0))

    def test_the_key_appears_with_a_limit(self):
        self.assertEqual(self._openai_body(2048)["max_tokens"], 2048)

    def test_the_legacy_body_is_actually_returned(self):
        """Beim Umbau ist das ``return`` einmal verlorengegangen — dann liefert
        die Route stillschweigend ``None`` statt eines Rumpfes."""
        self.assertIsInstance(self._openai_body(0), dict)


if __name__ == "__main__":
    unittest.main()
