"""Microsoft Teams als vollwertiger Kanal — hin und zurück.

Der AUSGANG war längst da: ``ms_send_teams_message`` und die Chat-Werkzeuge in
``msgraph_mcp`` können seit langem in Kanäle und Chats schreiben. Was fehlte, war der
EINGANG — es gab keinen Weg, auf dem eine Nachricht aus Teams bei einem Agenten
ankommt. Damit war Teams eine Einbahnstraße: der Agent konnte hineinrufen, aber
niemand konnte ihn ansprechen.

Bewusst OHNE Bot-Registrierung im Azure Bot Service. Die Graph-Anbindung mit
Nutzer-OAuth existiert bereits und darf Chats lesen und schreiben; eine zusätzliche
Bot-Identität hätte eine neue Registrierung, ein neues Geheimnis und eine neue
Freigabekette beim Kunden bedeutet — für dieselbe Fähigkeit. Stattdessen wird
abgefragt, so wie der Telegram-Bot ``getUpdates`` abfragt.

Drei Richtungen, die der Nutzer ausdrücklich wollte:

1. **Mensch schreibt den Agenten an** — Nachricht im überwachten Chat/Kanal → Agent
2. **Agent schreibt Agent** — Agent A schreibt über Graph in den Chat, den Agent B
   überwacht; für B ist das eine ganz normale eingehende Nachricht
3. **Agent im Termin** — siehe ``teams_meetings.py``

Der Takt hängt am bestehenden Scheduler, nicht an einem eigenen Uhrwerk.
"""

import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core import channel_gateway as gw
from app.db.session import resilient_session
from app.models.agent import Agent

logger = logging.getLogger(__name__)

CONFIG_KEY = "teams"
# Wie weit zurück beim allerersten Lauf geschaut wird. Kurz gehalten: sonst arbeitet
# der Agent beim Einschalten den halben Chatverlauf als „neue" Nachrichten ab.
FIRST_RUN_LOOKBACK = timedelta(minutes=15)
# Obergrenze je Abfrage — ein sehr lebhafter Kanal darf nicht einen ganzen Lauf fluten.
MAX_PER_POLL = 20


def channel_config(agent: Agent) -> dict:
    """Die Teams-Einstellungen eines Agenten."""
    return ((agent.config or {}).get("channels") or {}).get(CONFIG_KEY) or {}


def is_enabled(agent: Agent) -> bool:
    cfg = channel_config(agent)
    return bool(cfg.get("enabled") and (cfg.get("chat_ids") or cfg.get("channels")))


def _mentions_agent(text: str, agent: Agent, cfg: dict) -> bool:
    """Ist der Agent gemeint?

    In einem Gruppenchat will niemand, dass der Agent auf JEDE Nachricht antwortet.
    Vorgabe ist deshalb: nur bei Nennung. Wer einen Chat ausschliesslich für einen
    Agenten führt, schaltet ``mention_only`` ab und bekommt alles.
    """
    if not cfg.get("mention_only", True):
        return True
    needles = [agent.name.lower()]
    extra = cfg.get("mention_names") or []
    needles.extend(str(n).lower() for n in extra if str(n).strip())
    low = (text or "").lower()
    return any(n and n in low for n in needles)


def _plain_text(message: dict) -> str:
    """Den Text aus einer Graph-Nachricht holen.

    Teams liefert HTML. Wir wollen den Fliesstext — die Auszeichnung interessiert den
    Agenten nicht, und HTML im Prompt kostet nur Platz.
    """
    import re

    body = (message.get("body") or {})
    content = body.get("content") or ""
    if (body.get("contentType") or "").lower() == "html":
        content = re.sub(r"<br\s*/?>", "\n", content)
        content = re.sub(r"<[^>]+>", "", content)
        import html as _html
        content = _html.unescape(content)
    return " ".join(content.split())


def _sender(message: dict) -> tuple[str, str]:
    user = ((message.get("from") or {}).get("user") or {})
    return user.get("id") or "", user.get("displayName") or ""


async def _graph_token(user_id: str) -> str | None:
    try:
        from app.services.oauth_service import OAuthService

        async with resilient_session() as db:
            return await OAuthService(db).get_valid_token("microsoft", user_id)
    except Exception as e:  # noqa: BLE001 — ohne Token ist Teams fuer diesen Nutzer aus
        logger.debug("[Teams] kein Graph-Token fuer %s: %s", user_id, e)
        return None


