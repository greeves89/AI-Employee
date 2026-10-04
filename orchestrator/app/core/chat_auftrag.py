"""Was der Agent bekommt und was im Verlauf steht — zwei verschiedene Texte (#916).

Bis v1.361 baute der Browser die Anweisung an das Modell („[Angehängte Datei(en)
im Workspace: … WICHTIG: Öffne und lies …]“, „[NUR PLANEN — NICHT AUSFÜHREN] …“)
direkt in den Nachrichtentext. Gespeichert wurde, was der Agent bekam: Die
Anweisung stand nach dem Neuladen sichtbar in der eigenen Nachricht und im
Gesprächstitel.

Dasselbe Muster wie bei ``/goal`` (``anzeige_text`` gegen Auftrag): Der Browser
schickt ``{text, anhaenge, plan}``, der Server baut hier den Auftrag — für alle
Laufzeiten gleich —, und gespeichert werden der Nutzertext und ``meta.anhaenge``.
Die Oberfläche zeichnet daraus Dateikarten.

Ein alter Browser-Tab schickt weiter den zusammengebauten Text. ``aus_nachricht``
zerlegt ihn wieder, damit auch dann nur der Nutzertext gespeichert wird; mit
``altzeilen_bereinigen`` werden bestehende Zeilen einmalig umgeschrieben.

Und hier liegt der EINE Weg in die Warteschlange ``agent:{id}:chat``:
``einreihen`` prüft vorher das Budget (#898) — für Web-Chat, Kiosk, Telegram,
die Kanäle, die Sprachfront, ``/goal`` und Rückmeldungen gleichermaßen.
"""

from __future__ import annotations

import json
import logging
import posixpath
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

#: Wurzel, unter der Anhänge liegen (Upload-Ziel der Oberfläche).
WURZEL = "/workspace"
#: Mehr Anhänge an einer Nachricht nimmt der Server nicht an.
ANHAENGE_MAX = 20
_PFAD_MAX = 512

_ANHANG_KOPF = "[Angehängte Datei(en) im Workspace: "
_ANHANG_ANWEISUNG = (
    "WICHTIG: Öffne und lies die Datei(en) ZUERST selbst mit deinem Read-Tool (PDFs und "
    "Bilder werden unterstützt; große Textdateien ggf. mit bash/grep) und antworte dann auf "
    "Basis des TATSÄCHLICHEN Inhalts — rate NICHT aus dem Dateinamen."
)
_PLAN_KOPF = (
    "[NUR PLANEN — NICHT AUSFÜHREN] Beschreibe kurz und konkret, welche Schritte du für die "
    "folgende Aufgabe gehen würdest (Tools, betroffene Dateien/Befehle, externe Aktionen, grober "
    "Aufwand/Risiken). Führe nichts aus, ändere nichts, sende nichts — gib NUR den Plan zurück."
)

# Erkennung der Altform. Der Plan-Rahmen umschliesst den Anhang (so baute ihn
# chat.tsx: erst Anhang anhängen, dann den Plan darum legen).
_ALT_PLAN = re.compile(r"^\[NUR PLANEN[^\]]*\][^\n]*\n\nAufgabe: (?P<rest>.*)$", re.DOTALL)
_ALT_ANHANG = re.compile(
    r"^(?P<text>.*?)\s*\[Angehängte Datei\(en\) im Workspace: (?P<pfade>.*?)\. WICHTIG: [^\]]*\]\s*$",
    re.DOTALL,
)
_STEUERZEICHEN = re.compile(r"[\x00-\x1f\x7f]")


# ── Einreihen: der EINE Weg in ``agent:{id}:chat`` (#898) ────────────────────


@dataclass(frozen=True)
class Einreihung:
    """Ergebnis von ``einreihen``. ``hinweis`` ist der Text an den Menschen, wenn
    die Nachricht NICHT eingereiht wurde."""

    eingereiht: bool
    hinweis: str = ""
    modell: str | None = None


async def _budget_entscheid(agent_id: str):
    """Budgetentscheid für eine Chat-Nachricht; meldet eine Sperre gleich mit.

    Fail-open: Kann die Prüfung nicht laufen (Datenbank weg), wird zugestellt — ein
    kaputter Zähler soll nicht jede Unterhaltung der Anlage anhalten.
    """
    from app.core import budget
    from app.db.session import async_session_factory
    from app.models.agent import Agent

    try:
        async with async_session_factory() as db:
            agent = await db.get(Agent, agent_id)
            entscheid = await budget.budget_pruefen(db, agent, fuer_chat=True)
            if entscheid.art == budget.BLOCKIEREN:
                try:
                    await budget.sperre_melden(db, agent, entscheid)
                except Exception:  # noqa: BLE001 — die Sperre gilt auch ohne Meldung
                    logger.warning("Budget-Meldung fuer %s fehlgeschlagen", agent_id, exc_info=True)
            return entscheid
    except Exception:  # noqa: BLE001
        logger.warning("Budgetpruefung fuer %s nicht moeglich — Nachricht geht durch",
                       agent_id, exc_info=True)
        return budget.ERLAUBT


