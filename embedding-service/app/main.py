"""Local embedding service using BAAI/bge-m3.

Why bge-m3:
- MTEB score 68.8 (beats OpenAI text-embedding-3-small at 62.3)
- Multilingual: 100+ languages including German, English, French, Spanish, etc.
- 1024-dimensional dense vectors
- Apache 2.0 licensed, no API costs, runs entirely offline

The model is pre-downloaded into the image during build (~2.3 GB).
"""

import asyncio
import logging
import os
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

MODEL_NAME = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")
MODEL_CACHE_DIR = os.getenv("MODEL_CACHE_DIR", "/models")
MAX_BATCH_SIZE = int(os.getenv("MAX_BATCH_SIZE", "64"))
MAX_INPUT_LENGTH = int(os.getenv("MAX_INPUT_LENGTH", "8192"))  # chars
# Texts per model call inside one /embed/batch request. Between two sub-batches
# the handler checks whether the caller is still there (issue #826).
SUB_BATCH_SIZE = int(os.getenv("EMBEDDING_SUB_BATCH_SIZE", "16"))
# CPU threads torch may use. Default leaves one core for the rest of the
# platform: on a 2-core host an unbounded torch starved the orchestrator and
# pinned the host at 85-88 C (issue #826). Override with EMBEDDING_TORCH_THREADS.
TORCH_THREADS = int(os.getenv("EMBEDDING_TORCH_THREADS", "0")) or max(1, (os.cpu_count() or 2) - 1)

# Singleton model (loaded at startup)
_model = None
_load_time_seconds = 0.0
# Encoding is CPU-bound and runs in the threadpool so the event loop (and
# /healthz) stays responsive. One encode at a time: two parallel model calls
# only fight over the same cores.
_encode_slot: asyncio.Semaphore | None = None
_encode_slot_loop: asyncio.AbstractEventLoop | None = None


def _slot() -> asyncio.Semaphore:
    """Semaphore bound to the running loop (created lazily, re-created if the
    loop changes -- a module-level Semaphore binds to the first loop it sees)."""
    global _encode_slot, _encode_slot_loop
    loop = asyncio.get_running_loop()
    if _encode_slot is None or _encode_slot_loop is not loop:
        _encode_slot = asyncio.Semaphore(1)
        _encode_slot_loop = loop
    return _encode_slot


class ClientGone(Exception):
    """The caller disconnected while a batch was still being computed."""


async def _encode_in_threadpool(texts, normalize: bool):
    async with _slot():
        return await run_in_threadpool(
            _model.encode,
            texts,
            normalize_embeddings=normalize,
            show_progress_bar=False,
            convert_to_numpy=True,
            batch_size=32,
        )


async def _encode_batch_abortable(
    texts: list[str],
    normalize: bool,
    is_disconnected: Callable[[], Awaitable[bool]],
) -> list:
    """Encode in sub-batches, stopping as soon as the caller is gone.

    Before #826 a batch kept computing for the full duration after the
    orchestrator had already timed out and logged the chunk as failed -- nobody
    was waiting for the result, the CPU was pinned for over an hour anyway.
    """
    vecs: list = []
    for start in range(0, len(texts), SUB_BATCH_SIZE):
        if start and await is_disconnected():
            raise ClientGone(f"client gone after {start}/{len(texts)} texts")
        chunk = await _encode_in_threadpool(texts[start:start + SUB_BATCH_SIZE], normalize)
        vecs.extend(chunk)
    return vecs


@asynccontextmanager
async def lifespan(_: FastAPI):
    global _model, _load_time_seconds
    logger.info(f"Loading embedding model '{MODEL_NAME}' from {MODEL_CACHE_DIR}...")
    start = time.monotonic()
    try:
        import torch
        torch.set_num_threads(TORCH_THREADS)
        logger.info(f"torch threads limited to {TORCH_THREADS}")
    except (ImportError, RuntimeError) as e:  # torch missing only in unit tests
        logger.warning(f"Could not limit torch threads: {e}")
    from sentence_transformers import SentenceTransformer
    _model = SentenceTransformer(MODEL_NAME, cache_folder=MODEL_CACHE_DIR)
    _load_time_seconds = time.monotonic() - start
    logger.info(
        f"Model loaded in {_load_time_seconds:.1f}s. "
        f"Dimension: {_model.get_sentence_embedding_dimension()}"
    )
    yield
    logger.info("Shutting down embedding service")


app = FastAPI(
    title="AI-Employee Embedding Service",
    version="1.0.0",
    description="Local multilingual embeddings via BAAI/bge-m3",
    lifespan=lifespan,
)


class EmbedRequest(BaseModel):
    text: str = Field(..., min_length=1)
    normalize: bool = Field(True, description="L2-normalize the vector (default: true)")


class EmbedBatchRequest(BaseModel):
    texts: list[str] = Field(..., min_length=1, max_length=MAX_BATCH_SIZE)
    normalize: bool = Field(True)


class EmbedResponse(BaseModel):
    embedding: list[float]
    dimension: int
    model: str


class EmbedBatchResponse(BaseModel):
    embeddings: list[list[float]]
    dimension: int
    model: str
    count: int


@app.get("/healthz")
async def healthz():
    """Health check — returns 200 only after model is loaded."""
    if _model is None:
        raise HTTPException(503, "Model still loading")
    return {
        "status": "ok",
        "model": MODEL_NAME,
        "dimension": _model.get_sentence_embedding_dimension(),
        "load_time_seconds": round(_load_time_seconds, 2),
    }


@app.get("/info")
async def info():
    """Return model info."""
    if _model is None:
        raise HTTPException(503, "Model still loading")
    return {
        "model": MODEL_NAME,
        "dimension": _model.get_sentence_embedding_dimension(),
        "max_input_length_chars": MAX_INPUT_LENGTH,
        "max_batch_size": MAX_BATCH_SIZE,
    }


@app.post("/embed", response_model=EmbedResponse)
async def embed(body: EmbedRequest):
    """Embed a single text into a dense vector."""
    if _model is None:
        raise HTTPException(503, "Model still loading")
    text = body.text[:MAX_INPUT_LENGTH]
    try:
        vec = (await _encode_in_threadpool([text], body.normalize))[0]
        return EmbedResponse(
            embedding=vec.tolist(),
            dimension=len(vec),
            model=MODEL_NAME,
        )
    except Exception as e:
        logger.exception("Embed failed")
        raise HTTPException(500, f"Embedding failed: {e}")


@app.post("/embed/batch", response_model=EmbedBatchResponse)
async def embed_batch(body: EmbedBatchRequest, request: Request):
    """Embed multiple texts at once (much faster than individual calls)."""
    if _model is None:
        raise HTTPException(503, "Model still loading")
    if len(body.texts) > MAX_BATCH_SIZE:
        raise HTTPException(400, f"Max {MAX_BATCH_SIZE} texts per batch")
    texts = [t[:MAX_INPUT_LENGTH] for t in body.texts]
    try:
        vecs = await _encode_batch_abortable(texts, body.normalize, request.is_disconnected)
        return EmbedBatchResponse(
            embeddings=[v.tolist() for v in vecs],
            dimension=len(vecs[0]) if len(vecs) > 0 else 0,
            model=MODEL_NAME,
            count=len(vecs),
        )
    except ClientGone as e:
        # Nobody is listening any more; the response is dropped by the server.
        logger.warning(f"Batch embed aborted: {e}")
        raise HTTPException(499, "Client closed request")
    except Exception as e:
        logger.exception("Batch embed failed")
        raise HTTPException(500, f"Batch embedding failed: {e}")
