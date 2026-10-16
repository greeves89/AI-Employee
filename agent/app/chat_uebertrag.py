"""Der Gesprächsfaden reißt nicht ab — Übergabe und Übertrag für Chat-Sitzungen.

Ein Chat läuft als EINE Sitzung der Laufzeit (Claude Code ``--resume``, Codex
``exec resume <id>``) und wird fortgesetzt. Eine NEUE Sitzung gibt es nur in
Sonderfällen: der Verlauf ist zu lang geworden, der Agent wurde neu aufgesetzt,
oder eine Übergabe hat die Sitzung bewusst gewechselt (siehe unten). Bis hierhin
begann die neue Sitzung dann bei null — alles, was nur im Gespräch stand
(Korrekturen, Absprachen, Zwischenstände), war für den Agenten weg, obwohl es in
der Datenbank liegt und der Nutzer es in der Oberfläche weiter sieht.

Vorbild ist die Verdichtung in Hermes Agent: Anfang und Ende wörtlich, die Mitte
als strukturierte Zusammenfassung (Ziel, Vorgaben, Stand, Entscheidungen, Dateien,
nächste Schritte, wichtiger Kontext) — und vor dem Verdichten ein Gedächtnis-
Abgleich wie bei OpenClaw.

Zwei Bausteine:

- **Übergabe** (``UEBERGABE_AUFTRAG``): wird der Kontext voll, bekommt die noch
  laufende Sitzung einen stillen Zug. Darin sichert der Agent dauerhaft, was über
  das Gespräch hinaus gilt (memory_save, skill_update), und schreibt die Übergabe.
  Sie liegt danach in Redis, die Sitzung wird gewechselt.
- **Übertrag** (``uebertrag_block``): die erste Nachricht einer neuen Sitzung in
  einem Gespräch MIT Vorgeschichte bekommt die letzte Übergabe und die jüngsten
  Nachrichten wörtlich aus der Datenbank vorangestellt. Ältere Stellen findet der
  Agent mit ``chat_search``.

Ein bewusster Neuanfang (``/reset`` bzw. „Neues Gespräch“) setzt eine Marke: was
davor lag, wird nicht übertragen.
"""

from __future__ import annotations

import json
import logging
import time

logger = logging.getLogger(__name__)

#: Höchstzahl Nachrichten, die aus der Datenbank geholt werden.
VERLAUF_MAX = 40
#: Zeichenbudget für die wörtlich übertragenen Nachrichten (jüngste zuerst).
UEBERTRAG_ZEICHEN = 14_000
#: Eine einzelne Nachricht wird im Übertrag höchstens so lang wiedergegeben.
NACHRICHT_ZEICHEN = 2_000

_UEBERGABE_KEY = "agent:{agent_id}:chat_uebergabe:{source_key}"
_NEUANFANG_KEY = "agent:{agent_id}:chat_neuanfang:{source_key}"

UEBERGABE_AUFTRAG = """[Plattform — Gesprächsübergabe, NICHT an den Nutzer gerichtet]
Dieses Gespräch ist lang geworden und wird gleich in einer frischen Sitzung
fortgesetzt. Was nur im Verlauf steht, ist danach weg. Erledige zwei Dinge:

1. Sichere DAUERHAFT, was über dieses Gespräch hinaus gelten soll:
   - Korrekturen und Vorgaben des Nutzers („mach das nicht so, sondern so“) mit
     memory_save (category "learning", tags ["correction"] oder ["user_preference"],
     importance 4 bis 5, tag_type "permanent"), wörtlich zitiert.
   - Hat der Nutzer verlangt, eine Arbeitsweise dauerhaft zu ändern, und hast du das
     noch nicht getan: den Skill anpassen (skill_update) bzw. neu anlegen (create_skill).
   Nichts doppelt speichern, was schon gesichert ist.

2. Antworte danach AUSSCHLIESSLICH mit der Übergabe in genau dieser Gliederung
   (Markdown, höchstens 900 Wörter, keine Einleitung, kein Schlusssatz):

## Ziel
## Vorgaben und Vorlieben des Nutzers
(wörtlich zitieren, wo möglich — besonders Korrekturen)
## Stand
(erledigt / in Arbeit / blockiert)
## Entscheidungen
## Dateien und Orte
## Nächste Schritte
## Wichtiger Kontext
"""


