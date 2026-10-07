"""Inhaltsfilter des Sprach-Anbieters: ein beanstandeter Eintrag darf nicht alles sperren.

Befund vom 07.10.2026: Der Anbieter (Nova Sonic) lehnte JEDE Sprachsitzung eines
Agenten schon beim Aufbau ab („blocked by our content filters“) — auch leere,
auch nach einem Wechsel des Gesprächs. Ursache waren zwei harmlose Einträge im
Gedächtnisblock (eine Notiz zu Videoformaten, ein Rendering-Fehler), der bei
jeder Sitzung mitgeschickt wird. Der Systemprompt allein ging durch.

Der Filter ist nicht vorhersehbar. Deshalb:

* Blockt der Anbieter beim Aufbau, verbindet sich die Sitzung sofort OHNE
  Gedächtnis und Verlauf neu (``ohne_gedaechtnis_merken``).
* Im Hintergrund wird jeder Eintrag einzeln geprüft; die beanstandeten bleiben
  künftig weg (``gesperrte_merken``), der Rest des Gedächtnisses kommt zurück.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

#: So lange startet der Agent ohne Gedächtnis, wenn die Ursache (noch) nicht feststeht.
OHNE_GEDAECHTNIS_SEKUNDEN = 24 * 3600
#: So lange bleibt ein beanstandeter Eintrag weg. Danach wird er wieder versucht —
#: Filter ändern sich, und ein dauerhaft verschwiegener Eintrag wäre ein stiller Verlust.
GESPERRT_SEKUNDEN = 7 * 24 * 3600


def _ohne(agent_id: str, user_id: str | None) -> str:
    # Je Agent UND Person: den schlanken Start löst auch der geladene VERLAUF aus,
    # und der gehört dem, der gerade spricht. Eine Marke nur je Agent nähme sonst
    # dem Besitzer das Gedächtnis, weil ein Mitbenutzer etwas Beanstandetes sagte.
    return f"voice:filter:ohne_gedaechtnis:{agent_id}:{user_id or 'unbekannt'}"


def _gesperrt(agent_id: str) -> str:
    return f"voice:filter:gesperrt:{agent_id}"


def ist_inhaltsfilter(meldung: str) -> bool:
    return "content filter" in (meldung or "").lower()


async def ohne_gedaechtnis(redis_client, agent_id: str, user_id: str | None) -> bool:
    try:
        return bool(await redis_client.exists(_ohne(agent_id, user_id)))
    except Exception:  # noqa: BLE001 — ohne Redis lieber mit Gedächtnis als gar nicht
        return False


async def ohne_gedaechtnis_merken(redis_client, agent_id: str, user_id: str | None) -> None:
    try:
        await redis_client.set(_ohne(agent_id, user_id), "1", ex=OHNE_GEDAECHTNIS_SEKUNDEN)
    except Exception as e:  # noqa: BLE001
        logger.warning("[Sprache] Schlanker Start nicht gemerkt (agent=%s): %s", agent_id, e)


async def gesperrte(redis_client, agent_id: str) -> frozenset[str]:
    """Schlüssel der Gedächtniseinträge, die der Anbieter zuletzt beanstandet hat."""
    try:
        roh = await redis_client.get(_gesperrt(agent_id))
    except Exception:  # noqa: BLE001
        return frozenset()
    if not roh:
        return frozenset()
    try:
        werte = json.loads(roh.decode() if isinstance(roh, bytes) else roh)
    except (ValueError, AttributeError):
        return frozenset()
    return frozenset(str(w) for w in werte if w) if isinstance(werte, list) else frozenset()


async def gesperrte_merken(redis_client, agent_id: str, schluessel, user_id: str | None = None) -> None:
    """Beanstandete Einträge festhalten und den schlanken Start (dieser Person) aufheben.

    Die Liste gilt je Agent: sie stammt aus Prüfungen der Gedächtniseinträge beim
    Anbieter, nicht aus dem, was jemand gesagt hat."""
    alle = sorted(set(await gesperrte(redis_client, agent_id)) | {str(s) for s in schluessel if s})
    try:
        await redis_client.set(_gesperrt(agent_id), json.dumps(alle), ex=GESPERRT_SEKUNDEN)
        await redis_client.delete(_ohne(agent_id, user_id))
    except Exception as e:  # noqa: BLE001
        logger.warning("[Sprache] Beanstandete Einträge nicht gemerkt (agent=%s): %s", agent_id, e)


async def beanstandete_finden(eintraege: list[dict], wird_blockiert) -> list[str]:
    """Jeden Eintrag einzeln prüfen; Rückgabe: Schlüssel der beanstandeten.

    ``wird_blockiert(text) -> bool`` baut eine Mini-Sitzung beim Anbieter auf.
    Ein Prüffehler (Netz, Zeitüberschreitung) zählt NICHT als Beanstandung.
    """
    from app.core.memory_preload import eintrag_zeile

    gefunden: list[str] = []
    for eintrag in eintraege:
        schluessel = str(eintrag.get("key") or "")
        if not schluessel:
            continue
        try:
            if await wird_blockiert(eintrag_zeile(eintrag)):
                gefunden.append(schluessel)
        except Exception as e:  # noqa: BLE001
            logger.info("[Sprache] Prüfung von %r übersprungen: %s", schluessel, e)
    return gefunden
