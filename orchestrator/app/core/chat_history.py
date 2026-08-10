"""Gespräche verzweigen, zurückspulen, zusammenfassen und benennen (#538).

Vier Dinge, die im Chat fehlten, und alle vier greifen auf denselben Gedanken zu:
**„die Nachrichten bis hierher"**. Deshalb liegen sie zusammen statt in vier Ecken.

* **Verzweigen** — ab einer Nachricht in einem neuen Gespräch weiterreden, ohne den
  bisherigen Verlauf zu verlieren. Das Original bleibt unangetastet.
* **Zurückspulen** — alles nach einer Nachricht verwerfen. Anders als Verzweigen
  **löscht** das; deshalb gibt es einen Rückweg (siehe ``rewind``).
* **Zusammenfassen** — ein langes Gespräch als kurzen Stand in ein frisches
  übernehmen. Der Verlauf bleibt, wo er ist.
* **Titel** — die Liste zeigte die rohe letzte Nachricht auf 80 Zeichen gekürzt.
  Aus dem ersten Austausch lässt sich eine Überschrift bilden, die man wiedererkennt.
"""

import logging
import re
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.log_redaction import scrub_log
from app.models.chat_message import ChatMessage
from app.models.chat_session import ChatSession

logger = logging.getLogger(__name__)

# Mehr als das liest niemand in einer schmalen Liste.
TITLE_MAX = 60
# Ab so vielen Nachrichten lohnt eine Zusammenfassung; darunter ist der Verlauf
# kuerzer als seine eigene Zusammenfassung.
SUMMARY_MIN_MESSAGES = 6
# Wie viele der juengsten Nachrichten beim Verdichten woertlich bleiben. Dieselbe
# Ueberlegung wie im Kompaktierer des Agenten (RECENT_WINDOW_MESSAGES = 24, dort
# fuer den Modellkontext): die juengste Werkzeug-Ein- und -Ausgabe — Pfade,
# Kennungen, Werte — ist zusammengefasst wertlos. Hier kleiner, weil es um einen
# Chat geht und nicht um einen werkzeuglastigen Auftragslauf.
KEEP_VERBATIM = 8

# Woran eine Verdichtung im Verlauf zu erkennen ist.
COMPACT_MARKER = "[Verdichtet — der aeltere Verlauf steht zusammengefasst hier]"

_FILLER = re.compile(
    r"^\s*(hallo|hi|hey|guten (morgen|tag|abend)|moin|bitte|danke|kannst du|"
    r"koenntest du|könntest du|ich brauche|ich moechte|ich möchte|mach mal|"
    r"kurz mal|sag mal)\b[\s,:-]*",
    re.IGNORECASE,
)


def derive_title(first_user_message: str) -> str:
    """Aus der ersten Nutzernachricht eine Überschrift bilden.

    Bewusst ohne Sprachmodell: ein Titel ist es nicht wert, für jedes Gespräch ein
    Modell zu befragen — das kostet bei hundert Gesprächen hundert Aufrufe, und der
    erste Satz sagt fast immer schon, worum es geht.

    Entfernt wird die Anrede davor („Hallo, kannst du bitte …"), denn sonst hiessen
    alle Gespräche gleich.
    """
    text = " ".join((first_user_message or "").split())
    if not text:
        return ""
    # Anrede/Floskel abschneiden, aber nur einmal — sonst frisst es den Inhalt.
    stripped = _FILLER.sub("", text, count=1)
    # Satzzeichen, das von der Floskel uebrig bleibt, mit abraeumen: aus
    # „Moin! Ich brauche …" wurde sonst der Titel „!", weil der Satztrenner
    # danach das Ausrufezeichen als ersten Satz nimmt.
    stripped = stripped.lstrip(" ,.;:!?-–—").strip()
    if stripped:
        text = stripped

    # Erster Satz, falls er nicht absurd lang und nicht bloss ein Fragment ist.
    sentence = re.split(r"(?<=[.!?])\s", text)[0].strip()
    if 4 <= len(sentence) <= TITLE_MAX:
        text = sentence

    if len(text) > TITLE_MAX:
        cut = text[:TITLE_MAX].rsplit(" ", 1)[0]
        text = (cut or text[:TITLE_MAX]).rstrip(" ,;:-") + "…"
    return text[:1].upper() + text[1:] if text else ""


async def messages_of(db: AsyncSession, agent_id: str, session_id: str) -> list[ChatMessage]:
    return list((await db.execute(
        select(ChatMessage)
        .where(ChatMessage.agent_id == agent_id, ChatMessage.session_id == session_id)
        .order_by(ChatMessage.timestamp, ChatMessage.id)
    )).scalars().all())


def split_at(messages: list[ChatMessage], message_id: str) -> tuple[list, list]:
    """``(bis einschliesslich, danach)``. Unbekannte Kennung → alles davor."""
    for index, msg in enumerate(messages):
        if msg.message_id == message_id:
            return messages[: index + 1], messages[index + 1:]
    return messages, []