class TeamsGateway:
    """Fragt überwachte Chats/Kanäle ab und stellt neue Nachrichten zu."""

    def __init__(self, redis=None):
        self.redis = redis
        self._running = False

    async def tick(self) -> dict | None:
        """Billig, wenn kein Agent Teams eingeschaltet hat."""
        if self._running:
            return None
        self._running = True
        try:
            return await self._poll_all()
        except Exception as e:  # noqa: BLE001 — ein Kanal darf den Scheduler nie kippen
            logger.warning("[Teams] Abfrage fehlgeschlagen: %s", e)
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
            except Exception as e:  # noqa: BLE001 — ein Agent darf die anderen nicht stoppen
                logger.warning("[Teams] Agent %s: %s", agent.id, e)
        return {"agents": len(targets), "delivered": delivered} if delivered else None

    async def _poll_agent(self, agent: Agent) -> int:
        cfg = channel_config(agent)
        token = await _graph_token(agent.user_id)
        if not token:
            return 0

        since = await self._watermark(agent.id)
        newest = since
        delivered = 0

        for chat_id in cfg.get("chat_ids") or []:
            got, newest = await self._poll_source(
                agent, cfg, token, since, newest,
                path=f"/chats/{chat_id}/messages",
                context={"chat_id": chat_id},
            )
            delivered += got

        for entry in cfg.get("channels") or []:
            team_id, channel_id = entry.get("team_id"), entry.get("channel_id")
            if not (team_id and channel_id):
                continue
            got, newest = await self._poll_source(
                agent, cfg, token, since, newest,
                path=f"/teams/{team_id}/channels/{channel_id}/messages",
                context={"team_id": team_id, "channel_id": channel_id},
            )
            delivered += got

        await self._save_watermark(agent.id, newest)
        return delivered

    async def _poll_source(self, agent, cfg, token, since, newest, *, path, context):
        from app.core.msgraph_mcp import _graph

        data = await _graph("GET", path, token, params={"$top": MAX_PER_POLL})
        messages = data.get("value", [])
        delivered = 0

        # Aelteste zuerst zustellen, damit der Agent die Unterhaltung in der
        # richtigen Reihenfolge sieht — Graph liefert neueste zuerst.
        for message in reversed(messages):
            created = message.get("createdDateTime") or ""
            try:
                stamp = datetime.fromisoformat(created.replace("Z", "+00:00"))
            except ValueError:
                continue
            if stamp <= since:
                continue
            newest = max(newest, stamp)

            sender_id, sender_name = _sender(message)
            # Eigene Nachrichten NIE zurueckverarbeiten: der Agent schreibt ueber
            # dasselbe Konto, und ohne diese Sperre antwortet er auf sich selbst —
            # eine Schleife, die erst das Token-Budget beendet.
            if sender_id and sender_id == cfg.get("own_user_id"):
                continue
            if (message.get("messageType") or "message") != "message":
                continue

            text = _plain_text(message)
            if not text or not _mentions_agent(text, agent, cfg):
                continue

            inbound = gw.InboundMessage(
                agent_id=agent.id,
                text=text,
                channel=gw.CHANNEL_TEAMS,
                conversation_id=context.get("chat_id") or context.get("channel_id", ""),
                message_id=str(message.get("id") or ""),
                context={**context, "sender_name": sender_name,
                         "reply_to_id": message.get("id")},
                sender_name=sender_name,
            )
            if await gw.deliver(self.redis, inbound):
                delivered += 1

        return delivered, newest

    # -------------------------------------------------------------- Wasserstand

    async def _watermark(self, agent_id: str) -> datetime:
        """Bis wohin dieser Agent schon gelesen hat."""
        from app.services.settings_service import SettingsService

        async with resilient_session() as db:
            raw = await SettingsService(db).get(f"teams_watermark_{agent_id}")
        if raw:
            try:
                return datetime.fromisoformat(raw)
            except ValueError:
                pass
        return datetime.now(timezone.utc) - FIRST_RUN_LOOKBACK

    async def _save_watermark(self, agent_id: str, value: datetime) -> None:
        from app.services.settings_service import SettingsService

        async with resilient_session() as db:
            await SettingsService(db).set(f"teams_watermark_{agent_id}", value.isoformat())
            await db.commit()


async def send_reply(agent: Agent, context: dict, text: str) -> bool:
    """Eine Antwort zurück nach Teams schicken.

    Nutzt denselben Graph-Weg wie die Werkzeuge des Agenten — es gibt keinen zweiten
    Sendepfad, der andere Rechte oder anderes Verhalten hätte.
    """
    from app.core.msgraph_mcp import _graph

    token = await _graph_token(agent.user_id)
    if not token:
        logger.warning("[Teams] Antwort nicht moeglich — kein Graph-Token")
        return False

    if context.get("chat_id"):
        path = f"/chats/{context['chat_id']}/messages"
    elif context.get("team_id") and context.get("channel_id"):
        path = f"/teams/{context['team_id']}/channels/{context['channel_id']}/messages"
    else:
        return False

    try:
        await _graph("POST", path, token,
                     json={"body": {"contentType": "html", "content": _to_html(text)}})
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("[Teams] Senden fehlgeschlagen: %s", e)
        return False


