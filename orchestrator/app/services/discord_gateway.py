"""Discord als Kanal — hin und zurück, über denselben Eingang wie Slack und Teams.

Abgefragt statt über die Gateway-WebSocket, aus demselben betrieblichen Grund wie
bei Slack: Discords dauerhafte WebSocket-Verbindung müsste über die Laufzeit der
Anlage offen bleiben und bei jedem Neustart neu aufgebaut werden. Die REST-Abfrage
braucht nur ausgehendes HTTPS — die Voraussetzung, die in einem Kliniknetz ohnehin
die einzig genehmigungsfähige ist.

Gebraucht wird ein Bot-Token mit dem Recht, die betreffenden Kanäle zu lesen und zu
schreiben (``View Channels``, ``Send Messages``, ``Read Message History``) sowie die
Berechtigung **Message Content Intent** in den Bot-Einstellungen — ohne die liefert
Discord den Text jeder Nachricht leer aus, und der Agent bekäme lauter leere
Nachrichten zugestellt.

Das Token liegt verschlüsselt in der Agenten-Konfiguration, nicht im Code.
"""

import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select

from app.core import channel_gateway as gw
from app.db.session import resilient_session
from app.models.agent import Agent

logger = logging.getLogger(__name__)

DISCORD_API = "https://discord.com/api/v10"
CONFIG_KEY = "discord"
KEY_PREFIX = "discord"

FIRST_RUN_LOOKBACK = timedelta(minutes=15)
MAX_PER_POLL = 20
#: Discord nimmt 2000 Zeichen je Nachricht. Längeres wird geteilt statt
#: abgeschnitten — eine abgeschnittene Antwort ist schlimmer als zwei Nachrichten.
MAX_MESSAGE_CHARS = 1900


def channel_config(agent: Agent) -> dict:
    return ((agent.config or {}).get("channels") or {}).get(CONFIG_KEY) or {}


def is_enabled(agent: Agent) -> bool:
    cfg = channel_config(agent)
    return bool(cfg.get("enabled") and cfg.get("channels"))


async def _token_for(agent: Agent) -> str | None:
    """Das Bot-Token dieses Agenten, entschlüsselt.

    Wie bei Slack verschlüsselt abgelegt — ein Klartext-Token in der Config landet
    sonst in jeder Antwort, die die Agenten-Config ausliefert.
    """
    from app.core.encryption import decrypt_token

    raw = channel_config(agent).get("bot_token_enc") or ""
    if not raw:
        return None
    try:
        return decrypt_token(raw)
    except Exception:  # noqa: BLE001
        logger.warning("[Discord] Token von %s nicht entschluesselbar", agent.id)
        return None


async def _call(token: str, method: str, path: str, **kwargs) -> dict | list:
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.request(
            method, f"{DISCORD_API}{path}",
            headers={"Authorization": f"Bot {token}"}, **kwargs,
        )
        if resp.status_code == 429:
            # Discord ist beim Abfragen streng. Kein Nachbohren: der naechste
            # Durchlauf holt dieselben Nachrichten, der Wasserstand bleibt stehen.
            raise RuntimeError("rate limited")
        resp.raise_for_status()
        return resp.json() if resp.content else {}


def _mentions_agent(text: str, agent: Agent, cfg: dict, mentions: list) -> bool:
    """Reagiert der Agent auf diese Nachricht?

    In einem Kanal mit Menschen darf ein Agent nicht auf jede Zeile antworten. Drei
    Wege, ihn anzusprechen: echte Discord-Erwähnung, sein Name, oder ein
    konfiguriertes Stichwort. Ohne ``mention_required`` antwortet er auf alles —
    sinnvoll nur in einem eigenen Kanal.
    """
    if not cfg.get("mention_required", True):
        return True
    bot_id = str(cfg.get("bot_user_id") or "")
    if bot_id and any(str(m.get("id")) == bot_id for m in mentions or []):
        return True
    haystack = (text or "").lower()
    if agent.name and agent.name.lower() in haystack:
        return True
    return any(str(k).lower() in haystack for k in (cfg.get("keywords") or []))