async def ensure_title(db: AsyncSession, agent_id: str, session_id: str) -> str | None:
    """Einem Gespräch einen Titel geben, falls es noch keinen hat.

    Ein von Hand vergebener Titel wird NIE überschrieben — sonst verliert jemand
    seine Benennung, weil er noch eine Nachricht geschickt hat.
    """
    row = (await db.execute(
        select(ChatSession).where(
            ChatSession.agent_id == agent_id, ChatSession.session_id == session_id
        )
    )).scalar_one_or_none()
    if row is not None and (row.title or "").strip():
        return row.title

    first_user = (await db.execute(
        select(ChatMessage.content)
        .where(
            ChatMessage.agent_id == agent_id,
            ChatMessage.session_id == session_id,
            ChatMessage.role == "user",
        )
        .order_by(ChatMessage.timestamp, ChatMessage.id)
        .limit(1)
    )).scalar_one_or_none()
    title = derive_title(first_user or "")
    if not title:
        return None

    if row is None:
        row = ChatSession(agent_id=agent_id, session_id=session_id, title=title)
        db.add(row)
    else:
        row.title = title
    await db.flush()
    return title


async def fork(db: AsyncSession, agent_id: str, session_id: str,
               message_id: str, *, new_session_id: str | None = None) -> dict:
    """Ab einer Nachricht in einem neuen Gespräch weiterreden.

    Kopiert, verschiebt nicht: das Original bleibt vollständig. Genau das
    unterscheidet Verzweigen vom Zurückspulen — wer verzweigt, will beides behalten.
    """
    messages = await messages_of(db, agent_id, session_id)
    keep, _rest = split_at(messages, message_id)
    if not keep:
        return {"ok": False, "reason": "Nachricht nicht gefunden"}

    target = new_session_id or f"fork-{uuid.uuid4().hex[:10]}"
    now = datetime.now(timezone.utc)
    for offset, msg in enumerate(keep):
        db.add(ChatMessage(
            agent_id=agent_id,
            session_id=target,
            # Neue Kennung: die alte steckt in Antwort-Zuordnungen und Kanal-Kontexten,
            # zweimal dieselbe waere eine Verwechslung mit Ansage.
            message_id=f"fk-{uuid.uuid4().hex[:12]}",
            role=msg.role,
            content=msg.content,
            tool_calls=msg.tool_calls,
            meta={**(msg.meta or {}), "forked_from": session_id},
            cost_usd=None,        # Kosten hingen am Originallauf, nicht an der Kopie
            timestamp=msg.timestamp or now,
        ))

    source_title = await ensure_title(db, agent_id, session_id)
    db.add(ChatSession(
        agent_id=agent_id, session_id=target,
        title=f"Abzweig: {source_title}"[:TITLE_MAX] if source_title else "Abzweig",
    ))
    await db.flush()
    logger.info("[Chat] %s ab %s nach %s verzweigt (%d Nachrichten)",
                scrub_log(session_id), scrub_log(message_id), scrub_log(target), len(keep))
    return {"ok": True, "session_id": target, "copied": len(keep)}


async def rewind(db: AsyncSession, agent_id: str, session_id: str,
                 message_id: str) -> dict:
    """Alles NACH einer Nachricht verwerfen.

    Das löscht wirklich. Deshalb wird der verworfene Teil vorher in ein
    Sicherungs-Gespräch kopiert — ein Fehlklick in einer Nachrichtenliste ist zu
    leicht passiert, um ihn unumkehrbar zu machen. Die Sicherung taucht in der Liste
    auf und kann von Hand gelöscht werden.
    """
    messages = await messages_of(db, agent_id, session_id)
    keep, drop = split_at(messages, message_id)
    if not keep:
        return {"ok": False, "reason": "Nachricht nicht gefunden"}
    if not drop:
        return {"ok": True, "removed": 0, "backup_session_id": None}

    backup = f"undo-{uuid.uuid4().hex[:10]}"
    for msg in drop:
        db.add(ChatMessage(
            agent_id=agent_id, session_id=backup,
            message_id=f"uw-{uuid.uuid4().hex[:12]}",
            role=msg.role, content=msg.content, tool_calls=msg.tool_calls,
            meta={**(msg.meta or {}), "rewound_from": session_id},
            timestamp=msg.timestamp,
        ))
        await db.delete(msg)

    source_title = await ensure_title(db, agent_id, session_id)
    db.add(ChatSession(
        agent_id=agent_id, session_id=backup,
        title=f"Verworfen: {source_title}"[:TITLE_MAX] if source_title else "Verworfen",
    ))
    await db.flush()
    logger.info("[Chat] %s auf %s zurueckgespult (%d verworfen, Sicherung %s)",
                scrub_log(session_id), scrub_log(message_id), len(drop), scrub_log(backup))
    return {"ok": True, "removed": len(drop), "backup_session_id": backup}


