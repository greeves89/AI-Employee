"""Das Ziel eines Gespraechs: ``/goal <Ziel>`` — der Agent arbeitet weiter, bis es erreicht ist.

Wie ``/goal`` in Claude Code, aber serverseitig und damit in JEDER Laufzeit gleich
(Claude Code, Codex, eigenes Modell): Nach jeder fertigen Antwort in einem
Gespraech mit aktivem Ziel schaut der Server auf die Schlusszeile der Antwort.

* ``ZIEL ERREICHT``           → Ziel abgeschlossen, keine weitere Runde.
* ``ZIEL PAUSIERT: <Frage>``  → wartet auf den Menschen; seine naechste Nachricht
                                 setzt es fort (die Runde danach laeuft wieder).
* sonst                       → naechste Runde wird automatisch eingereiht.

Die Grenze ``ZIEL_MAX_RUNDEN`` verhindert Endlosschleifen; ein Fehler oder eine
Zeitueberschreitung pausiert statt im Kreis zu laufen. Sperrt das Budget die
naechste Runde (#898), pausiert das Ziel ebenfalls — mit dem Grund
``GRUND_BUDGET`` und einem Hinweis im Gespraech. Nach einer Budgeterhoehung setzt
die naechste Nachricht des Menschen es fort wie jedes pausierte Ziel.

Warum der Agent selbst urteilt und kein zweites Modell: Er hat den Kontext (Dateien,
Testergebnisse) und es braucht keinen zusaetzlichen Modellzugang je Anlage. Die
Schlusszeile ist laufzeitunabhaengig — kein neues Werkzeug, das in drei
Harnessen gepflegt werden muesste.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat_session import ChatSession

logger = logging.getLogger(__name__)

ZIEL_MAX_RUNDEN = 20

ERREICHT = "ZIEL ERREICHT"
PAUSIERT = "ZIEL PAUSIERT"

#: Zustaende. ``aktiv`` ist der einzige, in dem weitergearbeitet wird.
AKTIV, ERLEDIGT, PAUSE, GESTOPPT, GRENZE = "aktiv", "erreicht", "pausiert", "gestoppt", "grenze"

#: Grund einer Pause, die nicht der Agent ausgeloest hat (``goal_reason``). Steht so
#: in der Oberflaeche.
GRUND_BUDGET = "Budget aufgebraucht"

_BEFEHL = re.compile(r"^/(?:goal|ziel)(?:[ \t]+(.*))?$", re.IGNORECASE | re.DOTALL)
_STOPP_WOERTER = {"stop", "stopp", "aus", "ende", "beenden", "clear", "off", "abbrechen"}


def befehl(text: str) -> tuple[str, str] | None:
    """``/goal …`` erkennen. Liefert (aktion, ziel) — aktion: setzen | stopp | status."""
    treffer = _BEFEHL.match((text or "").strip())
    if not treffer:
        return None
    rest = (treffer.group(1) or "").strip()
    if not rest:
        return ("status", "")
    if rest.lower() in _STOPP_WOERTER:
        return ("stopp", "")
    return ("setzen", rest)


_REGELN = (
    "Beende deine Antwort mit GENAU EINER dieser Zeilen, sobald sie zutrifft:\n"
    f"{ERREICHT} — das Ziel ist vollständig erfüllt und von dir geprüft.\n"
    f"{PAUSIERT}: <deine Frage> — du kommst ohne Entscheidung des Nutzers nicht weiter.\n"
    "Trifft keine zu, schreib keine davon — dann geht es automatisch mit der nächsten Runde weiter."
)


def auftrag(ziel: str, runde: int) -> str:
    """Der Text, den der Agent fuer eine Runde bekommt."""
    if runde <= 1:
        kopf = (
            f"[ZIEL — /goal] Dein Ziel in diesem Gespräch:\n{ziel}\n\n"
            "Arbeite selbstständig darauf hin. Nach jeder Antwort bekommst du automatisch "
            f"die nächste Runde, bis das Ziel erreicht ist (höchstens {ZIEL_MAX_RUNDEN} Runden). "
            "Mach in jeder Runde einen echten, überprüfbaren Schritt und schreib kurz, was "
            "erledigt ist und was als Nächstes kommt."
        )
    else:
        kopf = (
            f"[ZIEL — Runde {runde} von {ZIEL_MAX_RUNDEN}] Das Ziel ist noch nicht erreicht:\n{ziel}\n\n"
            "Mach den nächsten Schritt. Prüfe vorher kurz, was schon erledigt ist, statt es zu wiederholen."
        )
    return f"{kopf}\n\n{_REGELN}"


def bewerte(antwort: str) -> str:
    """Schlusszeilen der Antwort → ``erreicht`` | ``pausiert`` | ``weiter``.

    Nur die letzten Zeilen zaehlen: Erwaehnt der Agent die Marke mitten im Text
    („ich melde ZIEL ERREICHT, sobald …“), ist das keine Meldung.
    """
    treffer = _schlusszeile(antwort)
    return treffer[1] if treffer else "weiter"


def _bereinigt(zeile: str) -> str:
    return zeile.strip().strip("*_`> ").strip()


def _schlusszeile(antwort: str) -> tuple[int, str] | None:
    """(Index der Zeile in ``splitlines()``, ``erreicht``|``pausiert``) oder None.

    EINE Erkennung fuer ``bewerte`` (Urteil) und ``ohne_marke`` (Anzeige), damit
    nie eine Zeile entfernt wird, die nicht als Meldung zaehlte — und umgekehrt.
    """
    zeilen = (antwort or "").splitlines()
    belegt = [i for i, z in enumerate(zeilen) if z.strip()]
    for i in belegt[-3:]:
        oben = _bereinigt(zeilen[i]).upper()
        if oben.startswith(ERREICHT):
            return i, "erreicht"
        if oben.startswith(PAUSIERT):
            return i, "pausiert"
    return None


def ohne_marke(antwort: str) -> tuple[str, str | None]:
    """Die Schlusszeile aus dem Text nehmen — (Text, ``erreicht``|``pausiert``|None).

    Die Marke ist ein Signal an den Server, kein Satz fuer den Menschen (#906).
    Bewertet wird weiter der Rohtext (``bewerte``); gespeichert und angezeigt wird
    der Text ohne Marke, der Zustand steht in ``meta.ziel``. Bei ``pausiert``
    bleibt die Frage stehen — nur die Marke davor faellt weg.
    """
    treffer = _schlusszeile(antwort)
    if treffer is None:
        return antwort or "", None
    index, zustand = treffer
    zeilen = (antwort or "").splitlines()
    rest = ""
    if zustand == "pausiert":
        rest = _bereinigt(zeilen[index])[len(PAUSIERT):].lstrip(" :-–—").strip().strip("*_`").strip()
    if rest:
        zeilen[index] = rest
    else:
        del zeilen[index]
    return "\n".join(zeilen).rstrip(), zustand


async def _zeile(db: AsyncSession, agent_id: str, session_id: str) -> ChatSession:
    zeile = await db.scalar(select(ChatSession).where(
        ChatSession.agent_id == agent_id, ChatSession.session_id == session_id,
    ))
    if zeile is None:
        zeile = ChatSession(agent_id=agent_id, session_id=session_id)
        db.add(zeile)
    return zeile


async def setzen(db: AsyncSession, agent_id: str, session_id: str, ziel: str) -> None:
    zeile = await _zeile(db, agent_id, session_id)
    zeile.goal = ziel[:4000]
    zeile.goal_status = AKTIV
    zeile.goal_rounds = 0
    zeile.goal_last_mid = None
    zeile.goal_reason = None
    await db.commit()


async def beenden(db: AsyncSession, agent_id: str, session_id: str, status: str = GESTOPPT) -> bool:
    zeile = await db.scalar(select(ChatSession).where(
        ChatSession.agent_id == agent_id, ChatSession.session_id == session_id,
    ))
    if zeile is None or not zeile.goal:
        return False
    zeile.goal_status = status
    await db.commit()
    return True


def als_dict(zeile: ChatSession | None) -> dict | None:
    if zeile is None or not zeile.goal:
        return None
    return {"text": zeile.goal, "status": zeile.goal_status or AKTIV,
            "rounds": zeile.goal_rounds or 0, "max_rounds": ZIEL_MAX_RUNDEN,
            "reason": zeile.goal_reason}


async def nach_zug(
    db: AsyncSession, redis_client, agent_id: str, session_id: str,
    message_id: str, antwort: str, fehlgeschlagen: bool = False,
) -> str | None:
    """Nach einer fertigen Antwort: Ziel fortschreiben. Liefert den neuen Zustand oder None.

    Idempotent je Antwort: Dieselbe Fertigmeldung kommt bis zu zweimal an (Live-Kanal
    und dauerhafte Liste). Der bedingte UPDATE beansprucht sie genau einmal.
    """
    # Eine PAUSE wird durch die naechste Nachricht des Nutzers beendet: deren
    # Antwort setzt das Ziel fort — auch eine Budget-Pause; ihr Grund faellt weg.
    beansprucht = (await db.execute(sa_text(
        "UPDATE chat_sessions SET goal_last_mid = :mid, goal_rounds = COALESCE(goal_rounds, 0) + 1, "
        "goal_status = :aktiv, goal_reason = NULL, updated_at = CURRENT_TIMESTAMP "
        "WHERE agent_id = :a AND session_id = :s AND goal IS NOT NULL "
        "AND goal_status IN (:aktiv, :pause) "
        "AND (goal_last_mid IS NULL OR goal_last_mid <> :mid) "
        "RETURNING goal, goal_rounds"
    ), {"mid": message_id, "a": agent_id, "s": session_id, "aktiv": AKTIV, "pause": PAUSE})).first()
    if beansprucht is None:
        await db.rollback()
        return None
    ziel, runden = beansprucht[0], int(beansprucht[1])

    urteil = "pausiert" if fehlgeschlagen else bewerte(antwort)
    if urteil == "erreicht":
        neu = ERLEDIGT
    elif urteil == "pausiert":
        neu = PAUSE
    elif runden >= ZIEL_MAX_RUNDEN:
        neu = GRENZE
    else:
        neu = AKTIV
    await db.execute(sa_text(
        "UPDATE chat_sessions SET goal_status = :st WHERE agent_id = :a AND session_id = :s"
    ), {"st": neu, "a": agent_id, "s": session_id})
    await db.commit()

    if neu == AKTIV:
        if await naechste_runde(redis_client, agent_id, session_id, ziel, runden + 1) is None:
            neu = PAUSE  # Budget gesperrt — ``budget_gesperrt`` hat pausiert
    return neu


async def naechste_runde(
    redis_client, agent_id: str, session_id: str, ziel: str, runde: int,
) -> str | None:
    """Eine Runde einreihen — wie eine Chat-Nachricht, nur ohne Tippen.

    Im Verlauf steht eine kurze Zeile (``meta.source = "goal"``), der Agent bekommt
    den ausfuehrlichen Auftrag. Die Zeile ist zugleich die „Nutzernachricht“, an der
    die Antwort beim Speichern ihr Gespraech findet.

    Liefert die Kennung der Runde — oder ``None``, wenn das Budget sie gesperrt
    hat; das Ziel ist dann pausiert (``budget_gesperrt``).
    """
    from app.services.chat_persistence import upsert_chat_message

    message_id = uuid.uuid4().hex[:12]
    await upsert_chat_message(
        agent_id, session_id, message_id, "user",
        content=f"Weiter am Ziel — Runde {runde} von {ZIEL_MAX_RUNDEN}",
        meta={"source": "goal", "runde": runde},
    )
    # Jede Runde kostet wie eine Chat-Nachricht — also auch hier das Budget (#898).
    # Gesperrt: Bis v1.362 blieb das Ziel dann stumm auf „aktiv“ stehen — ohne
    # Antwort kam nie eine weitere Runde, und niemand sah, warum.
    from app.core.chat_auftrag import einreihen

    einreihung = await einreihen(redis_client, agent_id, {
        "id": message_id,
        "text": auftrag(ziel, runde),
        "model": None,
        "images": [],
        "source": "webapp",
        "chat_session_id": session_id,
        "reasoning": "",
    })
    if not einreihung.eingereiht:
        await budget_gesperrt(redis_client, agent_id, session_id, message_id, einreihung.hinweis)
        return None
    return message_id


async def budget_gesperrt(
    redis_client, agent_id: str, session_id: str, message_id: str, hinweis: str,
) -> bool:
    """Eine Runde des Ziels scheiterte am Budget: Ziel pausieren, Hinweis ins Gespraech.

    Fuer jede Runde — die automatische (``naechste_runde``) wie die erste nach
    ``/goal`` im Chat (ws.py). Pausiert wird nur ein laufendes Ziel; ein beendetes
    lebt nicht wieder auf. Der Hinweis steht als ``error``-Zeile zu genau dieser
    Runde im Verlauf (uebersteht ein Neuladen), ein offenes Fenster laedt den
    Ziel-Stand ueber ein ``goal``-Ereignis neu. Fortgesetzt wird wie bei jeder
    Pause: Die naechste beantwortete Nachricht des Menschen (``nach_zug``).

    Liefert, ob pausiert wurde.
    """
    from app.db.session import async_session_factory

    async with async_session_factory() as db:
        zeile = await db.scalar(select(ChatSession).where(
            ChatSession.agent_id == agent_id, ChatSession.session_id == session_id,
        ))
        if zeile is None or not zeile.goal or (zeile.goal_status or AKTIV) != AKTIV:
            return False
        zeile.goal_status = PAUSE
        zeile.goal_reason = GRUND_BUDGET
        await db.commit()
        stand = als_dict(zeile)

    try:
        from app.services.chat_persistence import upsert_chat_message

        await upsert_chat_message(
            agent_id, session_id, message_id, "error",
            content=hinweis or f"Ziel pausiert: {GRUND_BUDGET}.",
            meta={"source": "goal", "grund": "budget"},
        )
    except Exception:  # noqa: BLE001 — die Pause gilt auch ohne Zeile im Verlauf
        logger.warning("Budget-Hinweis zum Ziel nicht gespeichert", exc_info=True)
    try:
        # Ohne ``message_id`` auf oberster Ebene: der Weiterleiter im WS laesst
        # solche Steuerereignisse an jedes Fenster dieses Agenten durch.
        await redis_client.publish(f"agent:{agent_id}:chat:response", json.dumps({
            "type": "goal",
            "agent_id": agent_id,
            "session_id": session_id,
            "data": {"goal": stand, "session_id": session_id},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }))
    except Exception:  # noqa: BLE001 — Anzeige, keine Voraussetzung
        logger.debug("Ziel-Ereignis nicht veroeffentlicht", exc_info=True)
    return True