class DiscordGateway:
    """Fragt regelmässig die konfigurierten Kanäle ab und stellt neue Nachrichten zu."""

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
            logger.warning("[Discord] Abfrage fehlgeschlagen: %s", e)
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
                logger.warning("[Discord] Agent %s: %s", agent.id, e)
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
            data = await _call(token, "GET", f"/channels/{channel_id}/messages",
                               params={"limit": MAX_PER_POLL})
            # Discord liefert neueste zuerst — umgedreht bleibt die Reihenfolge
            # des Gespraechs erhalten.
            for message in reversed(data if isinstance(data, list) else []):
                stamp = _parse_stamp(message.get("timestamp"))
                if stamp is None or stamp <= since:
                    continue
                newest = max(newest, stamp)

                author = message.get("author") or {}
                # Eigene und fremde Bot-Nachrichten nie zurueckverarbeiten — sonst
                # antworten zwei Bots einander bis zum Rate-Limit.
                if author.get("bot"):
                    continue

                text = (message.get("content") or "").strip()
                if not text:
                    # Leerer Text heisst fast immer: Message Content Intent fehlt.
                    continue
                if not _mentions_agent(text, agent, cfg, message.get("mentions") or []):
                    continue

                inbound = gw.InboundMessage(
                    agent_id=agent.id,
                    text=text,
                    channel=gw.CHANNEL_DISCORD,
                    conversation_id=str(channel_id),
                    message_id=str(message.get("id")),
                    context={"channel_id": str(channel_id),
                             "message_id": str(message.get("id"))},
                    sender_name=author.get("global_name") or author.get("username") or "",
                )
                if await gw.deliver(self.redis, inbound):
                    delivered += 1

        await self._save_watermark(agent.id, newest)
        return delivered

    # ------------------------------------------------------------ Wasserstand

    async def _watermark(self, agent_id: str) -> datetime:
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


def _parse_stamp(raw: str | None) -> datetime | None:
    """Discords ISO-Zeitstempel, robust gegen das ``Z`` am Ende."""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


async def send_reply(agent: Agent, context: dict, text: str) -> bool:
    token = await _token_for(agent)
    channel_id = context.get("channel_id")
    if not (token and channel_id):
        return False
    try:
        for chunk in split_message(to_discord_markdown(text)):
            await _call(token, "POST", f"/channels/{channel_id}/messages",
                        json={"content": chunk})
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("[Discord] Senden fehlgeschlagen: %s", e)
        return False


def to_discord_markdown(text: str) -> str:
    """Agenten-Markdown für Discord.

    Discord kann fett, kursiv, Code und Listen wie üblich — aber **keine
    Überschriften mit mehr als drei Rauten** und keine Tabellen. Überschriften
    werden deshalb zu fetten Zeilen; alles andere bleibt, wie es ist.
    """
    import re

    out = text or ""
    out = re.sub(r"(?m)^\s{0,3}#{4,6}\s+(.+?)\s*$", r"**\1**", out)
    return out.strip()


def split_message(text: str, limit: int = MAX_MESSAGE_CHARS) -> list[str]:
    """In sendbare Stücke teilen — bevorzugt an Absätzen, nie mitten im Wort.

    Abschneiden wäre die einfachere Lösung und die falsche: eine halbe Antwort
    sieht aus wie eine ganze, und niemand merkt, dass der Rest fehlt.
    """
    text = text or ""
    if len(text) <= limit:
        return [text] if text else []

    chunks: list[str] = []
    rest = text
    while len(rest) > limit:
        window = rest[:limit]
        cut = window.rfind("\n\n")
        if cut < limit // 2:
            cut = window.rfind("\n")
        if cut < limit // 2:
            cut = window.rfind(" ")
        if cut <= 0:
            cut = limit
        chunks.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    if rest:
        chunks.append(rest)
    return chunks
