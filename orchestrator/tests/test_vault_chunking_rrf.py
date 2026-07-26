"""Tests for the pure chunking and RRF helpers (hybrid vault search)."""
from app.core.chunking import chunk_markdown, TARGET_CHARS, MAX_CHARS
from app.core.rrf import reciprocal_rank_fusion


class TestChunkMarkdown:
    def test_empty(self):
        assert chunk_markdown("") == []
        assert chunk_markdown("   \n  \n") == []

    def test_single_paragraph(self):
        chunks = chunk_markdown("Ein einfacher Absatz über Drucker.")
        assert len(chunks) == 1
        assert "Drucker" in chunks[0].content
        assert chunks[0].index == 0

    def test_heading_becomes_context(self):
        md = "# Drucker\n\n## Fehlercodes\n\nDer Code x17137 bedeutet Papierstau."
        chunks = chunk_markdown(md)
        assert len(chunks) == 1
        assert chunks[0].heading == "Drucker > Fehlercodes"
        assert "x17137" in chunks[0].content
        assert "Drucker > Fehlercodes" in chunks[0].embed_text

    def test_heading_stack_pops_siblings(self):
        md = (
            "# A\n\n## B\n\n" + "Inhalt B. " * 60 + "\n\n## C\n\n" + "Inhalt C. " * 60
        )
        chunks = chunk_markdown(md)
        headings = {c.heading for c in chunks}
        assert "A > B" in headings
        assert "A > C" in headings

    def test_code_block_not_split(self):
        code = "```python\n" + "\n".join(f"line_{i} = {i}" for i in range(80)) + "\n```"
        md = f"# Code\n\n{code}"
        chunks = chunk_markdown(md)
        joined = "\n".join(c.content for c in chunks)
        assert "```python" in joined
        # every chunk must have balanced fence markers
        for c in chunks:
            assert c.content.count("```") % 2 == 0 or "```" not in c.content

    def test_large_code_fence_not_split(self):
        # A code fence longer than MAX_CHARS must be emitted as one chunk — never hard-split.
        long_code = "```python\n" + "\n".join(f"result_{i} = i * {i}" for i in range(120)) + "\n```"
        assert len(long_code) > MAX_CHARS
        md = f"# Big Fence\n\n{long_code}"
        chunks = chunk_markdown(md)
        fence_chunks = [c for c in chunks if "```" in c.content]
        # The entire fence must appear in exactly one chunk with balanced markers.
        assert len(fence_chunks) == 1
        assert fence_chunks[0].content.count("```") % 2 == 0

    def test_large_document_splits(self):
        md = "# Big\n\n" + ("Ein Absatz mit Text. " * 20 + "\n\n") * 20
        chunks = chunk_markdown(md)
        assert len(chunks) > 1
        for c in chunks:
            assert len(c.content) <= MAX_CHARS

    def test_deterministic(self):
        md = "# T\n\n" + ("Absatz. " * 30 + "\n\n") * 10
        a = chunk_markdown(md)
        b = chunk_markdown(md)
        assert [c.content for c in a] == [c.content for c in b]

    def test_indices_sequential(self):
        md = "# T\n\n" + ("Absatz Text hier. " * 30 + "\n\n") * 8
        chunks = chunk_markdown(md)
        assert [c.index for c in chunks] == list(range(len(chunks)))


class TestRRF:
    def test_empty(self):
        assert reciprocal_rank_fusion([]) == []
        assert reciprocal_rank_fusion([[], []]) == []

    def test_single_list_preserves_order(self):
        res = reciprocal_rank_fusion([["a", "b", "c"]])
        assert [k for k, _ in res] == ["a", "b", "c"]

    def test_agreement_boosts_item(self):
        # "b" appears high in both lists -> should win
        res = reciprocal_rank_fusion([["a", "b", "c"], ["b", "d", "e"]])
        assert res[0][0] == "b"

    def test_scores_descending(self):
        res = reciprocal_rank_fusion([["a", "b"], ["b", "a"]])
        scores = [s for _, s in res]
        assert scores == sorted(scores, reverse=True)

    def test_weights_shift_ranking(self):
        lists = [["x", "y"], ["y", "x"]]
        weighted = reciprocal_rank_fusion(lists, weights=[5.0, 1.0])
        assert weighted[0][0] == "x"

    def test_k_constant(self):
        res = reciprocal_rank_fusion([["a"]], k=60)
        assert abs(res[0][1] - (1.0 / 60)) < 1e-9

    def test_weights_length_mismatch_raises(self):
        try:
            reciprocal_rank_fusion([["a"]], weights=[1.0, 2.0])
            assert False, "expected ValueError"
        except ValueError:
            pass