def build_summary(messages: list[ChatMessage], limit: int = 2000) -> str:
    """Einen lesbaren Stand aus dem Verlauf bauen.

    Ohne Sprachmodell: der Verlauf wird verdichtet, nicht interpretiert. Das ist
    ehrlicher — eine Modell-Zusammenfassung erfindet im Zweifel etwas, und für das
    Weiterreden zählt, was wirklich gesagt wurde. Wer eine echte Verdichtung will,
    hat mit der Kompaktierung des Agenten bereits eine.
    """
    lines: list[str] = []
    for msg in messages:
        if msg.role not in ("user", "assistant"):
            continue
        text = " ".join((msg.content or "").split())
        if not text:
            continue
        who = "Du" if msg.role == "user" else "Agent"
        lines.append(f"{who}: {text[:400]}")
    body = "\n".join(lines)
    if len(body) > limit:
        # Von hinten kuerzen: das Ende eines Gespraechs ist der aktuelle Stand.
        body = "… (Anfang gekürzt)\n" + body[-limit:]
    return body


async def compact_session(db: AsyncSession, agent_id: str, session_id: str) -> dict:
    """Den Verlauf **im selben Gespräch** verdichten (``/compact``).

    Der Unterschied zu ``summarize_to_new_session`` ist der, den ein Nutzer meint,
    wenn er „compact" sagt: er will **hier** weiterreden, nur mit weniger Ballast.
    Ein frisches Gespräch wäre eine andere Antwort auf eine andere Frage.

    Die alten Nachrichten werden **nicht gelöscht**, sondern markiert. Für den
    Menschen bleibt der Verlauf lesbar; wer den Kontext für das Modell baut,
    überspringt sie. Löschen wäre unumkehrbar, und niemand verdichtet in der
    Absicht, etwas zu verlieren.

    Die letzten ``KEEP_VERBATIM`` Nachrichten bleiben unangetastet — dieselbe
    Überlegung wie im Kompaktierer des Agenten: die jüngste Werkzeug-Ein- und
    -Ausgabe (Pfade, Kennungen, Werte) ist zusammengefasst wertlos.
    """
    messages = await messages_of(db, agent_id, session_id)
    live = [m for m in messages if not (m.meta or {}).get("compacted")]
    if len(live) < SUMMARY_MIN_MESSAGES:
        return {"ok": False,
                "reason": f"Zu kurz — erst ab {SUMMARY_MIN_MESSAGES} Nachrichten sinnvoll"}

    fold, keep = live[:-KEEP_VERBATIM], live[-KEEP_VERBATIM:]
    if not fold:
        return {"ok": False, "reason": "Nichts zu verdichten — der Verlauf ist bereits kurz"}

    summary = build_summary(fold)
    now = datetime.now(timezone.utc)
    for msg in fold:
        msg.meta = {**(msg.meta or {}), "compacted": True, "compacted_at": now.isoformat()}

    db.add(ChatMessage(
        agent_id=agent_id,
        session_id=session_id,
        message_id=f"cp-{uuid.uuid4().hex[:12]}",
        role="system",
        content=f"{COMPACT_MARKER}\n\n{summary}",
        meta={"compaction": True, "folded": len(fold)},
        # Genau VOR die behaltenen Nachrichten, nicht ans Ende: sonst stuende die
        # Zusammenfassung nach dem, was sie zusammenfasst. Eine Mikrosekunde davor
        # und nicht gleichauf — bei gleichem Zeitstempel entscheidet die Kennung,
        # und die neue ist immer die groessere.
        timestamp=(keep[0].timestamp or now) - timedelta(microseconds=1),
    ))
    await db.flush()
    logger.info("[Chat] %s verdichtet: %d Nachrichten gefaltet, %d bleiben",
                session_id, len(fold), len(keep))
    return {"ok": True, "folded": len(fold), "kept": len(keep)}


async def summarize_to_new_session(db: AsyncSession, agent_id: str,
                                   session_id: str) -> dict:
    """Ein langes Gespräch als kurzen Stand in ein frisches übernehmen.

    Der Verlauf bleibt, wo er ist — es wird nichts gelöscht und nichts verschoben.
    """
    messages = await messages_of(db, agent_id, session_id)
    if len(messages) < SUMMARY_MIN_MESSAGES:
        return {"ok": False,
                "reason": f"Zu kurz — erst ab {SUMMARY_MIN_MESSAGES} Nachrichten sinnvoll"}

    target = f"cont-{uuid.uuid4().hex[:10]}"
    source_title = await ensure_title(db, agent_id, session_id)
    summary = build_summary(messages)

    db.add(ChatMessage(
        agent_id=agent_id, session_id=target,
        message_id=f"sm-{uuid.uuid4().hex[:12]}",
        role="system",
        content=("Stand aus dem vorherigen Gespräch — hier wird daran angeknüpft:\n\n"
                 + summary),
        meta={"summarized_from": session_id},
        timestamp=datetime.now(timezone.utc),
    ))
    db.add(ChatSession(
        agent_id=agent_id, session_id=target,
        title=f"Fortsetzung: {source_title}"[:TITLE_MAX] if source_title else "Fortsetzung",
    ))
    await db.flush()
    logger.info("[Chat] %s als Stand nach %s uebernommen", scrub_log(session_id), scrub_log(target))
    return {"ok": True, "session_id": target, "summarized": len(messages)}
