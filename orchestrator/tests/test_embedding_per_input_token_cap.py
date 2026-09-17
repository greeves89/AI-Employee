"""Ein einzelner Eingabetext an die OpenAI-Einbettungs-Schnittstelle muss unter
8.192 Token bleiben, unabhaengig von der Sprache (Issue #743).

``embed()``/``embed_batch()`` kappten bisher jeden Einzeltext auf
``MAX_INPUT_LENGTH * 4`` = 32.000 ZEICHEN — eine Annahme von ~4 Zeichen je
Token, die nur fuer englische Prosa haelt. Gemessen mit dem echten Tokenizer
des Modells bei 32.000 Zeichen: Englisch blieb bei 7.112 Token darunter,
Deutsch (11.389), Russisch (10.355), Arabisch (22.710) und Chinesisch (31.998)
rissen die Grenze allesamt. Das Produkt ist deutschsprachig — kein Randfall.

Wie in ``test_embedding_openai_request_limit.py`` zaehlt hier NICHT die eigene
Schaetzung des Produktionscodes, sondern der echte Tokenizer (tiktoken) — sonst
beweist der Test nur, dass der Code sich selbst zustimmt, genau wie beim alten
Zeichen-Budget.
"""

import unittest
from unittest.mock import patch

from app.services import embedding_service as es
from app.services.embedding_service import (
    EMBEDDING_DIM,
    EmbeddingService,
    truncate_for_openai_input,
)

try:  # test-only dependency, installed by CI alongside pytest
    import tiktoken

    _ENCODING = tiktoken.encoding_for_model("text-embedding-3-small")
except Exception:  # pragma: no cover - only when tiktoken is unavailable
    _ENCODING = None

API_MAX_TOKENS_PER_INPUT = 8_192

# Reale mehrsprachige Saetze, wiederholt bis 32.000 Zeichen — dieselbe
# Methodik wie in der Issue-Beschreibung, damit die gemessenen Tokenzahlen mit
# den dort dokumentierten vergleichbar sind.
_DE = ("Die Übersetzung berücksichtigt Umlaute, Straßennamen und "
       "zusammengesetzte Hauptwörter wie Donaudampfschifffahrt. ")
_RU = ("Съешь ещё этих мягких французских булок, да выпей же чаю. ")
_AR = ("قفز الثعلب البني السريع فوق الكلب الكسول في الصحراء الواسعة. ")
_ZH = ("快速的棕色狐狸跳过了懒狗，天气晴朗，适合长途旅行。")


def _repeat_to(text: str, chars: int) -> str:
    reps = chars // len(text) + 1
    return (text * reps)[:chars]


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class _PerInputEnforcingClient:
    """Lehnt wie die echte API ab, sobald EIN Einzeltext 8.192 Token reisst —
    nicht nur die Summe der Anfrage. Ein Doppelgaenger, der nie "nein" sagen
    kann, beweist nichts."""

    def __init__(self):
        self.requests: list[list[str]] = []

    async def post(self, url, json=None, **kwargs):
        texts = json["input"]
        self.requests.append(texts)
        if _ENCODING is not None:
            for t in texts:
                if len(_ENCODING.encode(t, disallowed_special=())) > API_MAX_TOKENS_PER_INPUT:
                    return _FakeResponse(
                        400, text="This model's maximum context length is 8192 tokens",
                    )
        data = [{"index": i, "embedding": [float(i)] * EMBEDDING_DIM} for i in range(len(texts))]
        return _FakeResponse(200, {"data": data})


def _service_with(client) -> EmbeddingService:
    svc = EmbeddingService()

    async def _get(_self=None):
        return client

    svc._get_openai_client = _get  # type: ignore[method-assign]
    svc._check_local_available = lambda: _false()  # force the OpenAI fallback path
    return svc


async def _false():
    return False


