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

import json
import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core import ziel as _ziel
from app.core.dlp import mask as _dlp_mask
from app.core.dlp import werkzeug_eingabe_maskieren as _eingabe_maskieren
from app.db.session import async_session_factory
from app.models.chat_message import ChatMessage

logger = logging.getLogger(__name__)

#: Wie viel Werkzeug-Ausgabe je Aufruf im Verlauf bleibt (#911). Genug, um nach
#: dem Neuladen zu sehen, was herauskam — der volle Strom gehoert nicht in die
#: Datenbank. Dieselbe Groessenordnung wie im Agenten (werkzeug_eintrag.py).
AUSGABE_MAX = 1000


def ausgabe_text(inhalt) -> str:
    """Ein ``tool_result``-Inhalt als Text — Zeichenkette, Blockliste oder Objekt."""
    if inhalt is None:
        return ""
    if isinstance(inhalt, str):
        return inhalt
    if isinstance(inhalt, list):
        teile = []
        for block in inhalt:
            if isinstance(block, str):
                teile.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                teile.append(str(block.get("text") or ""))
            elif isinstance(block, dict) and "content" in block:
                teile.append(ausgabe_text(block.get("content")))
            else:
                teile.append(json.dumps(block, ensure_ascii=False, default=str))
        return "\n".join(t for t in teile if t)
    if isinstance(inhalt, dict) and "content" in inhalt:
        return ausgabe_text(inhalt.get("content"))
    return json.dumps(inhalt, ensure_ascii=False, default=str)


def _ausgabe_kuerzen(text: str) -> str:
    if len(text) <= AUSGABE_MAX:
        return text
    return text[:AUSGABE_MAX] + "\n… (gekürzt)"


def werkzeug_ergebnis_eintragen(werkzeuge: list, tool_use_id: str, inhalt) -> bool:
    """Die Ausgabe eines Werkzeugs an SEINEN Eintrag haengen (per ``tool_use_id``).

    Ohne Kennung wird nichts geraten — eine Ausgabe am falschen Aufruf ist
    schlimmer als keine. Rueckgabe: eingetragen ja/nein.
    """
    if not tool_use_id:
        return False
    for eintrag in werkzeuge or []:
        if isinstance(eintrag, dict) and eintrag.get("tool_use_id") == tool_use_id:
            text = ausgabe_text(inhalt)
            if text:
                eintrag["output"] = _ausgabe_kuerzen(text)
            return True
    return False


def _werkzeuge_bereinigen(werkzeuge):
    """Vor dem Speichern: Eingaben und Ausgaben ueber die Geheimnis-Maskierung
    (core/dlp, nur Klasse ``secret``), Ausgaben auf ``AUSGABE_MAX`` kuerzen. Die
    Eingabe bleibt dabei gueltiges JSON — die Oberflaeche parst sie."""
    if not isinstance(werkzeuge, list):
        return werkzeuge
    aus = []
    for eintrag in werkzeuge:
        if isinstance(eintrag, dict) and "input" in eintrag:
            eintrag = {**eintrag, "input": _eingabe_maskieren(eintrag["input"])}
        if isinstance(eintrag, dict) and eintrag.get("output"):
            eintrag = dict(eintrag)
            eintrag["output"] = _ausgabe_kuerzen(
                _dlp_mask(ausgabe_text(eintrag["output"]), {"secret"}))
        aus.append(eintrag)
    return aus


def _gewicht(werkzeuge: list) -> tuple[int, int, int]:
    """Wie viel eine Werkzeugliste weiss: Eintraege, Ausgaben, Zeichen."""
    eintraege = [e for e in werkzeuge if isinstance(e, dict)]
    zeichen = sum(
        len(e["input"] if isinstance(e.get("input"), str) else json.dumps(e.get("input") or {}))
        + len(str(e.get("output") or ""))
        for e in eintraege
    )
    return len(werkzeuge), sum(1 for e in eintraege if e.get("output")), zeichen


def _leer(wert) -> bool:
    return wert is None or wert == "" or wert == {} or wert == []


def werkzeuge_zusammenfuehren(alt, neu):
    """Zwei Fassungen derselben Werkzeugliste zu einer — nie aermer als die reichere.

    Zwei Schreiber, zwei Fassungen: Der Browser sammelt die volle Eingabe und die
    Ausgaben aus dem Strom, das ``done`` des Agenten traegt seine eigene Liste
    (aeltere Agenten: gekuerzte Eingabe ohne Ausgabe). Frueher gewann, wer
    zuletzt schrieb — und nach dem Neuladen stand ``IN {}`` (#911).

    Die reichere Liste ist die Grundlage; was nur die andere hat (Ausgabe,
    Subagent-Felder), wird je Eintrag ergaenzt — zugeordnet per ``tool_use_id``,
    sonst bei gleicher Laenge per Position und gleichem Werkzeug.
    """
    if not isinstance(neu, list) or not neu:
        return alt or (neu or None)
    if not isinstance(alt, list) or not alt:
        return neu
    basis, andere = (neu, alt) if _gewicht(neu) >= _gewicht(alt) else (alt, neu)
    nach_id = {
        e["tool_use_id"]: e for e in andere
        if isinstance(e, dict) and e.get("tool_use_id")
    }
    gleich_lang = len(basis) == len(andere)
    aus = []
    for index, eintrag in enumerate(basis):
        if not isinstance(eintrag, dict):
            aus.append(eintrag)
            continue
        gegen = nach_id.get(eintrag.get("tool_use_id")) if eintrag.get("tool_use_id") else None
        if gegen is None and gleich_lang:
            kandidat = andere[index]
            if isinstance(kandidat, dict) and kandidat.get("tool") == eintrag.get("tool"):
                gegen = kandidat
        if gegen is not None:
            eintrag = {
                **{k: v for k, v in gegen.items() if not _leer(v)},
                **{k: v for k, v in eintrag.items() if not _leer(v)},
            }
        aus.append(eintrag)
    return aus

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
    tool_calls = _werkzeuge_bereinigen(tool_calls)
    # /goal (#906): Die Schlusszeile ist ein Signal an den Server, kein Text fuer
    # den Menschen. Bewertet hat ``ziel.nach_zug`` den Rohtext; hier wird ohne
    # Marke gespeichert, der Zustand steht in ``meta.ziel``.
    if role == "assistant" and content:
        content, zustand = _ziel.ohne_marke(content)
        if zustand:
            meta = {**(meta or {}), "ziel": zustand}
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
                    existing.tool_calls = werkzeuge_zusammenfuehren(
                        existing.tool_calls, tool_calls)
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
