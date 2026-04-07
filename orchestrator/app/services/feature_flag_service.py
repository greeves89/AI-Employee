"""Feature flag service backed by Redis.

Flags are stored as Redis keys: feature:{name} -> JSON
Supports boolean toggles and percentage-based rollout.
"""

import hashlib
import json
import logging

from app.services.redis_service import RedisService

logger = logging.getLogger(__name__)

FEATURE_PREFIX = "feature:"


class FeatureFlagService:
    """Manages feature flags via Redis."""

    def __init__(self, redis: RedisService):
        self.redis = redis

    async def is_enabled(self, flag_name: str, user_id: str | None = None) -> bool:
        """Check if a feature flag is enabled.

        For percentage rollout, user_id determines deterministic bucket assignment.
        """
        if not self.redis.client:
            return False
        raw = await self.redis.client.get(f"{FEATURE_PREFIX}{flag_name}")
        if not raw:
            return False
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return raw in ("true", "1", "yes")

        if not data.get("enabled", False):
            return False

        rollout_pct = data.get("rollout_pct", 100)
        if rollout_pct >= 100:
            return True
        if rollout_pct <= 0:
            return False

        # Deterministic bucket: hash(flag_name + user_id) % 100
        if not user_id:
            return False
        bucket = int(hashlib.md5(f"{flag_name}:{user_id}".encode()).hexdigest(), 16) % 100
        return bucket < rollout_pct

    async def set_flag(
        self, name: str, enabled: bool = True, rollout_pct: int = 100, description: str = ""
    ) -> dict:
        """Create or update a feature flag."""
        if not self.redis.client:
            raise RuntimeError("Redis not connected")
        data = {
            "enabled": enabled,
            "rollout_pct": min(max(rollout_pct, 0), 100),
            "description": description,
        }
        await self.redis.client.set(f"{FEATURE_PREFIX}{name}", json.dumps(data))
        return {"name": name, **data}

    async def delete_flag(self, name: str) -> bool:
        """Delete a feature flag."""
        if not self.redis.client:
            return False
        return bool(await self.redis.client.delete(f"{FEATURE_PREFIX}{name}"))

    async def list_flags(self) -> list[dict]:
        """List all feature flags."""
        if not self.redis.client:
            return []
        keys = []
        async for key in self.redis.client.scan_iter(match=f"{FEATURE_PREFIX}*"):
            keys.append(key)

        flags = []
        for key in sorted(keys):
            raw = await self.redis.client.get(key)
            name = key.removeprefix(FEATURE_PREFIX)
            try:
                data = json.loads(raw)
                flags.append({"name": name, **data})
            except (json.JSONDecodeError, TypeError):
                flags.append(
                    {"name": name, "enabled": raw in ("true", "1"), "rollout_pct": 100, "description": ""}
                )
        return flags
