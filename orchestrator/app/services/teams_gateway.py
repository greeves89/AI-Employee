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
from app.core import gebundene_freigabe
from app.db.session import resilient_session
from app.models.agent import Agent

logger = logging.getLogger(__name__)

CONFIG_KEY = "teams"
# Redis-Namensraum fuer die Laufmarken dieses Kanals.
KEY_PREFIX = "teams"
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


async def _agenten_token(agent) -> str | None:
    """Token des Agenten: eigenes Microsoft-Konto, sonst das des Besitzers (core/agenten_konto)."""
    try:
        from app.core.agenten_konto import token_fuer

        async with resilient_session() as db:
            return await token_fuer(db, agent)
    except Exception as e:  # noqa: BLE001 — ohne Token ist Teams fuer diesen Agenten aus
        logger.debug("[Teams] kein Graph-Token fuer Agent %s: %s", getattr(agent, "id", "?"), e)
        return None


async def _eigene_kennung(agent: Agent, token: str) -> str | None:
    """Die Microsoft-Kennung des Agenten — nur mit eigenem Konto (sonst wäre es die des
    Besitzers, und dessen Nachrichten an den Agenten würden verschluckt)."""
    from app.core import agenten_konto
    from app.core.msgraph_mcp import _graph

    schluessel = f"{KEY_PREFIX}:ich:{agent.id}"
    try:
        client = (await _redis()).client
        gemerkt = await client.get(schluessel)
        if gemerkt:
            return gemerkt.decode() if isinstance(gemerkt, bytes) else str(gemerkt)
        async with resilient_session() as db:
            if await agenten_konto._zeile(db, agent.id) is None:
                return None
        kennung = str((await _graph("GET", "/me?$select=id", token)).get("id") or "")
        if kennung:
            await client.set(schluessel, kennung, ex=24 * 3600)
        return kennung or None
    except Exception as e:  # noqa: BLE001 — Halt (1) greift weiter
        logger.debug("[Teams] eigene Kennung von Agent %s nicht ermittelt: %s", agent.id, e)
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
        token = await _agenten_token(agent)
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
            # Eigene Nachrichten NIE zurueckverarbeiten — sonst antwortet der Agent auf sich
            # selbst, bis das Budget leer ist. Zwei Halte, unabhängig voneinander:
            # (1) was er gesendet hat, steht in seiner Liste (gilt auch, wenn er über das Konto
            #     seines Besitzers schreibt — dessen eigene Nachrichten bleiben dann sichtbar);
            # (2) mit eigenem Microsoft-Konto: alles von seiner eigenen Kennung (/me).
            if await _ist_eigen(agent.id, str(message.get("id") or "")):
                continue
            if sender_id and sender_id in {cfg.get("own_user_id"), await _eigene_kennung(agent, token)} - {None, ""}:
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


# ─── Senden nach Regeln (Plan agenten-identitaet-m365, Phase 3) ────────────

#: Gebundene Freigabe für Teams im Modus „Entwurf“ (core/gebundene_freigabe).
ART_SENDEN = "teams_senden"
#: Wie lange gesendete Nachrichten als eigene erkannt werden (Schleifenschutz).
EIGENE_TTL_S = 3 * 24 * 3600
_redis_dienst = None


async def _redis():
    """Geteilte Redis-Verbindung für den Schleifenschutz (Senden kennt keinen Request)."""
    global _redis_dienst
    if _redis_dienst is None:
        from app.config import settings
        from app.services.redis_service import RedisService

        dienst = RedisService(settings.redis_url)
        await dienst.connect()
        _redis_dienst = dienst
    return _redis_dienst


def _pfad(ziel: dict) -> str | None:
    from app.core.msgraph_mcp import _gid

    if ziel.get("chat_id"):
        return f"/chats/{_gid(ziel['chat_id'])}/messages"
    if ziel.get("team_id") and ziel.get("channel_id"):
        return f"/teams/{_gid(ziel['team_id'])}/channels/{_gid(ziel['channel_id'])}/messages"
    return None


def _ziel(context: dict) -> dict:
    if context.get("chat_id"):
        return {"chat_id": str(context["chat_id"])}
    if context.get("team_id") and context.get("channel_id"):
        return {"team_id": str(context["team_id"]), "channel_id": str(context["channel_id"])}
    return {}


async def _als_eigen_merken(agent_id: str, message_id: str | None) -> None:
    if not message_id:
        return
    try:
        client = (await _redis()).client
        await client.sadd(f"{KEY_PREFIX}:eigene:{agent_id}", str(message_id))
        await client.expire(f"{KEY_PREFIX}:eigene:{agent_id}", EIGENE_TTL_S)
    except Exception:  # noqa: BLE001 — der Schleifenschutz hat einen zweiten Halt (own_user_id)
        logger.warning("[Teams] Gesendete Nachricht von Agent %s nicht als eigene gemerkt", agent_id)


