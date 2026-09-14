"""Tests for the OpenAI embedding fallback staying under the per-request limit.

The API rejects any /embeddings call above 300k tokens with HTTP 400
(`max_tokens_per_request`). Callers hand over unbounded batches (the vault
indexer passes every chunk of a file at once), so the fallback has to split.
Without splitting, one oversized batch nulls out every vector in it and the
affected vault rows stay permanently unvectorised.

The HTTP client is the test seam: `_get_openai_client` is monkeypatched with a
recorder that captures the `input` list of each POST.
"""

import asyncio
import unittest
from unittest.mock import patch

from app.services import embedding_service as es
from app.services.embedding_service import (
    EMBEDDING_DIM,
    EmbeddingService,
    split_by_char_budget,
)


def _run(coro):
    return asyncio.run(coro)


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class _RecordingClient:
    """Records every POST body and answers with well-formed embeddings.

    ``fail_predicate`` lets a single sub-batch return HTTP 400 so the blast
    radius of one rejected group can be observed.
    """

    def __init__(self, fail_predicate=None):
        self.requests: list[list[str]] = []
        self._fail_predicate = fail_predicate

    async def post(self, url, json=None, **kwargs):
        texts = json["input"]
        self.requests.append(texts)
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


class SplitByCharBudgetTests(unittest.TestCase):
    def test_every_group_fits_the_budget(self):
        texts = ["a" * 30 for _ in range(10)]
        groups = split_by_char_budget(texts, 100)
        for g in groups:
            self.assertLessEqual(sum(len(t) for t in g), 100)

    def test_order_and_completeness_preserved(self):
        texts = [f"text-{i}" * 5 for i in range(25)]
        groups = split_by_char_budget(texts, 40)
        flat = [t for g in groups for t in g]
        self.assertEqual(flat, texts)

    def test_oversized_single_text_is_emitted_alone_not_dropped(self):
        big = "x" * 500
        groups = split_by_char_budget(["a", big, "b"], 100)
        flat = [t for g in groups for t in g]
        self.assertEqual(flat, ["a", big, "b"])
        self.assertIn([big], groups)

    def test_empty_input(self):
        self.assertEqual(split_by_char_budget([], 100), [])


class OpenAIBatchSplittingTests(unittest.TestCase):
    """An oversized batch must become several requests, each under the limit."""

    # 60 texts at the caller's per-text cap — the shape seen in the incident
    # (479k tokens requested in one call).
    OVERSIZED = [("y" * (es.MAX_INPUT_LENGTH * 4)) for _ in range(60)]

    def test_oversized_batch_is_split_into_several_requests(self):
        client = _RecordingClient()
        svc = _service_with(client)
        with patch.object(es.settings, "openai_api_key", "sk-test"):
            _run(svc._openai_embed_batch(self.OVERSIZED))
        self.assertGreater(len(client.requests), 1)

    def test_no_request_exceeds_the_api_token_limit(self):
        client = _RecordingClient()
        svc = _service_with(client)
        with patch.object(es.settings, "openai_api_key", "sk-test"):
            _run(svc._openai_embed_batch(self.OVERSIZED))
        for req in client.requests:
            est_tokens = sum(len(t) for t in req) / es._OPENAI_CHARS_PER_TOKEN
            self.assertLess(est_tokens, 300_000)

    def test_all_vectors_returned_aligned_to_input(self):
        client = _RecordingClient()
        svc = _service_with(client)
        with patch.object(es.settings, "openai_api_key", "sk-test"):
            out = _run(svc._openai_embed_batch(self.OVERSIZED))
        self.assertEqual(len(out), len(self.OVERSIZED))
        self.assertTrue(all(v is not None for v in out))
        self.assertTrue(all(len(v) == EMBEDDING_DIM for v in out))

    def test_texts_are_sent_once_each_in_order(self):
        client = _RecordingClient()
        svc = _service_with(client)
        with patch.object(es.settings, "openai_api_key", "sk-test"):
            _run(svc._openai_embed_batch(self.OVERSIZED))
        sent = [t for req in client.requests for t in req]
        self.assertEqual(len(sent), len(self.OVERSIZED))

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


if __name__ == "__main__":
    unittest.main()
