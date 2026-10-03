"""Angekuendigt, aber nicht getan: „Der Agent arbeitet gerade nicht weiter.“ (#891)

Ein Agent beendet seinen Zug mit einer Ankuendigung — „… und rendere dann das
ganze Video“, „ich mache das im Hintergrund“, „ich melde mich“. Danach laeuft
weder ein Zug noch eine Aufgabe; niemand weckt ihn. Im Chat sieht es trotzdem
aus, als arbeite er weiter. Beobachtet am 2026-10-02: letzter Satz eine
Ankuendigung, im Behaelter kein Prozess, keine Aufgabe.

Geloest serverseitig und damit in JEDER Laufzeit gleich (Claude Code, Codex,
eigenes Modell): Nach jeder fertigen Antwort schaut der Server auf die
Schlusssaetze (``ist_ankuendigung``). Kuendigen sie Arbeit an, prueft er nach
kurzer Wartezeit, ob inzwischen doch etwas laeuft (``laeuft_etwas``). Wenn
nicht, steht im Gespraech eine Statuszeile mit Knopf „Weitermachen“.

Eine Regel in der Anleitung allein haelt erfahrungsgemaess nicht; sie steht
trotzdem dort (``DEFAULT_CLAUDE_MD``), damit es seltener vorkommt.

Ein Fehlalarm ist billig: Die Zeile behauptet nur, was nach der Pruefung
stimmt — es laeuft nichts. Ein verpasster Fall kostet dagegen einen Menschen,
der auf Arbeit wartet, die nie kommt.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone

from sqlalchemy import func, or_, select

from app.models.chat_message import ChatMessage
from app.models.chat_session import ChatSession
from app.models.task import Task, TaskStatus

logger = logging.getLogger(__name__)

#: Der Text der Statuszeile im Chat.
HINWEIS = "Der Agent arbeitet gerade nicht weiter."
#: ``meta.source`` der Statuszeile und Typ des Live-Ereignisses.
QUELLE = "stillstand"
#: So lange darf der Agent nach dem Zug noch etwas anstossen (Aufgabe, Folgezug).
WARTEZEIT_SEKUNDEN = 20
#: Dieselbe Fertigmeldung kommt bis zu zweimal (Live-Kanal + dauerhafte Liste).
_ANSPRUCH_SEKUNDEN = 3600


# ── Erkennung ────────────────────────────────────────────────────────────────

#: Zeitangaben, die eine Handlung in die Zukunft (oder in den Hintergrund) legen.
_ZEIT = (
    r"(?:jetzt|gleich|nun|dann|danach|anschlie(?:ss|ß)end|als n(?:ä|ae)chstes|"
    r"im n(?:ä|ae)chsten schritt|direkt|sofort|noch|kurz|mal|zun(?:ä|ae)chst|"
    r"erst(?:\s?mal|\s+einmal)?|zuerst|sp(?:ä|ae)ter|bald|im anschluss|"
    r"im hintergrund|parallel|in k(?:ü|ue)rze|nochmal|noch mal|morgen|heute)"
)

#: Verbformen auf -e, die KEINE kuenftige Handlung ansagen (Hilfsverben,
#: Meinung, Wunsch, Bedarf, Warten).
_KEINE_HANDLUNG = {
    "habe", "hatte", "hätte", "haette", "würde", "wuerde", "wurde", "werde",
    "könnte", "koennte", "konnte", "sollte", "müsste", "muesste", "musste",
    "wollte", "möchte", "moechte", "dürfte", "duerfte", "wäre", "waere",
    "glaube", "denke", "hoffe", "meine", "finde", "empfehle", "schlage",
    "freue", "danke", "verstehe", "vermute", "sehe", "warte", "brauche",
    "bräuchte", "braeuchte", "benötige", "benoetige", "fürchte", "fuerchte",
    "bitte", "weise", "kenne", "liebe", "stelle",
}
#: Kurzformen ohne -e, die trotzdem Handlung ansagen („ich mach jetzt …“).
_KURZFORMEN = {"mach", "leg", "fang", "schau", "guck", "hol", "setz", "bau", "schreib", "check"}


def _handlungsverb(wort: str) -> bool:
    wort = wort.lower()
    if wort in _KEINE_HANDLUNG:
        return False
    return wort in _KURZFORMEN or (len(wort) >= 4 and wort.endswith("e"))


#: „ich rendere … dann“, „ich mache das im Hintergrund“
_ICH_VERB_ZEIT = re.compile(rf"\bich\s+(\w+)\b[^.!?]{{0,80}}?\b{_ZEIT}\b")
#: „Jetzt baue ich …“, „Als Nächstes schreibe ich …“
_ZEIT_VERB_ICH = re.compile(rf"\b{_ZEIT}\s+(\w+)\s+ich\b")
#: „Ich passe … an und rendere dann …“ (nur mit „ich“ im selben Satz)
_UND_VERB_ZEIT = re.compile(rf"\bund\s+(\w+)\s+(?:\w+\s+){{0,2}}?{_ZEIT}\b")
#: „Ich werde …“ / „Gleich werde ich …“
_WERDE = re.compile(r"\b(?:ich\s+werde|werde\s+ich)\b")

_FESTE_WENDUNGEN = [re.compile(m) for m in (
    # deutsch
    r"\bmelde mich\b",
    r"\bgib mir (?:einen|eine|ein paar|kurz|etwas|noch)\b",
    r"\beinen (?:moment|augenblick)\b",
    r"\bbin gleich (?:zurück|zurueck|wieder da|fertig|so weit|soweit)\b",
    r"\bdu hörst (?:gleich|bald|dann|später|spaeter) von mir\b",
    r"\bbis gleich\b",
    r"\blos geht'?s\b",
    r"\b(?:mach(?:e)? ich|ich mach(?:e)? das|wird gemacht)[.!]?$",
    r"\bleg(?:e)? (?:ich )?(?:direkt |gleich |sofort |jetzt )?los\b",
    r"\bdas dauert (?:etwa|ca\.?|circa|ungefähr|ungefaehr|ein paar|einige|einen moment|kurz|nur kurz|noch)\b",
    # englisch
    r"\b(?:i'll|i will|i'm going to|i am going to|i'm gonna)\b(?! (?:wait|be here|be happy|be glad|need|leave|stop))",
    r"\blet me (?!know\b)\w+",
    r"\b(?:i'm|i am) on it\b",
    r"\bworking on it\b",
    r"\bin the background\b",
    r"\bstay tuned\b",
    r"\bbe right back\b",
    r"\bgive me (?:a|one|some|a few) (?:moment|minute|sec|second|few|minutes)\b",
    r"\bget back to you\b",
    r"\breport back\b",
    r"\bkeep you posted\b",
    r"\bwill (?:update|notify|ping) you\b",
    r"\b(?:i'm|i am) (?:now |currently )?(?:starting|rendering|building|running|generating|creating)\b",
)]

#: Angebote, Rueckfragen, Hoeflichkeit — sagen nichts ueber eigene Arbeit.
_NEUTRAL = [re.compile(m) for m in (
    r"\bsoll(?:en|te)? (?:ich|wir)\b",
    r"\b(?:möchtest|moechtest|willst|magst|brauchst|wünschst|wuenschst) du\b",
    r"\bwenn du (?:willst|möchtest|moechtest|magst|lust|bescheid|das)\b",
    r"\bfalls du\b",
    r"\bbei (?:bedarf|fragen)\b",
    r"\b(?:sag|gib) (?:mir )?(?:gern |gerne |einfach |kurz )?bescheid\b",
    r"\bmelde dich\b",
    r"\bviel spa(?:ß|ss)\b",
    r"\bich hoffe\b",
    r"\bich kann (?:dir )?(?:auch|gern|gerne)\b",
    r"\blet me know\b",
    r"\bif you(?:'d| would)? (?:like|want|prefer|need)\b",
    r"\b(?:shall|should) i\b",
    r"\b(?:do|would) you (?:want|like)\b",
    r"\bwant me to\b",
    r"\bhope (?:this|that|it) helps\b",
    r"\bfeel free\b",
    r"\bi can also\b",
)]

#: Der Schlusssatz meldet ein Ergebnis.
_ABGESCHLOSSEN = re.compile(
    r"\b(?:fertig|erledigt|abgeschlossen|geschafft|hier (?:ist|sind|findest)|"
    r"done|finished|completed|all set|here(?:'s| is| are))\b"
)

_CODEBLOCK = re.compile(r"```.*?(?:```|$)", re.DOTALL)
_SATZENDE = re.compile(r"(?<=[.!?…])\s+|\n+")


def _saetze(antwort: str) -> list[str]:
    """Die Saetze der letzten beiden Absaetze, ohne Markdown, kleingeschrieben."""
    text = _CODEBLOCK.sub(" ", antwort or "")
    text = text.replace("’", "'").replace("‘", "'")
    absaetze = [a for a in re.split(r"\n\s*\n", text) if a.strip()]
    saetze = []
    for absatz in absaetze[-2:]:
        for satz in _SATZENDE.split(absatz):
            satz = re.sub(r"^[\s>*_#`\-•]+|[\s*_`]+$", "", satz)
            satz = re.sub(r"[*_`]+", "", satz).strip().lower()
            if satz:
                saetze.append(satz)
    return saetze


def _ist_neutral(satz: str) -> bool:
    return any(m.search(satz) for m in _NEUTRAL)


def _kuendigt_an(satz: str) -> bool:
    if any(m.search(satz) for m in _FESTE_WENDUNGEN):
        return True
    for treffer in _ICH_VERB_ZEIT.finditer(satz):
        if _handlungsverb(treffer.group(1)):
            return True
    for treffer in _ZEIT_VERB_ICH.finditer(satz):
        if _handlungsverb(treffer.group(1)):
            return True
    if re.search(r"\bich\b", satz):
        for treffer in _UND_VERB_ZEIT.finditer(satz):
            if _handlungsverb(treffer.group(1)):
                return True
    if _WERDE.search(satz) and not re.search(r"\bnicht|\bnichts\b|\bkein", satz):
        return True
    return False


def ist_ankuendigung(antwort: str) -> bool:
    """Kuendigen die Schlusssaetze der Antwort Arbeit an, die noch nicht getan ist?

    * Endet die Antwort mit einer Frage, wartet der Agent auf den Menschen — nein.
    * Angebote und Hoeflichkeit („Sag Bescheid, falls …“, „Soll ich …“) zaehlen
      nicht mit, weder dafuer noch dagegen.
    * Von den uebrigen zaehlen die letzten beiden Saetze; meldet der letzte ein
      Ergebnis („Fertig — hier ist …“), war die Ankuendigung davor schon erfuellt.
    """
    saetze = _saetze(antwort)
    if not saetze or saetze[-1].endswith("?"):
        return False
    kandidaten = [s for s in saetze if not _ist_neutral(s)][-2:]
    if not kandidaten:
        return False
    if _kuendigt_an(kandidaten[-1]):
        return True
    return (
        len(kandidaten) == 2
        and _kuendigt_an(kandidaten[0])
        and not _ABGESCHLOSSEN.search(kandidaten[-1])
    )


# ── Entscheidung nach dem Zug ────────────────────────────────────────────────

def _text(wert) -> str:
    return wert.decode() if isinstance(wert, bytes) else str(wert or "")


async def laeuft_etwas(db, redis_client, agent_id: str, session_id: str, message_id: str) -> bool:
    """Arbeitet der Agent inzwischen doch — oder steht Arbeit an?

    Im Zweifel ja: Lieber eine Zeile zu wenig als die Behauptung „arbeitet nicht“,
    waehrend er arbeitet.
    """
    # 1. Der Agent selbst meldet laufende Arbeit (Zug in einem anderen Gespraech,
    #    Aufgabe, Nachricht eines Kollegen).
    status = {_text(k): _text(v) for k, v in ((await redis_client.hgetall(f"agent:{agent_id}:status")) or {}).items()}
    if status.get("state") == "working":
        return True
    try:
        if json.loads(status.get("active_sessions") or "[]"):
            return True
    except (TypeError, ValueError):
        pass

    # 2. Ein Folgezug wartet in der Schlange (Nutzer, Goal-Runde, Rueckmeldung).
    if await redis_client.llen(f"agent:{agent_id}:chat"):
        return True

    # 3. Im Gespraech ist seither etwas dazugekommen: neue Nachricht, Runde, Kachel.
    eigene = await db.scalar(select(ChatMessage.id).where(
        ChatMessage.agent_id == agent_id, ChatMessage.session_id == session_id,
        ChatMessage.message_id == message_id, ChatMessage.role == "assistant",
    ).order_by(ChatMessage.id.desc()).limit(1))
    if eigene is None:
        return True  # keine Antwortzeile, an die sich die Statuszeile haengen liesse
    neueste = await db.scalar(select(func.max(ChatMessage.id)).where(
        ChatMessage.agent_id == agent_id, ChatMessage.session_id == session_id,
    ))
    if neueste is not None and neueste > eigene:
        return True

    # 4. Aufgaben: laufend — oder in diesem Zug angelegt (auch fuer einen Kollegen,
    #    dessen Ergebnis spaeter einen Folgezug ausloest). Alte liegengebliebene
    #    Auftraege zaehlen nicht, sonst kaeme fuer diesen Agenten nie wieder ein Hinweis.
    beginn = await db.scalar(select(ChatMessage.timestamp).where(
        ChatMessage.agent_id == agent_id, ChatMessage.session_id == session_id,
        ChatMessage.message_id == message_id, ChatMessage.role == "user",
    ).limit(1))
    bedingung = Task.status == TaskStatus.RUNNING
    if beginn is not None:
        bedingung = or_(bedingung, Task.created_at >= beginn)
    offen = (await db.execute(select(Task.agent_id, Task.metadata_).where(
        Task.status.in_([TaskStatus.PENDING, TaskStatus.QUEUED, TaskStatus.RUNNING]),
        bedingung,
    ).limit(500))).all()
    for zustaendig, meta in offen:
        if zustaendig == agent_id or (meta or {}).get("created_by_agent") == agent_id:
            return True
    return False


async def _ziel_aktiv(db, agent_id: str, session_id: str) -> bool:
    from app.core.ziel import AKTIV

    zeile = await db.scalar(select(ChatSession).where(
        ChatSession.agent_id == agent_id, ChatSession.session_id == session_id,
    ))
    return bool(zeile and zeile.goal and (zeile.goal_status or AKTIV) == AKTIV)


async def pruefen_und_melden(db, redis_client, agent_id: str, session_id: str, message_id: str) -> bool:
    """Nach der Wartezeit: Statuszeile schreiben, wenn nichts weiterlaeuft."""
    # /goal treibt selbst weiter (app.core.ziel) — dort gibt es keinen Stillstand.
    if await _ziel_aktiv(db, agent_id, session_id):
        return False
    if await laeuft_etwas(db, redis_client, agent_id, session_id, message_id):
        return False

    from app.services.chat_persistence import upsert_chat_message

    zeilen_id = f"{QUELLE}-{message_id}"
    neu = await upsert_chat_message(
        agent_id, session_id, zeilen_id, "system",
        content=HINWEIS, meta={"source": QUELLE, "nach": message_id},
    )
    if not neu:
        return False
    # Ohne ``message_id`` auf oberster Ebene: der Weiterleiter im WS laesst nur
    # solche Steuerereignisse ohne Zuordnung durch; das Fenster filtert selbst
    # nach ``session_id``.
    await redis_client.publish(f"agent:{agent_id}:chat:response", json.dumps({
        "type": QUELLE,
        "agent_id": agent_id,
        "session_id": session_id,
        "data": {"id": zeilen_id, "content": HINWEIS, "nach": message_id},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }))
    return True


#: Laufende Pruefungen — eine Referenz, damit der Muellsammler sie nicht abraeumt.
_LAEUFE: set[asyncio.Task] = set()


def nach_zug(
    redis_client, agent_id: str, session_id: str, message_id: str, antwort: str,
    *, fehlgeschlagen: bool = False, warten: float = WARTEZEIT_SEKUNDEN,
    session_factory=None,
) -> asyncio.Task | None:
    """Nach einer fertigen Antwort: bei einer Ankuendigung die Pruefung einplanen.

    Kehrt sofort zurueck — der Lauscher auf ``chat:completions`` arbeitet die
    Fertigmeldungen nacheinander ab und darf nicht 20 Sekunden stehen. Liefert die
    eingeplante Pruefung (fuer Tests) oder ``None``.
    """
    if not (agent_id and session_id and message_id) or session_id == "scheduler":
        return None
    if fehlgeschlagen or not ist_ankuendigung(antwort):
        return None

    async def _lauf() -> bool:
        # Beanspruchen vor dem Warten: dieselbe Fertigmeldung kommt bis zu
        # zweimal an, geprueft wird genau einmal.
        if not await redis_client.set(
            f"chat:{QUELLE}:{agent_id}:{message_id}", "1", nx=True, ex=_ANSPRUCH_SEKUNDEN,
        ):
            return False
        if warten:
            await asyncio.sleep(warten)
        fabrik = session_factory
        if fabrik is None:
            from app.db.session import async_session_factory as fabrik
        try:
            async with fabrik() as db:
                return await pruefen_und_melden(db, redis_client, agent_id, session_id, message_id)
        except Exception:  # noqa: BLE001 — eine Anzeige darf nichts anderes stoeren
            logger.warning("[Stillstand] Pruefung nach %s fehlgeschlagen", message_id, exc_info=True)
            return False

    lauf = asyncio.create_task(_lauf())
    _LAEUFE.add(lauf)
    lauf.add_done_callback(_LAEUFE.discard)
    return lauf
