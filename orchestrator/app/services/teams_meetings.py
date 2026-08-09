"""Agenten in Teams-Terminen — als Mitschreiber oder als Beisitzer.

Der Wunsch war, einen Agenten zu einem Termin einladen zu können: entweder damit er
mitschreibt, oder damit er wirklich dabei ist und sich einbringt.

Nichts davon braucht eine neue Mechanik, und das ist der Kern des Entwurfs:

* **Beisitzer.** Ein Teams-Termin HAT einen Chat. Der Agent hängt für die Dauer des
  Termins an genau diesem Chat — über denselben Eingang, den ``teams_gateway`` schon
  bedient. Er liest also mit und kann sich einbringen, ohne dass ein zweiter Weg
  entsteht. (Eine Stimme in der Audiospur bräuchte einen Media-Bot im Azure Bot
  Service; das ist eine Registrierung beim Kunden, keine Frage des Aufwands — und im
  Chat mitzureden ist das, was in Besprechungen ohnehin die meisten tun.)
* **Mitschreiber.** Nach dem Termin wird das Teams-Transkript über Graph geholt und
  über den gemeinsamen Wissens-Schreibweg abgelegt — mit Embedding und Verknüpfung,
  also auffindbar, statt als Textdatei irgendwo zu liegen.

Eingeladen wird der Agent ganz normal über den Kalender: er hat mit
``ms_respond_event`` längst ein Werkzeug zum Annehmen. Neu ist nur, dass er die
Einladung von sich aus bemerkt und weiß, was sie für ihn bedeutet.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db.session import resilient_session
from app.models.agent import Agent

logger = logging.getLogger(__name__)

CONFIG_KEY = "meetings"

ROLE_SCRIBE = "mitschreiber"
ROLE_PARTICIPANT = "beisitzer"
ROLES = (ROLE_SCRIBE, ROLE_PARTICIPANT)

# So lange vor Beginn haengt sich der Agent an den Termin-Chat. Frueher waere
# unnoetig (im leeren Chat passiert nichts), spaeter verpasst er den Anfang.
JOIN_BEFORE = timedelta(minutes=5)
# So lange nach Ende bleibt er dran — Nachbesprechungen im Chat laufen weiter.
STAY_AFTER = timedelta(minutes=15)
# Das Transkript steht bei Teams erst nach dem Termin bereit, oft mit Verzug.
TRANSCRIPT_GRACE = timedelta(minutes=10)


def meeting_config(agent: Agent) -> dict:
    return ((agent.config or {}).get("channels") or {}).get(CONFIG_KEY) or {}


def is_enabled(agent: Agent) -> bool:
    cfg = meeting_config(agent)
    return bool(cfg.get("enabled") and cfg.get("role") in ROLES)


def role_of(agent: Agent) -> str:
    return meeting_config(agent).get("role") or ROLE_SCRIBE


def is_active_now(event: dict, now: datetime) -> bool:
    """Läuft dieser Termin gerade (samt Vor- und Nachlauf)?"""
    start, end = _window(event)
    if not start or not end:
        return False
    return (start - JOIN_BEFORE) <= now <= (end + STAY_AFTER)


def is_finished(event: dict, now: datetime) -> bool:
    """Vorbei und lange genug her, dass ein Transkript vorliegen kann."""
    _start, end = _window(event)
    return bool(end and now >= end + TRANSCRIPT_GRACE)


def _window(event: dict) -> tuple[datetime | None, datetime | None]:
    def _parse(node):
        raw = (node or {}).get("dateTime")
        if not raw:
            return None
        try:
            stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        # Graph liefert die Zone getrennt; ohne Zone ist es UTC.
        return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)

    return _parse(event.get("start")), _parse(event.get("end"))


def should_auto_accept(agent: Agent, event: dict) -> bool:
    """Einladung von selbst annehmen?

    Vorgabe ist NEIN. Ein Agent, der ungefragt jede Einladung annimmt, taucht in
    fremden Terminen auf und liest dort mit — das muss eine bewusste Entscheidung
    des Besitzers sein, keine Voreinstellung.
    """
    cfg = meeting_config(agent)
    if not cfg.get("auto_accept"):
        return False
    if (event.get("responseStatus") or {}).get("response") not in ("none", "notResponded", None):
        return False
    allowed = [str(o).lower() for o in (cfg.get("accept_from") or []) if str(o).strip()]
    if not allowed:
        return True
    organizer = (((event.get("organizer") or {}).get("emailAddress") or {})
                 .get("address") or "").lower()
    return any(organizer.endswith(a) or organizer == a for a in allowed)


def transcript_title(event: dict) -> str:
    subject = (event.get("subject") or "Besprechung").strip()
    start, _end = _window(event)
    stamp = start.strftime("%Y-%m-%d %H:%M") if start else ""
    return f"Besprechung {stamp} — {subject}"[:200]


class TeamsMeetingService:
    """Hängt Agenten an laufende Termine und holt Transkripte ab."""

    def __init__(self, redis=None):
        self.redis = redis
        self._running = False

    async def tick(self) -> dict | None:
        if self._running:
            return None
        self._running = True
        try:
            return await self._run()
        except Exception as e:  # noqa: BLE001 — darf den Scheduler nie kippen
            logger.warning("[Termine] Lauf fehlgeschlagen: %s", e)
            return None
        finally:
            self._running = False

    async def _run(self) -> dict | None:
        async with resilient_session() as db:
            agents = (await db.execute(
                select(Agent).where(Agent.user_id.isnot(None))
            )).scalars().all()
            targets = [a for a in agents if is_enabled(a)]
        if not targets:
            return None

        joined = scribed = accepted = 0
        for agent in targets:
            try:
                a, j, s = await self._handle_agent(agent)
                accepted += a
                joined += j
                scribed += s
            except Exception as e:  # noqa: BLE001
                logger.warning("[Termine] Agent %s: %s", agent.id, e)

        if not (joined or scribed or accepted):
            return None
        return {"agents": len(targets), "angenommen": accepted,
                "dabei": joined, "mitgeschrieben": scribed}

    async def _handle_agent(self, agent: Agent) -> tuple[int, int, int]:
        from app.services.teams_gateway import _graph_token
        from app.core.msgraph_mcp import _graph

        token = await _graph_token(agent.user_id)
        if not token:
            return 0, 0, 0

        now = datetime.now(timezone.utc)
        window_start = (now - timedelta(hours=4)).isoformat()
        window_end = (now + timedelta(hours=4)).isoformat()
        data = await _graph(
            "GET", "/me/calendarView", token,
            params={
                "startDateTime": window_start,
                "endDateTime": window_end,
                "$select": "id,subject,start,end,isOnlineMeeting,onlineMeeting,"
                           "organizer,responseStatus",
                "$top": "25",
            },
        )
        events = [e for e in data.get("value", []) if e.get("isOnlineMeeting")]

        accepted = joined = scribed = 0
        active_chats: list[str] = []

        for event in events:
            if should_auto_accept(agent, event):
                if await self._accept(token, event):
                    accepted += 1

            if is_active_now(event, now):
                chat_id = await self._meeting_chat_id(token, event)
                if chat_id:
                    active_chats.append(chat_id)
                    joined += 1
            elif is_finished(event, now) and role_of(agent) == ROLE_SCRIBE:
                if await self._ingest_transcript(agent, token, event):
                    scribed += 1

        await self._sync_watched_chats(agent, active_chats)
        return accepted, joined, scribed

    async def _accept(self, token: str, event: dict) -> bool:
        from app.core.msgraph_mcp import _graph

        try:
            await _graph("POST", f"/me/events/{event['id']}/accept", token,
                         json={"sendResponse": True})
            logger.info("[Termine] Einladung angenommen: %s", event.get("subject"))
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("[Termine] Annehmen fehlgeschlagen: %s", e)
            return False

    @staticmethod
    async def _meeting_chat_id(token: str, event: dict) -> str:
        """Der Chat, der zu diesem Termin gehört.

        Genau der Chat, in dem während der Besprechung geschrieben wird — dort hängt
        sich der Beisitzer ein.
        """
        online = event.get("onlineMeeting") or {}
        join_url = online.get("joinUrl") or ""
        if not join_url:
            return ""
        from app.core.msgraph_mcp import _graph
        from urllib.parse import quote

        try:
            data = await _graph(
                "GET", "/me/onlineMeetings", token,
                params={"$filter": f"JoinWebUrl eq '{join_url}'"},
            )
            meetings = data.get("value", [])
            if meetings:
                return meetings[0].get("chatInfo", {}).get("threadId") or ""
        except Exception as e:  # noqa: BLE001
            logger.debug("[Termine] Chat nicht ermittelbar: %s", e)
        return ""

    async def _sync_watched_chats(self, agent: Agent, chats: list[str]) -> None:
        """Die Termin-Chats in die überwachten Chats des Agenten eintragen.

        Bewusst über DIESELBE Liste, die der Teams-Eingang ohnehin abfragt — der
        Beisitzer ist damit kein Sonderfall, sondern ein Chat mehr. Beim Ende des
        Termins fällt er wieder heraus, sonst hinge der Agent für immer an jeder
        Besprechung, in der er je war.
        """
        from sqlalchemy.orm.attributes import flag_modified

        async with resilient_session() as db:
            fresh = await db.get(Agent, agent.id)
            if fresh is None:
                return
            config = fresh.config or {}
            channels = config.setdefault("channels", {})
            teams_cfg = channels.setdefault("teams", {})

            manual = list(teams_cfg.get("chat_ids_manual") or
                          [c for c in (teams_cfg.get("chat_ids") or [])
                           if c not in (teams_cfg.get("chat_ids_meetings") or [])])
            wanted = sorted(set(chats))
            if teams_cfg.get("chat_ids_meetings") == wanted:
                return

            teams_cfg["chat_ids_manual"] = manual
            teams_cfg["chat_ids_meetings"] = wanted
            teams_cfg["chat_ids"] = sorted(set(manual) | set(wanted))
            # Der Agent soll in einer Besprechung nicht nur auf Zuruf reagieren.
            if wanted:
                teams_cfg["enabled"] = True
            fresh.config = config
            flag_modified(fresh, "config")
            await db.commit()
            logger.info("[Termine] %s haengt an %d Termin-Chat(s)", agent.id, len(wanted))

    async def _ingest_transcript(self, agent: Agent, token: str, event: dict) -> bool:
        """Das Teams-Transkript holen und als Wissenseintrag ablegen.

        Über den gemeinsamen Schreibweg, also mit Embedding und Verknüpfung — sonst
        läge das Protokoll da, wäre aber weder auffindbar noch mit irgendetwas
        verbunden.
        """
        from app.core.knowledge_write import write_entry
        from app.core.msgraph_mcp import _graph, _graph_bytes

        title = transcript_title(event)

        async with resilient_session() as db:
            from app.models.knowledge import KnowledgeEntry

            exists = await db.scalar(
                select(KnowledgeEntry.id).where(
                    KnowledgeEntry.title == title,
                    KnowledgeEntry.user_id == agent.user_id,
                )
            )
        if exists:
            return False   # schon mitgeschrieben

        online = event.get("onlineMeeting") or {}
        join_url = online.get("joinUrl") or ""
        if not join_url:
            return False

        try:
            meetings = (await _graph(
                "GET", "/me/onlineMeetings", token,
                params={"$filter": f"JoinWebUrl eq '{join_url}'"},
            )).get("value", [])
            if not meetings:
                return False
            meeting_id = meetings[0].get("id")
            transcripts = (await _graph(
                "GET", f"/me/onlineMeetings/{meeting_id}/transcripts", token
            )).get("value", [])
            if not transcripts:
                return False
            content = await _graph_bytes(
                "GET",
                f"/me/onlineMeetings/{meeting_id}/transcripts/{transcripts[0]['id']}/content",
                token,
            )
            text = _vtt_to_text(content.decode("utf-8", errors="replace")
                                if isinstance(content, bytes) else str(content))
        except Exception as e:  # noqa: BLE001 — kein Transkript ist kein Fehlerfall
            logger.debug("[Termine] Transkript nicht verfuegbar: %s", e)
            return False

        if not text.strip():
            return False

        async with resilient_session() as db:
            await write_entry(
                db, user_id=agent.user_id, title=title,
                content=f"{text[:12000]}\n\n---\nMitgeschrieben aus Microsoft Teams.",
                tags=["besprechung", "transkript"], author=agent.id,
            )
        logger.info("[Termine] Transkript abgelegt: %s", title)
        return True


def _vtt_to_text(raw: str) -> str:
    """Aus dem WebVTT-Transkript lesbaren Fliesstext machen.

    Teams liefert Zeitmarken, Kennungen und Sprecher-Tags. Für das Protokoll zählt,
    WER WAS gesagt hat — der Rest ist Ballast, der im Prompt nur Platz kostet.
    """
    import re

    lines: list[str] = []
    last_speaker = ""
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line or line == "WEBVTT" or "-->" in line:
            continue
        if re.fullmatch(r"[0-9a-fA-F-]{8,}", line) or line.isdigit():
            continue
        match = re.match(r"<v ([^>]+)>(.*?)(?:</v>)?$", line)
        if match:
            speaker, said = match.group(1).strip(), match.group(2).strip()
            if not said:
                continue
            if speaker == last_speaker and lines:
                lines[-1] += f" {said}"
            else:
                lines.append(f"{speaker}: {said}")
                last_speaker = speaker
            continue
        clean = re.sub(r"<[^>]+>", "", line).strip()
        if clean:
            if lines:
                lines[-1] += f" {clean}"
            else:
                lines.append(clean)
    return "\n".join(lines)
