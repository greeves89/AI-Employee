"""WhatsApp über die Meta Cloud API — Eingang per Webhook, Ausgang per Graph-Aufruf.

Anders als Teams und Slack lässt sich WhatsApp NICHT abfragen: Meta stellt eingehende
Nachrichten ausschließlich per Webhook zu, eine „hole neue Nachrichten"-Schnittstelle
gibt es nicht. Diese Anlage muss dafür also aus dem Internet erreichbar sein — auf dem
Pi ist sie das über den bestehenden Cloudflare-Tunnel.

Zwei Dinge, die Meta verlangt und die deshalb hier stehen:

* **Verifizierung.** Beim Einrichten schickt Meta ein ``hub.challenge`` und erwartet
  es unverändert zurück, zusammen mit einem selbst vergebenen Token.
* **Signaturprüfung.** Jede Zustellung trägt ``X-Hub-Signature-256``. Ohne Prüfung
  könnte jeder, der die Adresse kennt, dem Agenten beliebige Nachrichten unterschieben
  — die Adresse ist öffentlich erreichbar, also ist das kein theoretischer Fall.
"""

import hashlib
import hmac
import logging

import httpx
from sqlalchemy import select

from app.core import channel_gateway as gw
from app.db.session import resilient_session
from app.models.agent import Agent

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.facebook.com/v21.0"
CONFIG_KEY = "whatsapp"


def channel_config(agent: Agent) -> dict:
    return ((agent.config or {}).get("channels") or {}).get(CONFIG_KEY) or {}


def is_enabled(agent: Agent) -> bool:
    cfg = channel_config(agent)
    return bool(cfg.get("enabled") and cfg.get("phone_number_id"))


def verify_signature(body: bytes, header: str, app_secret: str) -> bool:
    """Stammt diese Zustellung wirklich von Meta?

    Vergleich in konstanter Zeit — ein zeichenweiser Vergleich verrät über die
    Laufzeit, wie viele Zeichen stimmten, und macht die Signatur erratbar.
    Fehlt das Geheimnis, wird abgelehnt statt durchgewunken: eine ungeprüfte
    öffentliche Adresse ist schlimmer als ein nicht funktionierender Kanal.
    """
    if not app_secret or not header:
        return False
    expected = "sha256=" + hmac.new(
        app_secret.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, header.strip())


def extract_messages(payload: dict) -> list[dict]:
    """Die eigentlichen Nachrichten aus Metas verschachtelter Struktur holen.

    Der Aufbau ist entry[] → changes[] → value.messages[]. Zustellberichte
    (``statuses``) kommen über denselben Weg und sind KEINE Nachrichten — würde man
    sie mitverarbeiten, antwortete der Agent auf seine eigenen Zustellquittungen.
    """
    out: list[dict] = []
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            phone_number_id = ((value.get("metadata") or {}).get("phone_number_id") or "")
            contacts = {c.get("wa_id"): (c.get("profile") or {}).get("name", "")
                        for c in (value.get("contacts") or [])}
            for message in value.get("messages") or []:
                if message.get("type") != "text":
                    continue
                sender = message.get("from") or ""
                out.append({
                    "phone_number_id": phone_number_id,
                    "from": sender,
                    "sender_name": contacts.get(sender, ""),
                    "id": message.get("id") or "",
                    "text": ((message.get("text") or {}).get("body") or "").strip(),
                })
    return out


async def agent_for_phone_number(phone_number_id: str) -> Agent | None:
    """Welcher Agent hängt an dieser Nummer?

    Die Nummer ist die Zuordnung: eine WhatsApp-Nummer gehört genau einem Agenten,
    so wie ein Telegram-Bot-Token genau einem gehört.
    """
    async with resilient_session() as db:
        agents = (await db.execute(
            select(Agent).where(Agent.user_id.isnot(None))
        )).scalars().all()
    for agent in agents:
        if is_enabled(agent) and channel_config(agent).get("phone_number_id") == phone_number_id:
            return agent
    return None


async def handle_payload(redis, payload: dict) -> int:
    """Eine geprüfte Zustellung verarbeiten. Gibt zurück, wie viele ankamen."""
    delivered = 0
    for message in extract_messages(payload):
        if not message["text"]:
            continue
        agent = await agent_for_phone_number(message["phone_number_id"])
        if agent is None:
            logger.debug("[WhatsApp] keine Zuordnung fuer Nummer %s",
                         message["phone_number_id"])
            continue
        inbound = gw.InboundMessage(
            agent_id=agent.id,
            text=message["text"],
            channel=gw.CHANNEL_WHATSAPP,
            conversation_id=message["from"],
            message_id=message["id"],
            context={"to": message["from"],
                     "phone_number_id": message["phone_number_id"]},
            sender_name=message["sender_name"],
        )
        if await gw.deliver(redis, inbound):
            delivered += 1
    return delivered


async def send_reply(agent: Agent, context: dict, text: str) -> bool:
    from app.core.encryption import decrypt_token

    cfg = channel_config(agent)
    raw_token = cfg.get("access_token_enc") or ""
    if not (raw_token and context.get("to") and context.get("phone_number_id")):
        return False
    try:
        token = decrypt_token(raw_token)
    except Exception:  # noqa: BLE001
        logger.warning("[WhatsApp] Token von %s nicht entschluesselbar", agent.id)
        return False

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                f"{GRAPH_BASE}/{context['phone_number_id']}/messages",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "messaging_product": "whatsapp",
                    "to": context["to"],
                    "type": "text",
                    # WhatsApp kennt keine Auszeichnung ausser *fett* und _kursiv_,
                    # und Nachrichten sind auf 4096 Zeichen begrenzt.
                    "text": {"body": to_whatsapp(text)[:4096]},
                },
            )
        resp.raise_for_status()
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("[WhatsApp] Senden fehlgeschlagen: %s", e)
        return False


def to_whatsapp(text: str) -> str:
    """Agenten-Markdown in das Wenige, das WhatsApp kann."""
    import re

    out = text or ""
    out = re.sub(r"```[a-zA-Z0-9_+-]*\n?(.*?)```", r"\1", out, flags=re.S)
    out = re.sub(r"`([^`\n]+)`", r"\1", out)
    out = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"*\1*", out, flags=re.S)
    out = re.sub(r"(?m)^\s{0,3}#{1,6}\s+(.+?)\s*$", r"*\1*", out)
    out = re.sub(r"(?m)^\s*[-*]\s+", "• ", out)
    return out.strip()