async def einreihen(redis_client, agent_id: str, payload: dict | str) -> Einreihung:
    """Eine Nachricht in die Chat-Warteschlange des Agenten legen — nach Budgetprüfung.

    Bis v1.362 schrieben Web-Chat, Kiosk, Telegram, die Kanäle, die Sprachfront,
    ``/goal`` und die Rückmeldungen delegierter Aufträge je für sich in
    ``agent:{id}:chat`` — und am Budget vorbei, das nur der Aufgabenweg prüfte.
    Jetzt geht jede dieser Stellen hier durch (ein Test wacht darüber):

    * erlaubt → einreihen wie bisher;
    * Sparmodus → mit dem günstigeren Modell der Laufzeit einreihen;
    * gesperrt → NICHT einreihen. Ein offener Chat (Browser, Telegram, Sprachfront)
      bekommt ein ``error``-Ereignis mit dem deutschen Hinweis zu genau dieser
      Nachricht; Wege, die selbst antworten (Kanäle, Kiosk), nehmen ``hinweis``.
    """
    if isinstance(payload, str):
        payload = json.loads(payload)
    payload = dict(payload)

    from app.core import budget

    entscheid = await _budget_entscheid(agent_id)
    if entscheid.art == budget.BLOCKIEREN:
        try:
            await redis_client.publish(f"agent:{agent_id}:chat:response", json.dumps({
                "agent_id": agent_id,
                "message_id": str(payload.get("id") or ""),
                "type": "error",
                "data": {"message": entscheid.hinweis, "grund": "budget"},
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }))
        except Exception:  # noqa: BLE001 — der Hinweis ist Beiwerk, die Sperre nicht
            logger.debug("Budget-Hinweis nicht veroeffentlicht", exc_info=True)
        return Einreihung(False, hinweis=entscheid.hinweis)

    if entscheid.art == budget.SPARMODELL:
        payload["model"] = entscheid.modell
    await redis_client.lpush(f"agent:{agent_id}:chat", json.dumps(payload))
    return Einreihung(True, modell=entscheid.modell)


def anhaenge_pruefen(roh) -> list[dict]:
    """Anhänge aus der Nachricht des Browsers — nur Dateien unter ``/workspace``.

    Die Pfade landen im Auftrag an das Modell und in der Datenbank. Deshalb hier
    hart: normalisiert, ohne Steuerzeichen, kein Ausbruch nach oben, keine
    Dubletten, höchstens ``ANHAENGE_MAX``.
    """
    if not isinstance(roh, list):
        return []
    aus: list[dict] = []
    gesehen: set[str] = set()
    for eintrag in roh[:ANHAENGE_MAX]:
        if isinstance(eintrag, str):
            eintrag = {"path": eintrag}
        if not isinstance(eintrag, dict):
            continue
        pfad = str(eintrag.get("path") or "")
        if not pfad or len(pfad) > _PFAD_MAX or _STEUERZEICHEN.search(pfad):
            continue
        sauber = posixpath.normpath(pfad)
        if sauber != pfad.rstrip("/") or not sauber.startswith(WURZEL + "/"):
            continue
        if sauber in gesehen:
            continue
        gesehen.add(sauber)
        anhang: dict = {"path": sauber, "filename": posixpath.basename(sauber)}
        groesse = eintrag.get("size")
        if isinstance(groesse, (int, float)) and not isinstance(groesse, bool) and groesse >= 0:
            anhang["size"] = int(groesse)
        art = eintrag.get("media_type")
        if isinstance(art, str) and art and len(art) <= 100 and not _STEUERZEICHEN.search(art):
            anhang["media_type"] = art
        aus.append(anhang)
    return aus


def anhang_hinweis(anhaenge: list[dict]) -> str:
    """Die Lese-Anweisung an das Modell (ohne sie öffnete es die Datei nicht)."""
    pfade = ", ".join(a["path"] for a in anhaenge)
    return f"{_ANHANG_KOPF}{pfade}. {_ANHANG_ANWEISUNG}]"


def auftrag(text: str, anhaenge: list[dict] | None = None, plan: bool = False) -> str:
    """Der Text, den der Agent bekommt — in jeder Laufzeit derselbe."""
    aus = text or ""
    if anhaenge:
        hinweis = anhang_hinweis(anhaenge)
        aus = f"{aus}\n\n{hinweis}" if aus else hinweis
    if plan:
        aus = f"{_PLAN_KOPF}\n\nAufgabe: {aus}"
    return aus