@unittest.skipIf(_ENCODING is None, "tiktoken not installed")
class TruncateForOpenAIInputTests(unittest.TestCase):
    """Direkter Test der Kappungsfunktion, mit dem echten Tokenizer nachgezaehlt."""

    def test_english_was_already_fine_and_stays_fine(self):
        text = _repeat_to("The quick brown fox jumps over the lazy dog. ", 32_000)
        truncated = truncate_for_openai_input(text)
        self.assertLessEqual(len(_ENCODING.encode(truncated)), API_MAX_TOKENS_PER_INPUT)

    def test_german_with_umlauts_is_kept_under_the_limit(self):
        """Das war der gemeldete Fehler: 32.000 Zeichen Deutsch ergaben 11.389
        Token — ueber der Grenze."""
        text = _repeat_to(_DE, 32_000)
        truncated = truncate_for_openai_input(text)
        self.assertLessEqual(len(_ENCODING.encode(truncated)), API_MAX_TOKENS_PER_INPUT)

    def test_russian_is_kept_under_the_limit(self):
        text = _repeat_to(_RU, 32_000)
        truncated = truncate_for_openai_input(text)
        self.assertLessEqual(len(_ENCODING.encode(truncated)), API_MAX_TOKENS_PER_INPUT)

    def test_arabic_is_kept_under_the_limit(self):
        text = _repeat_to(_AR, 32_000)
        truncated = truncate_for_openai_input(text)
        self.assertLessEqual(len(_ENCODING.encode(truncated)), API_MAX_TOKENS_PER_INPUT)

    def test_chinese_is_kept_under_the_limit(self):
        """Die dichteste Sprache im Sample: 31.998 von 32.000 Zeichen waren
        eigene Token."""
        text = _repeat_to(_ZH, 32_000)
        truncated = truncate_for_openai_input(text)
        self.assertLessEqual(len(_ENCODING.encode(truncated)), API_MAX_TOKENS_PER_INPUT)

    def test_short_text_is_returned_unchanged(self):
        text = "Ein kurzer Satz."
        self.assertEqual(truncate_for_openai_input(text), text)

    def test_the_cut_never_lands_mid_character(self):
        """encode -> schneiden -> decode(errors='ignore'): das Ergebnis muss
        IMMER gueltiges UTF-8 sein und darf beim Zurueck-Encodieren nicht
        wachsen (kein halbes Mehrbyte-Zeichen haengt am Ende)."""
        for text in (_repeat_to(_DE, 9_000), _repeat_to(_ZH, 9_000), _repeat_to(_AR, 9_000)):
            truncated = truncate_for_openai_input(text, max_bytes=100)
            reencoded = truncated.encode("utf-8")  # raises if invalid
            self.assertLessEqual(len(reencoded), 100)

    def test_byte_budget_is_respected_exactly_at_the_boundary(self):
        # 'ä' ist 2 Bytes in UTF-8 — ein Budget von 5 darf nicht mitten in ein
        # 'ä' schneiden.
        text = "aaää"  # 2 + 2*2 = 6 Bytes
        truncated = truncate_for_openai_input(text, max_bytes=5)
        self.assertLessEqual(len(truncated.encode("utf-8")), 5)
        truncated.encode("utf-8")  # must not raise


@unittest.skipIf(_ENCODING is None, "tiktoken not installed")
class EmbedRejectsNothingAfterTruncationTests(unittest.IsolatedAsyncioTestCase):
    """End-to-end durch embed()/embed_batch(): der Doppelgaenger lehnt nach
    echter Tokenzahl ab, eine wirkungslose Kappung muss hier durchfallen."""

    async def test_a_long_german_text_is_not_rejected(self):
        client = _PerInputEnforcingClient()
        svc = _service_with(client)
        with patch.object(es.settings, "openai_api_key", "sk-test"):
            vec = await svc.embed(_repeat_to(_DE, 32_000))
        self.assertIsNotNone(vec)

    async def test_a_long_chinese_text_in_a_batch_is_not_rejected(self):
        client = _PerInputEnforcingClient()
        svc = _service_with(client)
        with patch.object(es.settings, "openai_api_key", "sk-test"):
            out = await svc.embed_batch(["kurz", _repeat_to(_ZH, 32_000)])
        self.assertEqual(len(out), 2)
        self.assertTrue(all(v is not None for v in out))


if __name__ == "__main__":
    unittest.main()
