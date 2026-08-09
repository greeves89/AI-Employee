"""Slack als Kanal — hin und zurück, über denselben Eingang wie Telegram und Teams.

Abgefragt statt per Webhook zugestellt, aus einem betrieblichen Grund: Ein Webhook
verlangt, dass Slack diese Anlage aus dem Internet erreichen kann. Beim Kunden steht
sie hinter einer Firewall im Kliniknetz — ein eingehender Weg von außen wäre dort
weder gewollt noch genehmigungsfähig. Abfragen funktioniert überall, wo ausgehendes
HTTPS erlaubt ist, und das ist die Voraussetzung ohnehin.

Gebraucht wird ein Bot-Token (``xoxb-…``) mit den Rechten ``channels:history``,
``groups:history``, ``chat:write`` und ``users:read``. Das Token liegt verschlüsselt
in den Einstellungen des Agenten, nicht im Code.
"""

import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select

from app.core import channel_gateway as gw
from app.db.session import resilient_session
from app.models.agent import Agent

logger = logging.getLogger(__name__)

SLACK_API = "https://slack.com/api"
CONFIG_KEY = "slack"
# Redis-Namensraum fuer die Laufmarken dieses Kanals.
KEY_PREFIX = "slack"

FIRST_RUN_LOOKBACK = timedelta(minutes=15)
MAX_PER_POLL = 20


def channel_config(agent: Agent) -> dict:
    return ((agent.config or {}).get("channels") or {}).get(CONFIG_KEY) or {}


def is_enabled(agent: Agent) -> bool:
    cfg = channel_config(agent)
    return bool(cfg.get("enabled") and cfg.get("channels"))


async def _token_for(agent: Agent) -> str | None:
    """Das Bot-Token dieses Agenten, entschlüsselt.

    Liegt in den Agenten-Geheimnissen, nicht in der Config — ein Token in einem
    JSON-Feld landet sonst in jeder Antwort, die die Agenten-Config ausliefert.
    """
    from app.core.encryption import decrypt_token
    from app.services.settings_service import SettingsService

    cfg = channel_config(agent)
    raw = cfg.get("bot_token_enc") or ""
    if raw:
        try:
            return decrypt_token(raw)
        except Exception:  # noqa: BLE001
            logger.warning("[Slack] Token von %s nicht entschluesselbar", agent.id)
            return None
    # Rückfall: ein plattformweites Token in den Einstellungen.
    async with resilient_session() as db:
        return await SettingsService(db).get("slack_bot_token")


async def _call(token: str, method: str, **params) -> dict:
    """Ein Slack-Aufruf. Slack meldet Fehler mit HTTP 200 und ``ok: false``."""
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            f"{SLACK_API}/{method}",
            headers={"Authorization": f"Bearer {token}"},
            data=params,
        )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Slack {method}: {data.get('error', 'unbekannt')}")
    return data


def _mentions_agent(text: str, agent: Agent, cfg: dict) -> bool:
    """Vorgabe wie bei Teams: in einem Kanal nur auf Nennung antworten."""
    if not cfg.get("mention_only", True):
        return True
    needles = [agent.name.lower()]
    if cfg.get("bot_user_id"):
        needles.append(f"<@{cfg['bot_user_id']}>".lower())
    needles.extend(str(n).lower() for n in (cfg.get("mention_names") or []) if str(n).strip())
    low = (text or "").lower()
    return any(n and n in low for n in needles)