async def _ist_eigen(agent_id: str, message_id: str) -> bool:
    try:
        return bool(await (await _redis()).client.sismember(f"{KEY_PREFIX}:eigene:{agent_id}", str(message_id)))
    except Exception:  # noqa: BLE001
        return False


async def _posten(agent: Agent, ziel: dict, text: str, format_: str) -> str:
    """Wirklich senden — nur über :func:`senden_nach_regeln` oder die freigegebene Handlung."""
    from app.core.msgraph_mcp import _graph

    pfad = _pfad(ziel)
    token = await _agenten_token(agent)
    if not pfad or not token:
        raise RuntimeError("Kein Ziel oder kein Microsoft-Zugang")
    inhalt = _to_html(text) if format_ == "html" else text
    antwort = await _graph("POST", pfad, token, content=json.dumps(
        {"body": {"contentType": "html" if format_ == "html" else "text", "content": inhalt}}))
    await _als_eigen_merken(agent.id, (antwort or {}).get("id"))
    return str((antwort or {}).get("id") or "")


def _ziel_beschreibung(ziel: dict) -> str:
    if ziel.get("chat_id"):
        return f"Teams-Chat {ziel['chat_id'][:60]}"
    return f"Teams-Kanal {str(ziel.get('channel_id', ''))[:60]} (Team {str(ziel.get('team_id', ''))[:40]})"


async def senden_nach_regeln(agent: Agent, ziel: dict, text: str, *, format_: str = "html",
                             quelle: str = "kanal") -> str:
    """DER Weg, auf dem ein Agent in Teams schreibt — Kanal-Antwort und Werkzeug.

    Dieselben Regeln wie Mail (core/ms_access.sendemodus): Nur-Lesen-Schalter, Einstellung des
    Agenten, Sekretariat, Rechte-Matrix „E-Mail / M365 senden“; außerdem muss die
    Microsoft-Anbindung dem Agenten zugewiesen sein. Im Modus „Entwurf“ wird nicht gesendet,
    sondern eine gebundene Freigabe angelegt (Art ``teams_senden``): Nach dem Freigeben sendet
    der Server genau diesen Text, einmal.

    Liefert ``gesendet``, ``freigabe``, ``aus`` oder ``fehler``.
    """
    from app.core import ms_access
    from app.core.faehigkeiten import anbindung_zugewiesen

    if not _pfad(ziel) or not (text or "").strip():
        return "fehler"
    async with resilient_session() as db:
        if not await anbindung_zugewiesen(db, agent.id, "microsoft"):
            logger.warning("[Teams] Agent %s: Microsoft-Anbindung nicht zugewiesen — nicht gesendet", agent.id)
            return "aus"
        modus = await ms_access.sendemodus(db, agent)
        if modus == ms_access.LESEN:
            logger.warning("[Teams] Agent %s darf in Microsoft 365 nur lesen — %s nicht gesendet", agent.id, quelle)
            return "aus"
        if modus == ms_access.ENTWURF:
            try:
                await gebundene_freigabe.anfragen(
                    db, agent.id, ART_SENDEN, {"ziel": ziel, "text": text, "format": format_},
                    titel="Teams-Nachricht senden",
                    felder=[("An", _ziel_beschreibung(ziel)), ("Text", text)],
                    werkzeug="teams_senden")
                await db.commit()
                return "freigabe"
            except Exception as e:  # noqa: BLE001 — z. B. zu lang für eine Freigabe
                logger.warning("[Teams] Freigabe für Agent %s nicht angelegt: %s", agent.id, e)
                return "fehler"
    try:
        await _posten(agent, ziel, text, format_)
        return "gesendet"
    except Exception as e:  # noqa: BLE001
        logger.warning("[Teams] Senden fehlgeschlagen: %s", e)
        return "fehler"


@gebundene_freigabe.ausfuehrer(ART_SENDEN)
async def _freigegeben_senden(db, daten: dict, agent_id: str) -> str:
    """Freigegeben: genau diesen Text senden — wenn der Agent jetzt noch senden darf."""
    from fastapi import HTTPException

    from app.core import ms_access

    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent nicht gefunden")
    # Zwischen Anfrage und Freigabe kann der Admin auf Nur-Lesen gestellt haben.
    if await ms_access.sendemodus(db, agent) == ms_access.LESEN:
        raise HTTPException(status_code=409, detail="Microsoft 365 ist inzwischen nur lesend — nicht gesendet.")
    await _posten(agent, dict(daten.get("ziel") or {}), str(daten.get("text") or ""),
                  "text" if daten.get("format") == "text" else "html")
    return "In Teams gesendet."


async def send_reply(agent: Agent, context: dict, text: str) -> bool:
    """Eine Antwort zurück nach Teams — nach denselben Regeln wie jedes Senden."""
    return await senden_nach_regeln(agent, _ziel(context), text, quelle="kanal") in ("gesendet", "freigabe")


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
