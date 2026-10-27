"""Erste Schritte (#1047): der eigene Stand der fünf Einstiegsschritte."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import erste_schritte
from app.db.session import get_db
from app.dependencies import get_redis_service, require_auth
from app.services.redis_service import RedisService

router = APIRouter(prefix="/erste-schritte", tags=["erste-schritte"])


@router.get("")
async def mein_stand(
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    redis: RedisService = Depends(get_redis_service),
):
    """Nur der Stand des anfragenden Nutzers — es gibt keinen Parameter für andere."""
    return await erste_schritte.stand(db, user, redis)
