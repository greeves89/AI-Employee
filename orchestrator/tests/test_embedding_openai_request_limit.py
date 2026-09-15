"""Tests for the OpenAI embedding fallback staying under the per-request limits.

The API rejects a /embeddings call outright if it breaks one of two hard limits:
300k tokens in total, or 2048 entries in the input array. Callers hand over
unbounded batches (the vault indexer passes every chunk of a file at once), so
the fallback has to split. Without splitting, one oversized batch nulls out
every vector in it and the affected vault rows stay permanently unvectorised.

Two seams are used deliberately:

* The HTTP client is monkeypatched with a recorder that captures the `input`
  list of each POST, and that can *reject* a request the way the real API would.
  A double that cannot answer "no" proves nothing, so the recorder enforces the
  documented limits itself.
* Token counts are verified with the model's real tokenizer (tiktoken), not with
  the production code's own size estimate. Re-deriving the estimate the
  production code uses would only prove the code agrees with itself — which is
  exactly how the previous character budget passed its tests while still
  exceeding the token limit on Chinese text.
"""

import asyncio
import unittest
from unittest.mock import patch

from app.services import embedding_service as es
from app.services.embedding_service import (
    EMBEDDING_DIM,
    EmbeddingService,
    split_for_openai_request,
)

try:  # test-only dependency, installed by CI alongside pytest
    import tiktoken

    _ENCODING = tiktoken.encoding_for_model("text-embedding-3-small")
except Exception:  # pragma: no cover - only when tiktoken is unavailable
    _ENCODING = None

API_MAX_TOKENS_PER_REQUEST = 300_000
API_MAX_INPUTS_PER_REQUEST = 2_048

# A Chinese sentence tokenizes at roughly one token per character, which is what
# broke the character budget: 500k characters were assumed to be 250k tokens and
# were in fact ~500k.
CJK_SENTENCE = "这是一个用于测试多语言知识库索引的中文段落。"


def _run(coro):
    return asyncio.run(coro)


def _tokens(text: str) -> int:
    return len(_ENCODING.encode(text, disallowed_special=()))


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class _RecordingClient:
    """Records every POST body and answers with well-formed embeddings.

    ``enforce_api_limits`` makes the double reject oversized requests the way the
    real API does, so a splitter that does not actually split fails the test
    instead of silently passing. ``fail_predicate`` additionally lets a chosen
    sub-batch fail, so the blast radius of one rejected group can be observed.
    """

    def __init__(self, fail_predicate=None, enforce_api_limits=False):
        self.requests: list[list[str]] = []
        self._fail_predicate = fail_predicate
        self._enforce = enforce_api_limits

    async def post(self, url, json=None, **kwargs):
        texts = json["input"]
        self.requests.append(texts)
        if self._enforce:
            if len(texts) > API_MAX_INPUTS_PER_REQUEST:
                return _FakeResponse(400, text="array_above_max_length")
            if _ENCODING is not None:
                total = sum(_tokens(t) for t in texts)
                if total > API_MAX_TOKENS_PER_REQUEST:
                    return _FakeResponse(400, text="max_tokens_per_request")
        if self._fail_predicate is not None and self._fail_predicate(texts):
            return _FakeResponse(400, text="max_tokens_per_request")
        data = [
            {"index": i, "embedding": [float(i)] * EMBEDDING_DIM}
            for i in range(len(texts))
        ]
        return _FakeResponse(200, {"data": data})


def _service_with(client: _RecordingClient) -> EmbeddingService:
    svc = EmbeddingService()

    async def _get(_self=None):
        return client

    svc._get_openai_client = _get  # type: ignore[method-assign]
    return svc


