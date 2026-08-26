"""call2home — periodically reports usage to the license server.

Opt-in: does nothing unless the admin has set `license_server_url` under
Settings. Never blocks or degrades anything locally on failure/mismatch —
enforcement of the agent limit happens at agent-creation time
(api/agents.py, against the locally cached License), independent of whether
this heartbeat ever reaches the license server. This is purely a usage/
renewal signal for the operator, not a local kill switch — see
core/license.py's docstring and the "Produktionsdaten sind heilig" principle:
a network hiccup or a customer's air-gapped network must never stop agents
that are already running.
"""

import asyncio
import logging
import uuid

import httpx
from sqlalchemy import func, select

from app.config import _read_version
from app.models.agent import Agent

logger = logging.getLogger(__name__)

_INTERVAL = 6 * 3600  # 6 hours — frequent enough to catch revocations within any reasonable grace period, not chatty
_STARTUP_DELAY = 30


class LicenseHeartbeatService:
    def __init__(self, session_factory) -> None:
        self._sf = session_factory
        self._running = True

    async def run(self) -> None:
        await asyncio.sleep(_STARTUP_DELAY)
        while self._running:
            try:
                await self._beat()
            except Exception as exc:
                logger.warning("License heartbeat cycle failed (non-fatal): %s", exc)
            await asyncio.sleep(_INTERVAL)

    def stop(self) -> None:
        self._running = False

    # ------------------------------------------------------------------
    async def _beat(self) -> None:
        from app.db.session import resilient_session
        from app.services.settings_service import SettingsService

        async with resilient_session(session_factory=self._sf) as db:
            svc = SettingsService(db)
            server_url = (await svc.get("license_server_url") or "").strip()
            if not server_url:
                return  # opt-in — no server configured, stay silent

            license_key = (await svc.get("license_key") or "").strip()
            if not license_key:
                return  # nothing to authenticate the heartbeat with

            instance_id = await svc.get("license_instance_id")
            if not instance_id:
                instance_id = uuid.uuid4().hex
                await svc.set("license_instance_id", instance_id)
                await db.commit()

            from app.core.license import get_current_license
            lic = get_current_license()
            if not lic.license_id or lic.license_id == "community-default":
                return  # community tier has nothing to report home about

            agent_count = (await db.execute(select(func.count(Agent.id)))).scalar() or 0

        url = server_url.rstrip("/") + "/v1/call2home/heartbeat"
        body = {
            "instance_id": instance_id,
            "license_id": lic.license_id,
            "version": _read_version(),
            "active_agent_count": agent_count,
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(url, json=body, headers={"Authorization": f"Bearer {license_key}"})
            if resp.status_code == 200:
                logger.info("License heartbeat ok: %s", resp.json().get("license_status"))
            else:
                logger.warning("License heartbeat rejected: HTTP %s", resp.status_code)
        except httpx.HTTPError as exc:
            logger.info("License heartbeat unreachable (offline grace applies): %s", exc)
