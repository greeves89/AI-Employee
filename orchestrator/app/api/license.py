"""License API — check current license status, apply new licenses."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.license import get_current_license, load_license_from_string
from app.db.session import get_db
from app.dependencies import require_admin, require_auth
from app.models.platform_settings import PlatformSettings
from app.services.settings_service import SettingsService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/license", tags=["license"])


class ApplyLicenseRequest(BaseModel):
    license_key: str


@router.get("/")
async def get_license_status(user=Depends(require_auth)):
    """Return the currently active license."""
    lic = get_current_license()
    return lic.to_dict()


@router.post("/apply")
async def apply_license(
    body: ApplyLicenseRequest,
    user=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Install a new license key. Requires admin."""
    lic = load_license_from_string(body.license_key)
    if not lic.valid:
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_license", "message": lic.error},
        )

    # Persist the license key so it survives restarts
    svc = SettingsService(db)
    await svc.set("license_key", body.license_key)
    await db.commit()

    return {"status": "applied", "license": lic.to_dict()}


@router.delete("/")
async def remove_license(
    user=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Remove the current license — fall back to community tier."""
    svc = SettingsService(db)
    await svc.set("license_key", "")
    await db.commit()
    load_license_from_string("")
    return {"status": "removed", "tier": "community"}


@router.get("/features")
async def get_enabled_features(user=Depends(require_auth)):
    """Return the set of features the current license enables."""
    lic = get_current_license()
    return {
        "tier": lic.tier,
        "features": sorted(lic.features),
        "valid": lic.valid,
    }