class SplitForOpenAIRequestTests(unittest.TestCase):
    def test_every_group_fits_the_byte_budget(self):
        texts = ["a" * 30 for _ in range(10)]
        groups = split_for_openai_request(texts, max_bytes=100)
        for g in groups:
            self.assertLessEqual(sum(len(t.encode("utf-8")) for t in g), 100)

    def test_multibyte_text_is_measured_in_bytes_not_characters(self):
        """Three-byte characters must consume three times the budget.

        Under the old character budget these ten texts formed a single group;
        measured in bytes they cannot."""
        texts = [CJK_SENTENCE for _ in range(10)]
        per_text_bytes = len(CJK_SENTENCE.encode("utf-8"))
        groups = split_for_openai_request(texts, max_bytes=per_text_bytes * 2)
        self.assertEqual(len(groups), 5)

    def test_group_never_exceeds_the_input_count_limit(self):
        texts = ["tiny" for _ in range(5_000)]
        groups = split_for_openai_request(texts)
        for g in groups:
            self.assertLessEqual(len(g), API_MAX_INPUTS_PER_REQUEST)

    def test_input_count_boundary(self):
        """2048 inputs are one request, 2049 are two."""
        exact = split_for_openai_request(["t"] * API_MAX_INPUTS_PER_REQUEST)
        self.assertEqual(len(exact), 1)
        self.assertEqual(len(exact[0]), API_MAX_INPUTS_PER_REQUEST)

        over = split_for_openai_request(["t"] * (API_MAX_INPUTS_PER_REQUEST + 1))
        self.assertEqual([len(g) for g in over], [API_MAX_INPUTS_PER_REQUEST, 1])

    def test_order_and_completeness_preserved(self):
        texts = [f"text-{i}" * 5 for i in range(25)]
        groups = split_for_openai_request(texts, max_bytes=40)
        flat = [t for g in groups for t in g]
        self.assertEqual(flat, texts)

    def test_oversized_single_text_is_emitted_alone_not_dropped(self):
        big = "x" * 500
        groups = split_for_openai_request(["a", big, "b"], max_bytes=100)
        flat = [t for g in groups for t in g]
        self.assertEqual(flat, ["a", big, "b"])
        self.assertIn([big], groups)

    def test_empty_input(self):
        self.assertEqual(split_for_openai_request([]), [])


class TokenLimitTests(unittest.TestCase):
    """The limit that matters is measured in tokens, so measure it in tokens."""

    @unittest.skipIf(_ENCODING is None, "tiktoken not installed")
    def test_byte_budget_bounds_the_token_count_for_multilingual_text(self):
        """The whole fix rests on bytes being an upper bound for tokens.

        cl100k_base is a byte-level BPE containing all 256 single-byte tokens and
        every merge replaces two tokens with one, so encoding can only shrink the
        count. Asserted here on text that defeats character-based estimates."""
        samples = [
            CJK_SENTENCE * 40,
            "Ein ganz gewöhnlicher deutscher Satz mit Umlauten. " * 40,
            "مرحبا بالعالم هذا نص للاختبار. " * 40,
            "Обычное предложение для проверки. " * 40,
            "plain english ascii filler text " * 40,
            "🙂👨‍👩‍👧‍👦🇩🇪" * 40,
        ]
        for s in samples:
            self.assertLessEqual(_tokens(s), len(s.encode("utf-8")), repr(s[:30]))

    @unittest.skipIf(_ENCODING is None, "tiktoken not installed")
    def test_cjk_batch_stays_under_the_token_limit(self):
        """The regression: 500k characters of Chinese are ~500k tokens.

        The character budget let these through as a single request; every group
        must now be under the API limit as counted by the real tokenizer."""
        # 495k characters — just inside the old character budget, and 495k
        # tokens, far outside the API's token limit.
        texts = [CJK_SENTENCE * 25 for _ in range(900)]
        for group in split_for_openai_request(texts):
            self.assertLessEqual(
                sum(_tokens(t) for t in group), API_MAX_TOKENS_PER_REQUEST
            )

    @unittest.skipIf(_ENCODING is None, "tiktoken not installed")
    def test_cjk_batch_is_embedded_end_to_end_against_a_rejecting_api(self):
        """The counterproof over the public path: a double that rejects exactly
        like the API must return vectors for every input, not Nones."""
        # 495k characters — just inside the old character budget, and 495k
        # tokens, far outside the API's token limit.
        texts = [CJK_SENTENCE * 25 for _ in range(900)]
        client = _RecordingClient(enforce_api_limits=True)
        svc = _service_with(client)
        with patch.object(es.settings, "openai_api_key", "sk-test"):
            out = _run(svc._openai_embed_batch(texts))
        self.assertEqual(len(out), len(texts))
        self.assertTrue(all(v is not None for v in out),
                        "the API double rejected at least one request")