class SlackGateway:
    def __init__(self, redis=None):
        self.redis = redis
        self._running = False

    async def tick(self) -> dict | None:
        if self._running:
            return None
        self._running = True
        try:
            return await self._poll_all()
        except Exception as e:  # noqa: BLE001
            logger.warning("[Slack] Abfrage fehlgeschlagen: %s", e)
            return None
        finally:
            self._running = False

    async def _poll_all(self) -> dict | None:
        async with resilient_session() as db:
            agents = (await db.execute(
                select(Agent).where(Agent.user_id.isnot(None))
            )).scalars().all()
            targets = [a for a in agents if is_enabled(a)]
        if not targets:
            return None

        delivered = 0
        for agent in targets:
            try:
                delivered += await self._poll_agent(agent)
            except Exception as e:  # noqa: BLE001
                logger.warning("[Slack] Agent %s: %s", agent.id, e)
        return {"agents": len(targets), "delivered": delivered} if delivered else None

    async def _poll_agent(self, agent: Agent) -> int:
        cfg = channel_config(agent)
        token = await _token_for(agent)
        if not token:
            return 0

        since = await self._watermark(agent.id)
        newest = since
        delivered = 0

        for channel_id in cfg.get("channels") or []:
            data = await _call(token, "conversations.history",
                               channel=channel_id, limit=MAX_PER_POLL,
                               oldest=f"{since.timestamp():.6f}")
            for message in reversed(data.get("messages", [])):
                stamp = datetime.fromtimestamp(float(message.get("ts", 0)), tz=timezone.utc)
                if stamp <= since:
                    continue
                newest = max(newest, stamp)

                # Eigene Nachrichten und Systemmeldungen nie zurueckverarbeiten —
                # sonst antwortet der Agent auf sich selbst.
                if message.get("bot_id") or message.get("subtype"):
                    continue
                if cfg.get("bot_user_id") and message.get("user") == cfg["bot_user_id"]:
                    continue

                text = (message.get("text") or "").strip()
                if not text or not _mentions_agent(text, agent, cfg):
                    continue

                inbound = gw.InboundMessage(
                    agent_id=agent.id,
                    text=text,
                    channel=gw.CHANNEL_SLACK,
                    conversation_id=channel_id,
                    message_id=str(message.get("ts")),
                    context={"channel_id": channel_id,
                             # In einem Thread wird auch im Thread geantwortet —
                             # sonst reisst die Antwort das Gespraech auseinander.
                             "thread_ts": message.get("thread_ts") or message.get("ts")},
                    sender_name=message.get("user") or "",
                )
                if await gw.deliver(self.redis, inbound):
                    delivered += 1

        await self._save_watermark(agent.id, newest)
        return delivered

    # ------------------------------------------------------------ Wasserstand

    async def _watermark(self, agent_id: str) -> datetime:
        """Bis wohin dieser Agent schon gelesen hat.

        In Redis, NICHT in den Plattform-Einstellungen: das ist eine Laufmarke, keine
        Einstellung. ``SettingsService.set`` lehnt unbekannte Schluessel ausserdem ab —
        ein Wasserstand je Agent waere dort nie gespeichert worden, und der Poller
        haette bei jedem Durchlauf dasselbe Fenster erneut gelesen.
        """
        try:
            raw = await self.redis.client.get(f"{KEY_PREFIX}:watermark:{agent_id}")
            if raw:
                return datetime.fromisoformat(raw if isinstance(raw, str) else raw.decode())
        except Exception:  # noqa: BLE001
            logger.debug("[%s] Wasserstand nicht lesbar", KEY_PREFIX, exc_info=True)
        return datetime.now(timezone.utc) - FIRST_RUN_LOOKBACK

    async def _save_watermark(self, agent_id: str, value: datetime) -> None:
        try:
            await self.redis.client.set(
                f"{KEY_PREFIX}:watermark:{agent_id}", value.isoformat()
            )
        except Exception:  # noqa: BLE001
            logger.warning("[%s] Wasserstand nicht speicherbar — naechster Lauf liest erneut",
                           KEY_PREFIX)


async def send_reply(agent: Agent, context: dict, text: str) -> bool:
    token = await _token_for(agent)
    channel_id = context.get("channel_id")
    if not (token and channel_id):
        return False
    try:
        await _call(token, "chat.postMessage", channel=channel_id,
                    text=to_mrkdwn(text),
                    **({"thread_ts": context["thread_ts"]} if context.get("thread_ts") else {}))
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("[Slack] Senden fehlgeschlagen: %s", e)
        return False


def to_mrkdwn(text: str) -> str:
    """Agenten-Markdown in Slacks eigene Auszeichnung.

    Slack ist kein Markdown: fett ist ``*so*`` statt ``**so**``, und Überschriften
    kennt es gar nicht. Ohne diese Umwandlung stünden die Sternchen im Klartext da.
    """
    import re

    out = text or ""
    out = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"*\1*", out, flags=re.S)
    out = re.sub(r"(?m)^\s{0,3}#{1,6}\s+(.+?)\s*$", r"*\1*", out)
    out = re.sub(r"(?m)^\s*[-*]\s+", "• ", out)
    return out.strip()
