"""Embedding service for semantic memory + knowledge search.

Uses OpenAI text-embedding-3-small (1536 dims, $0.02/1M tokens — very cheap).
Falls back gracefully if OpenAI API key is not configured.
"""

import asyncio
import logging
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 1536
MAX_INPUT_LENGTH = 8000  # ~32k chars, leaves headroom for tokenizer expansion


class EmbeddingService:
    """Generates vector embeddings for text. Thread-safe, reusable."""

    def __init__(self):
        self._client: httpx.AsyncClient | None = None
        self._enabled: bool | None = None

    @property
    def enabled(self) -> bool:
        """Check if embeddings are available (requires OpenAI API key)."""
        if self._enabled is None:
            self._enabled = bool(settings.openai_api_key)
        return self._enabled

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                base_url="https://api.openai.com/v1",
                headers={
                    "Authorization": f"Bearer {settings.openai_api_key}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def embed(self, text: str) -> Optional[list[float]]:
        """Generate an embedding vector for a single text string.

        Returns None if the service is disabled or the call fails.
        """
        if not self.enabled:
            return None
        if not text or not text.strip():
            return None

        # Truncate long inputs
        text = text[:MAX_INPUT_LENGTH * 4]

        try:
            client = await self._get_client()
            resp = await client.post(
                "/embeddings",
                json={"model": EMBEDDING_MODEL, "input": text},
            )
            if resp.status_code != 200:
                logger.warning(f"Embedding API returned {resp.status_code}: {resp.text[:200]}")
                return None
            data = resp.json()
            return data["data"][0]["embedding"]
        except Exception as e:
            logger.warning(f"Embedding generation failed: {e}")
            return None

    async def embed_batch(self, texts: list[str]) -> list[Optional[list[float]]]:
        """Batch-embed up to 2048 texts in one API call (cheaper + faster)."""
        if not self.enabled:
            return [None] * len(texts)
        if not texts:
            return []

        # Filter out empty texts (track indices for reassembly)
        cleaned: list[tuple[int, str]] = []
        for i, t in enumerate(texts):
            if t and t.strip():
                cleaned.append((i, t[:MAX_INPUT_LENGTH * 4]))

        if not cleaned:
            return [None] * len(texts)

        try:
            client = await self._get_client()
            resp = await client.post(
                "/embeddings",
                json={
                    "model": EMBEDDING_MODEL,
                    "input": [t for _, t in cleaned],
                },
            )
            if resp.status_code != 200:
                logger.warning(f"Batch embedding API returned {resp.status_code}")
                return [None] * len(texts)
            data = resp.json()
            embeddings = [item["embedding"] for item in data["data"]]
            # Reassemble in original order
            result: list[Optional[list[float]]] = [None] * len(texts)
            for (orig_idx, _), emb in zip(cleaned, embeddings):
                result[orig_idx] = emb
            return result
        except Exception as e:
            logger.warning(f"Batch embedding failed: {e}")
            return [None] * len(texts)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


# Module-level singleton
_instance: EmbeddingService | None = None


def get_embedding_service() -> EmbeddingService:
    global _instance
    if _instance is None:
        _instance = EmbeddingService()
    return _instance
