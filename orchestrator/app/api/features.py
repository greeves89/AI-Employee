"""Feature flags API -- read flags (public), manage flags (admin-only)."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.dependencies import require_admin, require_auth
from app.services.feature_flag_service import FeatureFlagService
from app.services.redis_service import RedisService

router = APIRouter(prefix="/features", tags=["features"])


def _get_service(request: Request) -> FeatureFlagService:
    redis: RedisService = request.app.state.redis
    return FeatureFlagService(redis)


class FeatureFlagRequest(BaseModel):
    enabled: bool = True
    rollout_pct: int = 100
    description: str = ""


@router.get("/")
async def list_feature_flags(
    user=Depends(require_auth),
    service: FeatureFlagService = Depends(_get_service),
):
    """List all feature flags."""
    return {"flags": await service.list_flags()}


@router.get("/{name}")
async def check_feature_flag(
    name: str,
    user=Depends(require_auth),
    service: FeatureFlagService = Depends(_get_service),
):
    """Check if a specific feature flag is enabled for the current user."""
    enabled = await service.is_enabled(name, user_id=str(user.id))
    return {"name": name, "enabled": enabled}


@router.put("/{name}")
async def set_feature_flag(
    name: str,
    body: FeatureFlagRequest,
    user=Depends(require_admin),
    service: FeatureFlagService = Depends(_get_service),
):
    """Create or update a feature flag (admin only)."""
    flag = await service.set_flag(
        name=name,
        enabled=body.enabled,
        rollout_pct=body.rollout_pct,
        description=body.description,
    )
    return flag


@router.delete("/{name}")
async def delete_feature_flag(
    name: str,
    user=Depends(require_admin),
    service: FeatureFlagService = Depends(_get_service),
):
    """Delete a feature flag (admin only)."""
    deleted = await service.delete_flag(name)
    if not deleted:
        raise HTTPException(status_code=404, detail="Flag not found")
    return {"ok": True}
