"""Tests for semantic skill search (embedding_client helpers + backfill)."""
import math
from unittest.mock import AsyncMock, patch

import pytest

from app.services.embedding_client import skill_embedding_text, cosine_similarity


class TestSkillEmbeddingText:
    def test_name_only(self):
        assert skill_embedding_text("deploy", "", "") == "deploy"

    def test_name_and_description(self):
        result = skill_embedding_text("deploy", "CI/CD pipeline setup", "")
        assert result == "deploy | CI/CD pipeline setup"

    def test_full(self):
        result = skill_embedding_text("deploy", "pipeline", "step 1 do this")
        assert result == "deploy | pipeline | step 1 do this"

    def test_content_truncated(self):
        long_content = "x" * 1000
        result = skill_embedding_text("n", "d", long_content)
        content_part = result.split(" | ")[-1]
        assert len(content_part) == 500


class TestCosineSimilarity:
    def test_identical_vectors(self):
        v = [1.0, 0.0, 0.0]
        assert cosine_similarity(v, v) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        a = [1.0, 0.0, 0.0]
        b = [0.0, 1.0, 0.0]
        assert cosine_similarity(a, b) == pytest.approx(0.0)

    def test_opposite_vectors(self):
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        assert cosine_similarity(a, b) == pytest.approx(-1.0)

    def test_zero_vector(self):
        assert cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0

    def test_non_unit_vectors(self):
        a = [3.0, 4.0]
        b = [4.0, 3.0]
        expected = (12 + 12) / (5.0 * 5.0)
        assert cosine_similarity(a, b) == pytest.approx(expected)


def _make_mock_db(select_rows):
    """Create a mock async DB session with proper chaining for raw SQL queries."""
    from unittest.mock import MagicMock

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.mappings.return_value.all.return_value = select_rows

    call_count = [0]
    original_execute = mock_db.execute

    async def execute_side_effect(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            return mock_result
        return MagicMock()

    mock_db.execute = AsyncMock(side_effect=execute_side_effect)
    return mock_db


class TestBackfillSkillEmbeddings:
    @pytest.mark.asyncio
    async def test_backfill_processes_skills_without_embedding(self):
        from app.services.embedding_backfill import backfill_skill_embeddings

        fake_rows = [
            {"id": 1, "name": "deploy", "description": "CI/CD", "content": "steps..."},
            {"id": 2, "name": "testing", "description": "unit tests", "content": "pytest..."},
        ]
        mock_db = _make_mock_db(fake_rows)

        with patch("app.services.embedding_backfill.get_embedding_service") as mock_svc:
            mock_svc.return_value.embed_batch = AsyncMock(
                return_value=[[0.1] * 1024, [0.2] * 1024]
            )
            count = await backfill_skill_embeddings(mock_db, limit=16)

        assert count == 2
        assert mock_db.execute.call_count == 3
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_backfill_returns_zero_when_no_skills(self):
        from app.services.embedding_backfill import backfill_skill_embeddings

        mock_db = _make_mock_db([])

        with patch("app.services.embedding_backfill.get_embedding_service"):
            count = await backfill_skill_embeddings(mock_db, limit=16)

        assert count == 0

    @pytest.mark.asyncio
    async def test_backfill_skips_failed_embeddings(self):
        from app.services.embedding_backfill import backfill_skill_embeddings

        fake_rows = [
            {"id": 1, "name": "a", "description": "b", "content": "c"},
            {"id": 2, "name": "d", "description": "e", "content": "f"},
        ]
        mock_db = _make_mock_db(fake_rows)

        with patch("app.services.embedding_backfill.get_embedding_service") as mock_svc:
            mock_svc.return_value.embed_batch = AsyncMock(
                return_value=[[0.1] * 1024, None]
            )
            count = await backfill_skill_embeddings(mock_db, limit=16)

        assert count == 1
