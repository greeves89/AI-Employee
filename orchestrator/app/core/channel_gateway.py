"""EIN Eingang für Nachrichten aus allen Kanälen.

Telegram war lange der einzige Weg von außen zu einem Agenten, und der Ablauf steckte
entsprechend im Telegram-Bot: Nachricht festhalten, ins Second Brain aufnehmen, in die
Warteschlange des Agenten legen. Für Teams und Slack denselben Ablauf noch einmal zu
schreiben hieße, ihn dreimal zu pflegen — und beim nächsten Fix würde einer der drei
vergessen. Genau dieses Muster hat in diesem Projekt schon mehrfach zugeschlagen.

Deshalb liegt der Ablauf hier, und die Kanäle liefern nur noch, was bei ihnen
unterschiedlich ist: woher die Nachricht kam und wie die Antwort zurückgeht.

Die Sitzungskennung ist ``{kanal}:{externe_id}`` — dieselbe Form, die Telegram schon
benutzt (``telegram:12345``). Damit bleibt ein Gespräch pro Chat getrennt, und die
Chat-Historie zeigt für jeden Kanal eine eigene Spur.
"""

import json
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Kanalnamen. Die Sitzungskennung baut darauf auf, also sind das keine Beschriftungen,
# sondern Teil der gespeicherten Daten — nicht einfach umbenennen.
CHANNEL_TELEGRAM = "telegram"
CHANNEL_TEAMS = "teams"
CHANNEL_SLACK = "slack"
CHANNEL_WHATSAPP = "whatsapp"

KNOWN_CHANNELS = (CHANNEL_TELEGRAM, CHANNEL_TEAMS, CHANNEL_SLACK, CHANNEL_WHATSAPP)

# Präfix der Nachrichten-Kennung je Kanal. NICHT aus dem Kanalnamen ableiten: die
# Telegram-Rückstrecke filtert Antworten mit ``msg_id.startswith("tg-")``, und ein
# abgeleitetes "te-" hätte dazu geführt, dass der Agent antwortet und niemand es sieht.
# Ein neuer Kanal braucht hier einen eigenen, eindeutigen Eintrag.
CHANNEL_PREFIX = {
    CHANNEL_TELEGRAM: "tg",
    CHANNEL_TEAMS: "tm",
    CHANNEL_SLACK: "sl",
    CHANNEL_WHATSAPP: "wa",
}


@dataclass
class InboundMessage:
    """Eine Nachricht von außen, unabhängig davon, wo sie herkam."""

    agent_id: str
    text: str
    channel: str
    # Die Kennung des Gesprächs beim Anbieter (Telegram-Chat, Teams-Chat, Slack-Kanal).
    conversation_id: str
    # Die Kennung DIESER Nachricht beim Anbieter — verhindert Doppelverarbeitung,
    # wenn ein Poller dieselbe Nachricht zweimal sieht.
    message_id: str
    # Alles Kanalspezifische, das der Agent zum Antworten braucht.
    context: dict = field(default_factory=dict)
    # Wer geschrieben hat (nur zur Anzeige).
    sender_name: str = ""

    @property
    def session_id(self) -> str:
        return f"{self.channel}:{self.conversation_id}"

    @property
    def queue_message_id(self) -> str:
        """Die Kennung, unter der die Nachricht in der Warteschlange steht.

        Das Präfix entscheidet, ob der Kanal die Antwort später wiedererkennt —
        siehe ``CHANNEL_PREFIX``.
        """
        return f"{CHANNEL_PREFIX.get(self.channel, self.channel[:2])}-{self.message_id}"


async def already_seen(redis, message: InboundMessage, ttl: int = 24 * 3600) -> bool:
    """Wurde diese Nachricht schon verarbeitet?

    Für Kanäle, die abgefragt werden (Teams über Graph), ist das unverzichtbar: zwei
    Durchläufe sehen dieselbe Nachricht, und der Agent würde zweimal antworten. Bei
    Telegram, das die Nachricht aktiv zustellt, ist es eine billige Zusatzsicherung.
    """
    key = f"gateway:seen:{message.channel}:{message.message_id}"
    try:
        return not await redis.client.set(key, "1", nx=True, ex=ttl)
    except Exception:  # noqa: BLE001 — ohne Redis lieber doppelt als gar nicht
        logger.debug("Doppelt-Erkennung nicht verfuegbar", exc_info=True)
        return False


