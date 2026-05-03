"""Client for the local embedding service (BAAI/bge-m3, 1024 dimensions)."""

import logging
import math

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = 10.0


async def get_embedding(text: str) -> list[float] | None:
    """Get embedding vector for a single text. Returns None on failure."""
    url = settings.embedding_service_url
    if not url:
        return None
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(f"{url}/embed", json={"text": text})
            resp.raise_for_status()
            return resp.json()["embedding"]
    except Exception as e:
        logger.warning(f"Embedding service call failed: {e}")
        return None


async def get_embeddings_batch(texts: list[str]) -> list[list[float]] | None:
    """Get embeddings for multiple texts at once. Returns None on failure."""
    url = settings.embedding_service_url
    if not url:
        return None
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT * 2) as client:
            resp = await client.post(f"{url}/embed/batch", json={"texts": texts})
            resp.raise_for_status()
            return resp.json()["embeddings"]
    except Exception as e:
        logger.warning(f"Embedding service batch call failed: {e}")
        return None


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def skill_embedding_text(name: str, description: str, content: str) -> str:
    """Build the text to embed for a skill (name + description + first 500 chars of content)."""
    parts = [name]
    if description:
        parts.append(description)
    if content:
        parts.append(content[:500])
    return " | ".join(parts)
