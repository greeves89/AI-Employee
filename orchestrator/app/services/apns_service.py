"""APNs push notifications.

Signs a provider JWT (ES256) with the APNs auth key and delivers alerts
over HTTP/2. Config comes from PlatformSettings (loaded into `settings`):
apns_auth_key (.p8 contents), apns_key_id, apns_team_id, apns_bundle_id,
apns_sandbox.
"""

from __future__ import annotations

import logging
import time

import httpx
import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.device_token import DeviceToken

logger = logging.getLogger(__name__)


class APNsService:
    _token: str | None = None
    _token_at: float = 0.0

    @classmethod
    def configured(cls) -> bool:
        return bool(
            settings.apns_auth_key and settings.apns_key_id and settings.apns_team_id
        )

    @classmethod
    def _provider_token(cls) -> str:
        # Apple wants the JWT refreshed periodically; 30 min is safely under
        # the 1 h limit and over the 20 min minimum.
        now = time.time()
        if cls._token and (now - cls._token_at) < 1800:
            return cls._token
        cls._token = jwt.encode(
            {"iss": settings.apns_team_id, "iat": int(now)},
            settings.apns_auth_key,
            algorithm="ES256",
            headers={"kid": settings.apns_key_id},
        )
        cls._token_at = now
        return cls._token

    @classmethod
    async def send(cls, device_token: str, title: str, body: str) -> bool:
        if not cls.configured():
            return False
        host = (
            "api.sandbox.push.apple.com"
            if settings.apns_sandbox
            else "api.push.apple.com"
        )
        payload = {
            "aps": {
                "alert": {"title": title, "body": body},
                "sound": "default",
            }
        }
        headers = {
            "authorization": f"bearer {cls._provider_token()}",
            "apns-topic": settings.apns_bundle_id,
            "apns-push-type": "alert",
        }
        try:
            async with httpx.AsyncClient(http2=True, timeout=10.0) as client:
                r = await client.post(
                    f"https://{host}/3/device/{device_token}",
                    headers=headers, json=payload,
                )
            if r.status_code != 200:
                logger.warning("APNs %s for %s…: %s",
                               r.status_code, device_token[:8], r.text)
            return r.status_code == 200
        except Exception:  # noqa: BLE001
            logger.exception("APNs send failed")
            return False


async def push_to_user(
    db: AsyncSession, user_id: str, title: str, body: str
) -> None:
    """Send an alert to every device the user has registered. Best-effort."""
    if not user_id or not APNsService.configured():
        return
    rows = await db.execute(
        select(DeviceToken).where(DeviceToken.user_id == user_id)
    )
    for dt in rows.scalars().all():
        await APNsService.send(dt.token, title, body)
