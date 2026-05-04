"""User Profile API — view, update, and extract adaptive user profiles."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies import require_auth
from app.models.user_profile import UserProfile, UserProfileEvent
from app.services.profile_extractor import (
    delete_dimension,
    extract_profile,
    generate_profile_summary,
    get_or_create_profile,
    update_dimension,
)

router = APIRouter(prefix="/user-profiles", tags=["user-profiles"])


class ProfileResponse(BaseModel):
    user_id: str
    profile_version: int
    dimensions: dict
    last_extracted_at: str | None = None


class DimensionUpdate(BaseModel):
    dimension: str
    key: str
    value: str
    confidence: float = 1.0


class ProfileEventResponse(BaseModel):
    id: int
    dimension: str
    key: str
    old_value: str | None
    new_value: str
    source: str
    confidence: float
    created_at: str


def _profile_to_response(p: UserProfile) -> ProfileResponse:
    return ProfileResponse(
        user_id=p.user_id,
        profile_version=p.profile_version,
        dimensions=p.dimensions or {},
        last_extracted_at=p.last_extracted_at.isoformat() if p.last_extracted_at else None,
    )


@router.get("/me")
async def get_my_profile(
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> ProfileResponse:
    profile = await get_or_create_profile(db, user.id)
    await db.commit()
    return _profile_to_response(profile)


@router.post("/me/extract")
async def extract_my_profile(
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> ProfileResponse:
    profile = await extract_profile(db, user.id)
    await db.commit()
    return _profile_to_response(profile)


@router.put("/me/dimensions")
async def update_my_dimension(
    body: DimensionUpdate,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> ProfileResponse:
    profile = await update_dimension(
        db, user.id, body.dimension, body.key, body.value, body.confidence
    )
    await db.commit()
    return _profile_to_response(profile)


@router.delete("/me/dimensions/{dimension}")
async def delete_my_dimension(
    dimension: str,
    key: str | None = None,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> ProfileResponse:
    profile = await delete_dimension(db, user.id, dimension, key)
    await db.commit()
    return _profile_to_response(profile)


@router.get("/me/summary")
async def get_my_profile_summary(
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> dict:
    profile = await get_or_create_profile(db, user.id)
    await db.commit()
    return {"summary": generate_profile_summary(profile)}


@router.get("/me/events")
async def get_my_profile_events(
    limit: int = 50,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> list[ProfileEventResponse]:
    result = await db.execute(
        select(UserProfileEvent)
        .where(UserProfileEvent.user_id == user.id)
        .order_by(UserProfileEvent.created_at.desc())
        .limit(limit)
    )
    events = result.scalars().all()
    return [
        ProfileEventResponse(
            id=ev.id,
            dimension=ev.dimension,
            key=ev.key,
            old_value=ev.old_value,
            new_value=ev.new_value,
            source=ev.source,
            confidence=ev.confidence,
            created_at=ev.created_at.isoformat(),
        )
        for ev in events
    ]
