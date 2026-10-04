"""Das Ziel eines Gespraechs: ``/goal <Ziel>`` — der Agent arbeitet weiter, bis es erreicht ist.

Wie ``/goal`` in Claude Code, aber serverseitig und damit in JEDER Laufzeit gleich
(Claude Code, Codex, eigenes Modell): Nach jeder fertigen Antwort in einem
Gespraech mit aktivem Ziel schaut der Server auf die Schlusszeile der Antwort.

* ``ZIEL ERREICHT``           → Ziel abgeschlossen, keine weitere Runde.
* ``ZIEL PAUSIERT: <Frage>``  → wartet auf den Menschen; seine naechste Nachricht
                                 setzt es fort (die Runde danach laeuft wieder).
* sonst                       → naechste Runde wird automatisch eingereiht.

Die Grenze ``ZIEL_MAX_RUNDEN`` verhindert Endlosschleifen; ein Fehler oder eine
Zeitueberschreitung pausiert statt im Kreis zu laufen.

Warum der Agent selbst urteilt und kein zweites Modell: Er hat den Kontext (Dateien,
Testergebnisse) und es braucht keinen zusaetzlichen Modellzugang je Anlage. Die
Schlusszeile ist laufzeitunabhaengig — kein neues Werkzeug, das in drei
Harnessen gepflegt werden muesste.
"""

from __future__ import annotations

import re
import uuid

from sqlalchemy import select, text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat_session import ChatSession

ZIEL_MAX_RUNDEN = 20

ERREICHT = "ZIEL ERREICHT"
PAUSIERT = "ZIEL PAUSIERT"

#: Zustaende. ``aktiv`` ist der einzige, in dem weitergearbeitet wird.
AKTIV, ERLEDIGT, PAUSE, GESTOPPT, GRENZE = "aktiv", "erreicht", "pausiert", "gestoppt", "grenze"

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
            "rounds": zeile.goal_rounds or 0, "max_rounds": ZIEL_MAX_RUNDEN}


async def nach_zug(
    db: AsyncSession, redis_client, agent_id: str, session_id: str,
    message_id: str, antwort: str, fehlgeschlagen: bool = False,
) -> str | None:
    """Nach einer fertigen Antwort: Ziel fortschreiben. Liefert den neuen Zustand oder None.

    Idempotent je Antwort: Dieselbe Fertigmeldung kommt bis zu zweimal an (Live-Kanal
    und dauerhafte Liste). Der bedingte UPDATE beansprucht sie genau einmal.
    """
    # Eine PAUSE wird durch die naechste Nachricht des Nutzers beendet: deren
    # Antwort setzt das Ziel fort.
    beansprucht = (await db.execute(sa_text(
        "UPDATE chat_sessions SET goal_last_mid = :mid, goal_rounds = COALESCE(goal_rounds, 0) + 1, "
        "goal_status = :aktiv, updated_at = CURRENT_TIMESTAMP "
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
        await naechste_runde(redis_client, agent_id, session_id, ziel, runden + 1)
    return neu


async def naechste_runde(redis_client, agent_id: str, session_id: str, ziel: str, runde: int) -> str:
    """Eine Runde einreihen — wie eine Chat-Nachricht, nur ohne Tippen.

    Im Verlauf steht eine kurze Zeile (``meta.source = "goal"``), der Agent bekommt
    den ausfuehrlichen Auftrag. Die Zeile ist zugleich die „Nutzernachricht“, an der
    die Antwort beim Speichern ihr Gespraech findet.
    """
    from app.services.chat_persistence import upsert_chat_message

    message_id = uuid.uuid4().hex[:12]
    await upsert_chat_message(
        agent_id, session_id, message_id, "user",
        content=f"Weiter am Ziel — Runde {runde} von {ZIEL_MAX_RUNDEN}",
        meta={"source": "goal", "runde": runde},
    )
    # Jede Runde kostet wie eine Chat-Nachricht — also auch hier das Budget (#898).
    # Gesperrt: der Hinweis steht als Fehler im Gespraech, eine weitere Runde
    # wird dann nicht mehr angestossen (keine Antwort, kein Weiter).
    from app.core.chat_auftrag import einreihen

    await einreihen(redis_client, agent_id, {
        "id": message_id,
        "text": auftrag(ziel, runde),
        "model": None,
        "images": [],
        "source": "webapp",
        "chat_session_id": session_id,
        "reasoning": "",
    })
    return message_id