class TeamsResponder:
    """Hört auf die Antworten der Agenten und schickt sie nach Teams zurück.

    Dieselbe Redis-Schiene, auf der auch Telegram und der Web-Chat lauschen —
    ``agent:{id}:chat:response``. Gefiltert wird über das Nachrichten-Präfix ``tm-``,
    genau wie Telegram auf ``tg-`` filtert.

    Gesammelt statt gestreamt: Teams kennt kein „Nachricht wächst mit" wie Telegram.
    Häppchenweise zu senden ergäbe zwanzig Einzelnachrichten für eine Antwort.
    """

    def __init__(self, redis=None):
        self.redis = redis
        self._tasks: dict[str, object] = {}

    async def ensure_listeners(self) -> int:
        """Für jeden Agenten mit eingeschaltetem Teams-Kanal einen Lauscher starten."""
        import asyncio

        async with resilient_session() as db:
            agents = (await db.execute(
                select(Agent).where(Agent.user_id.isnot(None))
            )).scalars().all()
            wanted = {a.id: a for a in agents if is_enabled(a)}

        # Abgeschaltete Kanäle: Lauscher beenden, sonst laufen sie bis zum Neustart.
        for agent_id in list(self._tasks):
            task = self._tasks[agent_id]
            if agent_id not in wanted or getattr(task, "done", lambda: True)():
                try:
                    task.cancel()  # type: ignore[attr-defined]
                except Exception:  # noqa: BLE001
                    pass
                self._tasks.pop(agent_id, None)

        for agent_id, agent in wanted.items():
            if agent_id not in self._tasks:
                self._tasks[agent_id] = asyncio.create_task(self._listen(agent_id))
                logger.info("[Teams] Antwort-Lauscher fuer %s gestartet", agent_id)
        return len(self._tasks)

    async def _listen(self, agent_id: str) -> None:
        pubsub = self.redis.client.pubsub()
        await pubsub.subscribe(f"agent:{agent_id}:chat:response")
        buffers: dict[str, str] = {}
        contexts: dict[str, dict] = {}

        try:
            while True:
                raw = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if not raw or raw.get("type") != "message":
                    continue
                try:
                    data = json.loads(raw["data"])
                except (ValueError, TypeError):
                    continue

                msg_id = data.get("message_id", "")
                if not msg_id.startswith(f"{gw.CHANNEL_PREFIX[gw.CHANNEL_TEAMS]}-"):
                    continue

                event = data.get("type", "")
                payload = data.get("data", {})

                if event == "text":
                    buffers[msg_id] = buffers.get(msg_id, "") + (payload.get("text") or "")
                    if payload.get("context"):
                        contexts[msg_id] = payload["context"]
                elif event in ("result", "done", "complete"):
                    text = buffers.pop(msg_id, "").strip()
                    ctx = contexts.pop(msg_id, None) or await self._context_for(msg_id)
                    if text and ctx:
                        async with resilient_session() as db:
                            agent = await db.get(Agent, agent_id)
                        if agent:
                            await send_reply(agent, ctx, text)
        except Exception as e:  # noqa: BLE001
            logger.warning("[Teams] Lauscher %s beendet: %s", agent_id, e)
        finally:
            try:
                await pubsub.unsubscribe()
            except Exception:  # noqa: BLE001
                pass

    async def _context_for(self, msg_id: str) -> dict | None:
        """Wohin die Antwort gehört, falls der Lauf den Kontext nicht mitgeliefert hat.

        Der Kontext wurde beim Einreihen mitgegeben; hier wird er aus der zuletzt
        gespeicherten Nachricht dieser Sitzung rekonstruiert, damit eine Antwort
        nicht verloren geht, nur weil die Laufzeit das Feld nicht durchgereicht hat.
        """
        try:
            raw = await self.redis.client.get(f"gateway:ctx:{msg_id}")
            return json.loads(raw) if raw else None
        except Exception:  # noqa: BLE001
            return None


def _to_html(text: str) -> str:
    """Agenten-Markdown in das HTML, das Teams anzeigt.

    Dieselbe Ueberlegung wie bei Telegram: ZUERST escapen, dann nur die erkannten
    Auszeichnungen zu Tags machen — ein loses Sonderzeichen im Agententext kann so
    die Nachricht nicht zerlegen.
    """
    import html as _html
    import re

    out = _html.escape(text or "", quote=False)
    out = re.sub(r"```[a-zA-Z0-9_+-]*\n?(.*?)```", r"<pre>\1</pre>", out, flags=re.S)
    out = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", out)
    out = re.sub(r"(?m)^\s{0,3}#{1,6}\s+(.+?)\s*$", r"<b>\1</b>", out)
    out = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"<b>\1</b>", out, flags=re.S)
    out = re.sub(r"(?m)^\s*[-*]\s+", "• ", out)
    return out.replace("\n", "<br>").strip()
