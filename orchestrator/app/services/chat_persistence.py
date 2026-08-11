"""Eine Chat-Zeile, zwei Schreiber — hier steht, wie sie zusammenfinden.

Chat-Antworten werden an ZWEI Stellen gespeichert, und das ist Absicht:

* die WebSocket-Verbindung des Browsers, solange jemand zusieht — sie hat den
  Strom ohnehin in der Hand und kennt Bilder, Dateien und Zwischenstände;
* der serverseitige Lauscher auf ``chat:completions`` (``main.py``), der auch
  dann schreibt, wenn niemand hinschaut.

Der zweite ist der wichtigere, denn wer parallel arbeitet, schaut per Definition
woanders hin. Beim Trennen der Verbindung hat der Browser 120 Sekunden Nachlauf;
ein Zug, der laut Messung auch mal 176, 502 oder 514 Sekunden dauert, überlebt
den nicht. Was dann weggeschrieben wurde, war ein **Zwischenstand**: die früh
gekommenen Werkzeugaufrufe ja, der am Ende gekommene Antworttext nein.

Und genau hier lag der Fehler, den dieses Modul behebt. Der Lauscher prüfte
„gibt es die Zeile schon?" und übersprang sie dann — der Zwischenstand blieb für
immer stehen, ohne Text. Übrig blieb das Bild aus der Meldung: **nur
Werkzeugaufrufe, keine Antwort.**

Deshalb gibt es hier EINE Zusammenführung für beide Schreiber. Wer zuerst kommt,
legt die Zeile an; der andere ergänzt, was fehlt. Ein leerer Text überschreibt
nie einen vorhandenen — ``content or existing.content`` ist genau dafür da.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.session import async_session_factory
from app.models.chat_message import ChatMessage

logger = logging.getLogger(__name__)

async def upsert_chat_message(
    agent_id: str,
    session_id: str,
    message_id: str,
    role: str,
    *,
    content: str = "",
    tool_calls: list | None = None,
    meta: dict | None = None,
    cost_usd: float | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> bool:
    """Eine Chat-Zeile anlegen oder ergänzen.

    Der Schlüssel ist ``(agent, session, message_id, role)``. Zwei Schreiber sind
    Absicht — die Browser-Verbindung und der serverseitige Lauscher —, deshalb
    ergänzt der zweite, statt zu überschreiben: ``content or existing.content``
    heisst, ein leerer Zwischenstand kann eine fertige Antwort nicht auslöschen.

    Rückgabe: **hat der Nutzer diese Antwort noch nicht gesehen?** Das ist der
    Fall, wenn die Zeile neu angelegt wurde ODER wenn sie bis eben ohne Text
    dastand und diesen Aufruf den Text bekommt. Der zweite Fall ist gerade der
    Wiedereinstieg nach einem Verbindungsabbruch: da hing der Zwischenstand ohne
    Antwort in der Ablage, und der Nutzer war weg. Ihn deshalb NICHT zu
    benachrichtigen wäre die falsche Sparsamkeit.

    Zwei Schreiber heisst auch: beide koennen das SELECT oben gleichzeitig mit
    "gibt es noch nicht" beantworten und beide ein INSERT versuchen. Der zweite
    Commit verletzt dann den Unique-Index — ohne Retry waere GENAU das der
    Rueckfall in den Fehler, den dieses Modul beheben soll: der Inhalt dieses
    Aufrufs (content/tool_calls/meta) ginge kommentarlos verloren. Deshalb bei
    diesem einen Konflikt einmal neu lesen: die Zeile existiert jetzt, also wird
    aus dem INSERT ein normales Merge-UPDATE.
    """
    if not (agent_id and session_id and message_id):
        return False
    try:
        for attempt in range(2):
            created = False
            filled = False
            async with async_session_factory() as db:
                existing = await db.scalar(
                    select(ChatMessage)
                    .where(ChatMessage.agent_id == agent_id)
                    .where(ChatMessage.session_id == session_id)
                    .where(ChatMessage.message_id == message_id)
                    .where(ChatMessage.role == role)
                    .order_by(ChatMessage.id.asc())
                    .limit(1)
                )
                if existing:
                    filled = not (existing.content or "").strip() and bool((content or "").strip())
                    existing.content = content or existing.content
                    existing.tool_calls = tool_calls or existing.tool_calls
                    merged_meta = dict(existing.meta or {})
                    for key, value in (meta or {}).items():
                        if value is None:
                            continue
                        if key == "presented_files":
                            merged_meta[key] = _merge_files(merged_meta.get(key), value)
                        else:
                            merged_meta[key] = value
                    existing.meta = merged_meta or None
                    existing.cost_usd = cost_usd if cost_usd is not None else existing.cost_usd
                    existing.input_tokens = (
                        input_tokens if input_tokens is not None else existing.input_tokens
                    )
                    existing.output_tokens = (
                        output_tokens if output_tokens is not None else existing.output_tokens
                    )
                else:
                    created = True
                    db.add(ChatMessage(
                        agent_id=agent_id,
                        session_id=session_id,
                        message_id=message_id,
                        role=role,
                        content=content,
                        tool_calls=tool_calls,
                        meta=meta,
                        cost_usd=cost_usd,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                    ))
                try:
                    await db.commit()
                except IntegrityError:
                    await db.rollback()
                    if attempt == 0:
                        continue
                    raise

                # Titel aus dem ersten Austausch (#538) — nur bei der ERSTEN
                # Nutzernachricht, und ein selbst vergebener Titel bleibt stehen.
                if role == "user":
                    try:
                        from app.core.chat_history import ensure_title
                        await ensure_title(db, agent_id, session_id)
                        await db.commit()
                    except Exception:  # noqa: BLE001 — ein Titel stoert keinen Chat
                        logger.debug("[Chat] Titel nicht ableitbar", exc_info=True)
            return created or filled
    except Exception:  # noqa: BLE001
        logger.warning("[Chat] Zeile nicht speicherbar (%s/%s)", agent_id, message_id,
                       exc_info=True)
        return False
    return False


def _merge_files(existing, incoming) -> list:
    """Dateilisten zusammenführen, ohne denselben Pfad doppelt aufzunehmen."""
    out = list(existing) if isinstance(existing, list) else []
    seen = {str(i.get("path", "")) for i in out if isinstance(i, dict)}
    for item in incoming if isinstance(incoming, list) else []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path", ""))
        if path and path not in seen:
            seen.add(path)
            out.append(item)
    return out


async def session_for_message(agent_id: str, message_id: str) -> str | None:
    """Zu welcher Unterhaltung gehört diese Nachricht?

    Die Frage beantwortet die Nutzer-Zeile: sie wird beim Absenden geschrieben,
    lange bevor die Antwort kommt, und trägt die Sitzung. Gibt es sie nicht,
    gehört die Nachricht nicht zu einem Web-Chat (Sprache, Telegram, Hintergrund-
    aufgabe) — dann ist hier nichts zu tun.
    """
    try:
        async with async_session_factory() as db:
            return await db.scalar(
                select(ChatMessage.session_id)
                .where(ChatMessage.agent_id == agent_id)
                .where(ChatMessage.message_id == message_id)
                .where(ChatMessage.role == "user")
                .order_by(ChatMessage.id.asc())
                .limit(1)
            )
    except Exception:  # noqa: BLE001
        logger.debug("[Chat] Sitzung zu %s nicht auflösbar", message_id, exc_info=True)
        return None
