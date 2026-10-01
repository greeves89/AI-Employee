"""License API — check current license status, apply new licenses."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.license import get_current_license, verify_license
from app.db.session import get_db
from app.dependencies import require_admin, require_auth
from app.services import lizenz_zustand

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/license", tags=["license"])


class ApplyLicenseRequest(BaseModel):
    license_key: str


class NutzungRequest(BaseModel):
    #: ``True`` = die Anlage wird privat / nicht-gewerblich genutzt.
    privat: bool


def _ist_admin(user) -> bool:
    from app.models.user import UserRole
    return getattr(user, "role", None) == UserRole.ADMIN


@router.get("/")
async def get_license_status(user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Lizenzstand. Belegung, Limit und Hinweis nur fuer Administratoren."""
    return await lizenz_zustand.lizenzstatus(db, fuer_admin=_ist_admin(user))


@router.post("/apply")
async def apply_license(
    body: ApplyLicenseRequest,
    user=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Install a new license key. Requires admin."""
    # ERST pruefen, DANN uebernehmen. Frueher wurde der Schluessel sofort
    # geladen — ein Tippfehler ersetzte damit die gueltige Lizenz im Speicher
    # durch die Community-Lizenz, bis zum naechsten Neustart.
    geprueft = verify_license(body.license_key)
    if not geprueft.valid:
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_license", "message": geprueft.error or "Kein gültiger Lizenzschlüssel."},
        )

    lic = await lizenz_zustand.lizenz_eintragen(db, body.license_key)
    await db.commit()

    return {"status": "applied", "license": lic.to_dict()}


@router.delete("/")
async def remove_license(
    user=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Remove the current license key.

    Its agent limit stays in force until a new license is applied — removing a
    license must not grant more than holding one (#886).
    """
    await lizenz_zustand.lizenz_entfernen(db)
    await db.commit()
    return {"status": "removed", "tier": "community"}


@router.post("/nutzung")
async def set_nutzung(
    body: NutzungRequest,
    user=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Erklaerung des Administrators: private / nicht-gewerbliche Nutzung.

    Sie beendet den Hinweis auf die Testphase. Mehr tut sie nicht — die Lizenz
    regelt, was erlaubt ist; die Software kann es nicht pruefen.
    """
    await lizenz_zustand.setze_private_nutzung(db, body.privat)
    await db.commit()
    return await lizenz_zustand.lizenzstatus(db, fuer_admin=True)


@router.get("/features")
async def get_enabled_features(user=Depends(require_auth)):
    """Return the set of features the current license enables."""
    lic = get_current_license()
    return {
        "tier": lic.tier,
        "features": sorted(lic.features),
        "valid": lic.valid,
    }