async def persist_message(message: InboundMessage) -> None:
    """Die Nachricht in der Chat-Historie festhalten.

    Zwei Gründe, warum das nicht optional ist: das Gespräch taucht sonst in der
    Oberfläche nicht auf, und die Kompaktierung übergeht es beim Aufbau des
    Gedächtnisses — die Unterhaltung wäre für den Agenten später nicht mehr da.
    """
    try:
        from app.db.session import async_session_factory
        from app.models.chat_message import ChatMessage

        async with async_session_factory() as db:
            db.add(ChatMessage(
                agent_id=message.agent_id,
                session_id=message.session_id,
                message_id=message.queue_message_id,
                role="user",
                content=message.text,
            ))
            await db.commit()
    except Exception as e:  # noqa: BLE001 — Zustellung geht vor
        logger.warning("[Gateway] Nachricht konnte nicht gespeichert werden: %s", e)


async def capture_if_worthwhile(message: InboundMessage) -> None:
    """Link, langer Text oder ein ausdrückliches „merk dir das" ins Second Brain.

    Vollständig abgesichert: Am 2026-08-06 hat genau so ein Beiwerk schon einmal die
    Zustellung verhindert. Aufheben ist nett, Zustellen ist Pflicht.
    """
    try:
        from app.core.capture import capture
        from app.db.session import async_session_factory
        from app.models.agent import Agent

        async with async_session_factory() as db:
            agent = await db.get(Agent, message.agent_id)
            owner = getattr(agent, "user_id", None)
            if not owner:
                return
            entry, reason = await capture(
                db, user_id=owner, text=message.text,
                source=message.channel.capitalize(), author=message.agent_id,
            )
            if entry is not None:
                logger.info("[Gateway] Auto-Capture (%s) aus %s -> Eintrag %s",
                            reason, message.channel, entry.id)
    except Exception as e:  # noqa: BLE001
        logger.warning("[Gateway] Auto-Capture fehlgeschlagen: %s", e)


async def enqueue(redis, message: InboundMessage) -> None:
    """In die Warteschlange des Agenten legen — dieselbe, die auch der Web-Chat nutzt.

    Es gibt bewusst keine zweite Warteschlange je Kanal: der Agent soll eine
    Unterhaltung führen, nicht vier getrennte, und die Live-Steuerung (Nachricht
    mitten im Lauf) hängt an genau dieser einen Liste.
    """
    payload = json.dumps({
        "id": message.queue_message_id,
        "text": message.text,
        "model": None,
        "chat_session_id": message.session_id,
        # Der Kanal steht unter seinem eigenen Namen im Kontext, damit die Laufzeit
        # weiß, wohin die Antwort geht. `telegram` bleibt zusätzlich erhalten, weil
        # die Agenten-Laufzeiten diesen Schlüssel bereits auswerten.
        "channel": message.channel,
        message.channel: message.context,
        **({"telegram": message.context} if message.channel == CHANNEL_TELEGRAM else {}),
    })
    await redis.client.lpush(f"agent:{message.agent_id}:chat", payload)

    # Den Kanal-Kontext getrennt hinterlegen. Die Laufzeiten reichen ihn nicht
    # zuverlaessig bis in die Antwort durch; ohne diesen Rueckgriff wuesste der
    # Rueckweg nicht, in welchen Chat die fertige Antwort gehoert — sie waere
    # erzeugt, aber unzustellbar. Eine Stunde reicht weit ueber jeden Lauf hinaus.
    try:
        await redis.client.set(
            f"gateway:ctx:{message.queue_message_id}",
            json.dumps({**message.context, "channel": message.channel}),
            ex=3600,
        )
    except Exception:  # noqa: BLE001 — Zustellung geht vor
        logger.debug("[Gateway] Kontext konnte nicht hinterlegt werden", exc_info=True)


async def deliver(redis, message: InboundMessage, *, capture: bool = True) -> bool:
    """Der vollständige Weg von außen zum Agenten. Gibt zurück, ob zugestellt wurde.

    Reihenfolge ist Absicht: erst die Doppelt-Prüfung (sonst antwortet der Agent
    zweimal), dann speichern und aufheben, zuletzt einreihen. Das Einreihen kommt
    ZULETZT, damit der Agent nicht schon antwortet, während die Nachricht noch nicht
    in der Historie steht.
    """
    if not message.text or not message.agent_id:
        return False
    if await already_seen(redis, message):
        logger.debug("[Gateway] %s/%s bereits verarbeitet", message.channel, message.message_id)
        return False

    await persist_message(message)
    if capture:
        await capture_if_worthwhile(message)
    await enqueue(redis, message)
    logger.info("[Gateway] %s -> Agent %s (Sitzung %s)",
                message.channel, message.agent_id, message.session_id)
    return True
