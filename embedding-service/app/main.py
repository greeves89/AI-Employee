"""Local embedding service using BAAI/bge-m3.

Why bge-m3:
- MTEB score 68.8 (beats OpenAI text-embedding-3-small at 62.3)
- Multilingual: 100+ languages including German, English, French, Spanish, etc.
- 1024-dimensional dense vectors
- Apache 2.0 licensed, no API costs, runs entirely offline

The model is pre-downloaded into the image during build (~2.3 GB).
"""

import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

MODEL_NAME = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")
MODEL_CACHE_DIR = os.getenv("MODEL_CACHE_DIR", "/models")
MAX_BATCH_SIZE = int(os.getenv("MAX_BATCH_SIZE", "64"))
MAX_INPUT_LENGTH = int(os.getenv("MAX_INPUT_LENGTH", "8192"))  # chars

# Singleton model (loaded at startup)
_model = None
_load_time_seconds = 0.0


@asynccontextmanager
async def lifespan(_: FastAPI):
    global _model, _load_time_seconds
    logger.info(f"Loading embedding model '{MODEL_NAME}' from {MODEL_CACHE_DIR}...")
    start = time.monotonic()
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
        vec = _model.encode(
            text,
            normalize_embeddings=body.normalize,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        return EmbedResponse(
            embedding=vec.tolist(),
            dimension=len(vec),
            model=MODEL_NAME,
        )
    except Exception as e:
        logger.exception("Embed failed")
        raise HTTPException(500, f"Embedding failed: {e}")


@app.post("/embed/batch", response_model=EmbedBatchResponse)
async def embed_batch(body: EmbedBatchRequest):
    """Embed multiple texts at once (much faster than individual calls)."""
    if _model is None:
        raise HTTPException(503, "Model still loading")
    if len(body.texts) > MAX_BATCH_SIZE:
        raise HTTPException(400, f"Max {MAX_BATCH_SIZE} texts per batch")
    texts = [t[:MAX_INPUT_LENGTH] for t in body.texts]
    try:
        vecs = _model.encode(
            texts,
            normalize_embeddings=body.normalize,
            show_progress_bar=False,
            convert_to_numpy=True,
            batch_size=32,
        )
        return EmbedBatchResponse(
            embeddings=[v.tolist() for v in vecs],
            dimension=len(vecs[0]) if len(vecs) > 0 else 0,
            model=MODEL_NAME,
            count=len(vecs),
        )
    except Exception as e:
        logger.exception("Batch embed failed")
        raise HTTPException(500, f"Batch embedding failed: {e}")