class OpenAIBatchSplittingTests(unittest.TestCase):
    """An oversized batch must become several requests, each under the limits."""

    # 60 texts at the caller's per-text cap — the shape seen in the incident
    # (479k tokens requested in one call).
    OVERSIZED = [("y" * (es.MAX_INPUT_LENGTH * 4)) for _ in range(60)]

    def test_oversized_batch_is_split_into_several_requests(self):
        client = _RecordingClient()
        svc = _service_with(client)
        with patch.object(es.settings, "openai_api_key", "sk-test"):
            _run(svc._openai_embed_batch(self.OVERSIZED))
        self.assertGreater(len(client.requests), 1)

    def test_many_small_inputs_are_split_by_count(self):
        """2100 small chunks fit any byte budget but break the array limit.

        This is the shape a large Markdown file with many short sections
        produces, and it survived the character budget untouched."""
        texts = [f"# Section {i}\n\nSmall paragraph." for i in range(2_100)]
        client = _RecordingClient(enforce_api_limits=True)
        svc = _service_with(client)
        with patch.object(es.settings, "openai_api_key", "sk-test"):
            out = _run(svc._openai_embed_batch(texts))
        self.assertEqual(len(client.requests), 2)
        self.assertTrue(all(len(r) <= API_MAX_INPUTS_PER_REQUEST for r in client.requests))
        self.assertTrue(all(v is not None for v in out))

    def test_all_vectors_returned_aligned_to_input(self):
        client = _RecordingClient()
        svc = _service_with(client)
        with patch.object(es.settings, "openai_api_key", "sk-test"):
            out = _run(svc._openai_embed_batch(self.OVERSIZED))
        self.assertEqual(len(out), len(self.OVERSIZED))
        self.assertTrue(all(v is not None for v in out))
        self.assertTrue(all(len(v) == EMBEDDING_DIM for v in out))

    def test_texts_are_sent_once_each_in_order(self):
        texts = [f"chunk-{i} " + CJK_SENTENCE * (i % 7) for i in range(3_000)]
        client = _RecordingClient()
        svc = _service_with(client)
        with patch.object(es.settings, "openai_api_key", "sk-test"):
            _run(svc._openai_embed_batch(texts))
        sent = [t for req in client.requests for t in req]
        self.assertEqual(sent, texts)

    def test_vectors_stay_aligned_across_group_boundaries(self):
        """Each sub-batch restarts the API's local indices at 0, so a naive
        re-assembly silently mixes vectors up. Distinct texts and distinct
        vectors make that visible instead of only checking the length."""
        texts = [f"chunk-{i}" for i in range(2_500)]
        client = _RecordingClient()
        svc = _service_with(client)
        with patch.object(es.settings, "openai_api_key", "sk-test"):
            out = _run(svc._openai_embed_batch(texts))

        self.assertGreater(len(client.requests), 1)
        expected = [
            float(local_index)
            for req in client.requests
            for local_index in range(len(req))
        ]
        self.assertEqual([v[0] for v in out], expected)

    def test_small_batch_stays_a_single_request(self):
        client = _RecordingClient()
        svc = _service_with(client)
        with patch.object(es.settings, "openai_api_key", "sk-test"):
            out = _run(svc._openai_embed_batch(["short", "texts"]))
        self.assertEqual(len(client.requests), 1)
        self.assertEqual(len(out), 2)


class FailureBlastRadiusTests(unittest.TestCase):
    """A rejected group must not null out the vectors of the other groups."""

    def test_one_failing_group_leaves_the_others_intact(self):
        oversized = [("z" * (es.MAX_INPUT_LENGTH * 4)) for _ in range(60)]
        state = {"seen": 0}

        def fail_first(_texts):
            state["seen"] += 1
            return state["seen"] == 1

        client = _RecordingClient(fail_predicate=fail_first)
        svc = _service_with(client)
        with patch.object(es.settings, "openai_api_key", "sk-test"):
            out = _run(svc._openai_embed_batch(oversized))

        self.assertEqual(len(out), len(oversized))
        self.assertTrue(any(v is None for v in out), "failing group should yield Nones")
        self.assertTrue(any(v is not None for v in out),
                        "a single rejected group must not null out the whole batch")

    def test_no_api_key_returns_aligned_nones(self):
        client = _RecordingClient()
        svc = _service_with(client)
        with patch.object(es.settings, "openai_api_key", ""):
            out = _run(svc._openai_embed_batch(["a", "b", "c"]))
        self.assertEqual(out, [None, None, None])
        self.assertEqual(client.requests, [])


class ProductionBudgetSanityTests(unittest.TestCase):
    """The configured budgets must actually respect the documented API limits."""

    def test_byte_budget_is_below_the_token_limit(self):
        self.assertLessEqual(es._OPENAI_MAX_BYTES_PER_REQUEST, API_MAX_TOKENS_PER_REQUEST)

    def test_input_budget_is_at_most_the_array_limit(self):
        self.assertLessEqual(es._OPENAI_MAX_INPUTS_PER_REQUEST, API_MAX_INPUTS_PER_REQUEST)


if __name__ == "__main__":
    unittest.main()
