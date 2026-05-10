"""Embedding service — local-first with cloud fallback.

Strategy:
1. LOCAL (preferred): call the embedding-service container (BAAI/bge-m3, 1024 dim)
2. OpenAI fallback: only if local is unreachable AND openai_api_key is set
3. Disabled: if neither is available, semantic search falls back to keyword

The local service runs completely offline after the first model download,
is multilingual (100+ languages), DSGVO-compliant, and free. It outscores
OpenAI text-embedding-3-small on the MTEB benchmark (68.8 vs 62.3).
"""

import logging
import os
import time
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


# Configurable via env (override default container DNS)
LOCAL_SERVICE_URL = os.environ.get("EMBEDDING_SERVICE_URL", "http://embedding-service:8001")
EMBEDDING_DIM = 1024  # bge-m3 output dimension
MAX_INPUT_LENGTH = 8000
_AVAILABILITY_TTL = 30.0  # seconds — re-check service health every 30s
_HEALTH_TIMEOUT = 5.0  # bge-m3 boot takes ~10s, give it a chance


class EmbeddingService:
    """Generates vector embeddings. Thread-safe, reusable.

    Health is re-checked every _AVAILABILITY_TTL seconds, so a transient
    failure (boot, restart, network hiccup) recovers automatically without
    requiring an orchestrator restart.
    """

    def __init__(self):
        self._local_client: httpx.AsyncClient | None = None
        self._openai_client: httpx.AsyncClient | None = None
        self._local_available: bool | None = None
        self._local_checked_at: float = 0.0
        self._fallback_count: int = 0  # how many times we silently degraded
        self._success_count: int = 0

    @property
    def enabled(self) -> bool:
        """We always try the service; caller handles None as fallback signal."""
        return True

    @property
    def stats(self) -> dict:
        """Diagnostic stats — useful for /health endpoints."""
        return {
            "local_available": self._local_available,
            "last_checked_seconds_ago": round(time.time() - self._local_checked_at, 1) if self._local_checked_at else None,
            "successes": self._success_count,
            "fallbacks": self._fallback_count,
            "service_url": LOCAL_SERVICE_URL,
        }

    async def _check_local_available(self) -> bool:
        """Health-check with TTL. Re-verifies every _AVAILABILITY_TTL seconds."""
        now = time.time()
        if self._local_available is not None and (now - self._local_checked_at) < _AVAILABILITY_TTL:
            return self._local_available

        try:
            client = await self._get_local_client()
            resp = await client.get("/healthz", timeout=_HEALTH_TIMEOUT)
            new_state = resp.status_code == 200
            # Log only on state transitions to avoid spam
            if new_state != self._local_available:
                if new_state:
                    logger.info(f"[Embedding] Local service UP at {LOCAL_SERVICE_URL} (bge-m3)")
                else:
                    logger.warning(f"[Embedding] Local service returned HTTP {resp.status_code} — semantic search will fall back to keyword")
            self._local_available = new_state
        except Exception as e:
            if self._local_available is not False:
                logger.warning(f"[Embedding] Local service unreachable at {LOCAL_SERVICE_URL}: {e} — semantic search will fall back to keyword (will retry in {_AVAILABILITY_TTL}s)")
            self._local_available = False
        finally:
            self._local_checked_at = now
        return self._local_available

    async def _get_local_client(self) -> httpx.AsyncClient:
        if self._local_client is None:
            # Generous timeout because CPU inference can be slow on big batches
            self._local_client = httpx.AsyncClient(
                timeout=httpx.Timeout(300.0, connect=5.0),
                base_url=LOCAL_SERVICE_URL,
            )
        return self._local_client

    async def _get_openai_client(self) -> httpx.AsyncClient:
        if self._openai_client is None:
            self._openai_client = httpx.AsyncClient(
                timeout=30.0,
                base_url="https://api.openai.com/v1",
                headers={
                    "Authorization": f"Bearer {settings.openai_api_key}",
                    "Content-Type": "application/json",
                },
            )
        return self._openai_client

    async def embed(self, text: str) -> Optional[list[float]]:
        """Generate an embedding vector for a single text string.

        Returns a 1024-dim vector from the local service, or None if everything failed.
        """
        if not text or not text.strip():
            return None
        text = text[:MAX_INPUT_LENGTH * 4]

        # 1. Try local service
        if await self._check_local_available():
            try:
                client = await self._get_local_client()
                resp = await client.post("/embed", json={"text": text})
                if resp.status_code == 200:
                    self._success_count += 1
                    return resp.json()["embedding"]
                else:
                    logger.warning(f"[Embedding] Local service returned {resp.status_code}")
            except Exception as e:
                logger.warning(f"[Embedding] Local call failed: {e}")
                self._local_available = None  # force re-check next call

        # 2. Cloud fallback would require dim conversion — skip for now.
        # We've committed to 1024-dim column in the DB, so we can't use OpenAI's 1536.
        self._fallback_count += 1
        return None

    async def embed_batch(self, texts: list[str]) -> list[Optional[list[float]]]:
        """Batch-embed multiple texts."""
        if not texts:
            return []
        cleaned: list[tuple[int, str]] = []
        for i, t in enumerate(texts):
            if t and t.strip():
                cleaned.append((i, t[:MAX_INPUT_LENGTH * 4]))
        if not cleaned:
            return [None] * len(texts)

        result: list[Optional[list[float]]] = [None] * len(texts)

        if await self._check_local_available():
            try:
                client = await self._get_local_client()
                # Smaller chunks (16) keep each call under ~60s on CPU
                CHUNK_SIZE = 16
                for chunk_start in range(0, len(cleaned), CHUNK_SIZE):
                    chunk = cleaned[chunk_start:chunk_start + CHUNK_SIZE]
                    try:
                        resp = await client.post(
                            "/embed/batch",
                            json={"texts": [t for _, t in chunk]},
                        )
                        if resp.status_code != 200:
                            logger.warning(f"[Embedding] Batch chunk {chunk_start} returned {resp.status_code}")
                            continue
                        embeddings = resp.json()["embeddings"]
                        for (orig_idx, _), emb in zip(chunk, embeddings):
                            result[orig_idx] = emb
                    except Exception as chunk_err:
                        logger.warning(f"[Embedding] Batch chunk {chunk_start}/{len(cleaned)} failed: {chunk_err}")
                        # Fall back to single embeds for this chunk
                        for orig_idx, text in chunk:
                            try:
                                single_resp = await client.post("/embed", json={"text": text})
                                if single_resp.status_code == 200:
                                    result[orig_idx] = single_resp.json()["embedding"]
                            except Exception:
                                pass
                return result
            except Exception as e:
                logger.warning(f"[Embedding] Batch local failed: {e}")
                self._local_available = None

        return result

    async def close(self) -> None:
        if self._local_client is not None:
            await self._local_client.aclose()
            self._local_client = None
        if self._openai_client is not None:
            await self._openai_client.aclose()
            self._openai_client = None


# Module-level singleton
_instance: EmbeddingService | None = None


def get_embedding_service() -> EmbeddingService:
    global _instance
    if _instance is None:
        _instance = EmbeddingService()
    return _instance