def chat_sitzung_der_quelle(source_key: str) -> str:
    """``source_key`` des Agenten → ``ChatMessage.session_id`` im Orchestrator.

    ``webapp:<sid>``, ``ios:<sid>``, ``voice:<sid>`` → ``<sid>``; Kanäle wie
    ``telegram:<chat_id>`` tragen den Präfix auch in der Datenbank.
    """
    art, _, rest = source_key.partition(":")
    if art in ("webapp", "ios", "voice") and rest:
        return rest
    return source_key


async def verlauf_laden(session_id: str | None, limit: int = VERLAUF_MAX) -> list[dict]:
    """Die jüngsten Wortmeldungen dieses Gesprächs aus dem Orchestrator.

    Best effort: geht es schief, gibt es keinen Übertrag — das ist der Zustand von
    vorher und darf den Zug nicht kippen. Nur ``user``/``assistant`` mit Inhalt;
    ``system``-Zeilen sind Kacheln und Statusmeldungen der Oberfläche.
    """
    if not session_id:
        return []
    try:
        from app.tools.api_client import OrchestratorAPIClient
        client = OrchestratorAPIClient()
        try:
            antwort = await client._request(
                "GET",
                f"/agents/{client.agent_id}/chat/history",
                params={"session_id": session_id, "limit": limit},
            )
        finally:
            await client._client.aclose()
    except Exception as e:  # noqa: BLE001
        logger.warning("[Übertrag] Verlauf nicht ladbar: %s", e)
        return []
    if not isinstance(antwort, dict):
        logger.warning("[Übertrag] Verlauf nicht ladbar: %s", str(antwort)[:200])
        return []
    zuege = []
    for eintrag in antwort.get("messages") or []:
        rolle = eintrag.get("role")
        inhalt = (eintrag.get("content") or "").strip()
        if rolle in ("user", "assistant") and inhalt:
            zuege.append({"role": rolle, "content": inhalt,
                          "timestamp": eintrag.get("timestamp") or ""})
    return zuege


async def uebergabe_speichern(redis, agent_id: str, source_key: str, text: str) -> None:
    if redis is None or not (text or "").strip():
        return
    try:
        await redis.set(_UEBERGABE_KEY.format(agent_id=agent_id, source_key=source_key),
                        json.dumps({"text": text.strip(), "zeit": time.time()}))
    except Exception as e:  # noqa: BLE001
        logger.warning("[Übertrag] Übergabe nicht gespeichert: %s", e)


async def uebergabe_laden(redis, agent_id: str, source_key: str) -> str:
    if redis is None:
        return ""
    try:
        roh = await redis.get(_UEBERGABE_KEY.format(agent_id=agent_id, source_key=source_key))
        return (json.loads(roh) or {}).get("text", "") if roh else ""
    except Exception as e:  # noqa: BLE001
        logger.warning("[Übertrag] Übergabe nicht lesbar: %s", e)
        return ""


async def neuanfang_markieren(redis, agent_id: str, source_key: str) -> None:
    """Bewusster Neuanfang: keine Übergabe mehr, und älterer Verlauf bleibt draußen."""
    if redis is None:
        return
    try:
        await redis.delete(_UEBERGABE_KEY.format(agent_id=agent_id, source_key=source_key))
        await redis.set(_NEUANFANG_KEY.format(agent_id=agent_id, source_key=source_key),
                        str(time.time()))
    except Exception as e:  # noqa: BLE001
        logger.warning("[Übertrag] Neuanfang nicht markiert: %s", e)


