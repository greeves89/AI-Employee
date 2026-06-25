"""Observability API — admin-only read access to LLM traces (Langfuse).

Verzahnt: the orchestrator is the only thing holding the Langfuse secret key, so
the browser never talks to Langfuse directly. These endpoints proxy the Langfuse
public read API behind the existing admin guard. All writes happen implicitly via
the task-completion trace hook (see services/observability_service.py).
"""
import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Query

from app.config import settings
from app.dependencies import require_auth
from app.models.user import UserRole
from app.services.observability_service import observability

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/observability", tags=["observability"])

# Trace ids are task ids (opaque short strings). Restrict to a safe charset so a
# crafted id can't traverse paths or inject query params into the upstream call.
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


async def _require_admin(user=Depends(require_auth)):
    if user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Admin only")
    return user


def _require_enabled() -> None:
    if not observability.enabled:
        raise HTTPException(status_code=503, detail="Observability (Langfuse) is not configured")


@router.get("/config")
async def get_observability_config(user=Depends(_require_admin)):
    """Whether tracing is active + deep-link base for the admin UI."""
    return {
        "enabled": observability.enabled,
        "public_url": settings.langfuse_public_url or None,
        "project_id": settings.langfuse_project_id,
    }


@router.get("/traces")
async def list_traces(
    user=Depends(_require_admin),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    user_id: str | None = Query(None),
    tags: str | None = Query(None),
):
    """Proxy: paginated trace list from Langfuse (admin-only)."""
    _require_enabled()
    try:
        status, body = await observability.api_get(
            "/api/public/traces",
            {"page": page, "limit": limit, "userId": user_id, "tags": tags},
        )
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Observability (Langfuse) is not configured")
    if status >= 400:
        raise HTTPException(status_code=502, detail="Langfuse API error")
    return body


@router.get("/traces/{trace_id}")
async def get_trace(trace_id: str, user=Depends(_require_admin)):
    """Proxy: single trace detail from Langfuse (admin-only)."""
    _require_enabled()
    if not _SAFE_ID.match(trace_id):
        raise HTTPException(status_code=400, detail="Invalid trace_id format")
    try:
        status, body = await observability.api_get(f"/api/public/traces/{trace_id}")
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Observability (Langfuse) is not configured")
    if status == 404:
        raise HTTPException(status_code=404, detail="Trace not found")
    if status >= 400:
        raise HTTPException(status_code=502, detail="Langfuse API error")
    # Enrich with a browser deep-link for the UI.
    deep_link = observability.trace_url(trace_id)
    if isinstance(body, dict) and deep_link:
        body["_deepLink"] = deep_link
    return body


@router.get("/metrics/daily")
async def daily_metrics(
    user=Depends(_require_admin),
    days: int = Query(14, ge=1, le=90),
):
    """Proxy: daily cost/usage metrics from Langfuse (admin-only)."""
    _require_enabled()
    try:
        status, body = await observability.api_get(
            "/api/public/metrics/daily", {"limit": days}
        )
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Observability (Langfuse) is not configured")
    if status >= 400:
        raise HTTPException(status_code=502, detail="Langfuse API error")
    return body