def _aus_altem_text(text: str) -> tuple[str, list[dict], bool] | None:
    """Einen vom alten Browser zusammengebauten Text zerlegen. ``None``: keine Altform."""
    rest = text
    plan = False
    treffer = _ALT_PLAN.match(rest)
    if treffer:
        plan = True
        rest = treffer.group("rest")
    anhaenge: list[dict] = []
    treffer = _ALT_ANHANG.match(rest)
    if treffer:
        anhaenge = anhaenge_pruefen([p.strip() for p in treffer.group("pfade").split(", ")])
        rest = treffer.group("text")
    if not plan and not treffer:
        return None
    return rest.strip(), anhaenge, plan


def aus_nachricht(msg: dict) -> tuple[str, list[dict], bool]:
    """Eingehende Nachricht → (Nutzertext, Anhänge, Plan).

    Ein alter Browser-Tab schickt den zusammengebauten Text ohne ``anhaenge``
    und ``plan``. Der wird zerlegt, damit auch dann nur der Nutzertext in den
    Verlauf und in den Titel kommt.
    """
    text = str(msg.get("text") or "").strip()
    if "anhaenge" in msg or "plan" in msg:
        return text, anhaenge_pruefen(msg.get("anhaenge")), bool(msg.get("plan"))
    alt = _aus_altem_text(text)
    if alt is not None:
        return alt
    return text, [], False


def mit_verlauf_hinweis(text: str, meta: dict | None) -> str:
    """Nutzertext samt kurzem Anhang-Vermerk — für Laufzeiten, die ihren Verlauf
    nachladen (Custom-LLM). Ohne den wüsste das Modell in der nächsten Runde
    nicht mehr, welche Datei gemeint war."""
    anhaenge = (meta or {}).get("anhaenge")
    if not isinstance(anhaenge, list) or not anhaenge:
        return text
    pfade = ", ".join(str(a.get("path")) for a in anhaenge if isinstance(a, dict) and a.get("path"))
    if not pfade:
        return text
    vermerk = f"{_ANHANG_KOPF}{pfade}]"
    return f"{text}\n\n{vermerk}" if text else vermerk


async def altzeilen_bereinigen(db: AsyncSession) -> tuple[int, int]:
    """Bestehende Nutzerzeilen mit eingebauter Anweisung umschreiben.

    Der Nutzertext bleibt, die Pfade wandern nach ``meta.anhaenge``, der Plan nach
    ``meta.plan``. Ein automatisch abgeleiteter Titel, der aus der verschmutzten
    Zeile entstand, wird neu abgeleitet; ein selbst vergebener bleibt.

    Rückgabe: (umgeschriebene Zeilen, neu abgeleitete Titel). Idempotent.
    """
    from app.core.chat_history import derive_title, ensure_title
    from app.models.chat_message import ChatMessage
    from app.models.chat_session import ChatSession

    zeilen = (await db.execute(
        select(ChatMessage).where(
            ChatMessage.role == "user",
            or_(
                ChatMessage.content.like(f"%{_ANHANG_KOPF}%"),
                ChatMessage.content.like("[NUR PLANEN%"),
            ),
        )
    )).scalars().all()

    alte_titel: dict[tuple[str, str], set[str]] = {}
    umgeschrieben = 0
    for zeile in zeilen:
        zerlegt = _aus_altem_text(zeile.content or "")
        if zerlegt is None:
            continue
        text, anhaenge, plan = zerlegt
        alte_titel.setdefault((zeile.agent_id, zeile.session_id), set()).add(
            derive_title(zeile.content or ""))
        zeile.content = text
        meta = dict(zeile.meta or {})
        if anhaenge:
            meta["anhaenge"] = anhaenge
        if plan:
            meta["plan"] = True
        zeile.meta = meta
        umgeschrieben += 1
    await db.flush()

    titel_neu = 0
    for (agent_id, session_id), kandidaten in alte_titel.items():
        sitzung = await db.scalar(select(ChatSession).where(
            ChatSession.agent_id == agent_id, ChatSession.session_id == session_id,
        ))
        if sitzung is None or not sitzung.title:
            continue
        verschmutzt = (
            sitzung.title in kandidaten
            or _ANHANG_KOPF.strip() in sitzung.title
            or sitzung.title.startswith("[NUR PLANEN")
        )
        if not verschmutzt:
            continue
        sitzung.title = None
        await db.flush()
        await ensure_title(db, agent_id, session_id)
        titel_neu += 1
    await db.commit()
    return umgeschrieben, titel_neu