async def _neuanfang(redis, agent_id: str, source_key: str) -> float:
    if redis is None:
        return 0.0
    try:
        roh = await redis.get(_NEUANFANG_KEY.format(agent_id=agent_id, source_key=source_key))
        return float(roh) if roh else 0.0
    except Exception:  # noqa: BLE001
        return 0.0


def _zeitstempel(iso: str) -> float:
    from datetime import datetime, timezone
    try:
        zeit = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return 0.0
    if zeit.tzinfo is None:
        zeit = zeit.replace(tzinfo=timezone.utc)
    return zeit.timestamp()


def _juengste_woertlich(zuege: list[dict], aktuelle_nachricht: str) -> list[dict]:
    """Jüngste Nachrichten im Zeichenbudget, in zeitlicher Reihenfolge.

    Die gerade eingehende Nachricht steht schon in der Datenbank — sie folgt unten
    als eigentliche Nachricht und wird hier nicht doppelt aufgeführt.
    """
    liste = list(zuege)
    if liste and liste[-1]["role"] == "user" and liste[-1]["content"].strip() == (aktuelle_nachricht or "").strip():
        liste = liste[:-1]
    auswahl: list[dict] = []
    rest = UEBERTRAG_ZEICHEN
    for zug in reversed(liste):
        inhalt = zug["content"]
        if len(inhalt) > NACHRICHT_ZEICHEN:
            inhalt = inhalt[:NACHRICHT_ZEICHEN].rstrip() + " […]"
        if len(inhalt) > rest:
            break
        auswahl.append({"role": zug["role"], "content": inhalt})
        rest -= len(inhalt)
    return list(reversed(auswahl))


async def uebertrag_block(redis, agent_id: str, source_key: str, aktuelle_nachricht: str,
                          zuege: list[dict] | None = None) -> str:
    """Vorspann für die ERSTE Nachricht einer neuen Sitzung in einem Gespräch mit
    Vorgeschichte — leer, wenn es keine gibt (neues Gespräch, nach Neuanfang).

    ``zuege`` nur für Tests; sonst aus dem Orchestrator geladen.
    """
    if zuege is None:
        zuege = await verlauf_laden(chat_sitzung_der_quelle(source_key))
    ab = await _neuanfang(redis, agent_id, source_key)
    if ab:
        zuege = [z for z in zuege if _zeitstempel(z.get("timestamp", "")) > ab]
    woertlich = _juengste_woertlich(zuege, aktuelle_nachricht)
    uebergabe = await uebergabe_laden(redis, agent_id, source_key)
    if not woertlich and not uebergabe:
        return ""

    teile = ["[Fortsetzung dieses Gesprächs — der Nutzer sieht den ganzen Verlauf; "
             "dir liegt er nur so weit vor, wie er hier steht. Ältere Stellen findest du "
             "mit chat_search.]"]
    if uebergabe:
        teile.append("### Übergabe aus der vorigen Sitzung\n" + uebergabe)
    if woertlich:
        zeilen = []
        for zug in woertlich:
            wer = "Nutzer" if zug["role"] == "user" else "Du"
            zeilen.append(f"**{wer}:** {zug['content']}")
        teile.append("### Die letzten Nachrichten (wörtlich)\n" + "\n\n".join(zeilen))
    teile.append("[Ende der Fortsetzung — es folgt die neue Nachricht.]")
    return "\n\n".join(teile) + "\n\n"


async def mit_uebertrag(handler, text: str) -> str:
    """Der Nachricht fuer eine neue Sitzung den Uebertrag voranstellen.

    ``handler.uebertrag_laden`` setzt der Chat-Consumer (er kennt Redis und Kanal);
    ohne ihn — oder wenn etwas schiefgeht — bleibt es beim Zustand von vorher.
    """
    laden = getattr(handler, "uebertrag_laden", None)
    if laden is None:
        return text
    try:
        return (await laden()) + text
    except Exception as e:  # noqa: BLE001
        logger.warning("[Übertrag] nicht erstellt: %s", e)
        return text
