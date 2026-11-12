"""Preis je Modell aus dem KI-Konto — und die Kosten eines Laufs, wenn der Agent keine kennt.

Ein Agent rechnet seine Kosten selbst aus seiner Preistabelle (``agent/app/model_registry``).
Für Modelle, deren Preis dort (noch) nicht steht — etwa neue Azure-Modelle wie gpt-6.x —
meldet er 0 USD: kein Betrag im Dashboard, und das Monatsbudget (``core/budget``) greift nie.

Hier trägt die Administration den Preis je Modell im KI-Konto ein
(``ai_accounts.models[].preis_eingabe_usd_1m`` / ``preis_ausgabe_usd_1m``, USD je 1 Mio.
Token). Meldet ein Lauf keinen Betrag, aber Token, rechnet der Orchestrator aus diesem
Preis nach. Ein vom Agenten gemeldeter Betrag > 0 bleibt unangetastet.

EINE Stelle für alle Wege: Aufgaben (``task_router.handle_task_completion``), Chat mit
Verlaufszeile (``chat_persistence.done_kennzahlen``) und Chat ohne Verlaufszeile
(``main._kosten_ohne_verlauf``).
"""

from __future__ import annotations

import logging
import math
import time

logger = logging.getLogger(__name__)

#: Obergrenze gegen Tippfehler (USD je 1 Mio. Token); teuerste Modelle liegen weit darunter.
PREIS_MAX = 1000.0
#: Wie lange ein nachgeschlagener Preis je Agent gilt (Sekunden). Kurz, damit eine
#: Preisänderung im KI-Konto schnell greift, aber nicht jedes Chat-Ereignis die DB fragt.
_TTL = 60.0
_cache: dict[str, tuple[float, tuple[float, float] | None]] = {}


def preis_pruefen(wert) -> float | None:
    """Preisangabe als Zahl ≥ 0 und ≤ PREIS_MAX, sonst None."""
    if wert is None or wert == "" or isinstance(wert, bool):
        return None
    try:
        zahl = float(wert)
    except (TypeError, ValueError):
        return None
    if math.isnan(zahl) or zahl < 0 or zahl > PREIS_MAX:  # NaN, negativ, unplausibel
        return None
    return zahl


def preis_aus_konto(modelle: list | None, modell: str | None) -> tuple[float, float] | None:
    """Preis (Eingabe, Ausgabe) je 1 Mio. Token für ``modell`` aus der Modellliste eines KI-Kontos."""
    if not modell:
        return None
    ziel = modell.strip().lower()
    for eintrag in modelle or []:
        if not isinstance(eintrag, dict) or str(eintrag.get("name", "")).strip().lower() != ziel:
            continue
        ein = preis_pruefen(eintrag.get("preis_eingabe_usd_1m"))
        aus = preis_pruefen(eintrag.get("preis_ausgabe_usd_1m"))
        if ein is not None and aus is not None:
            return ein, aus
    return None


def preis_aus_katalog(modell: str | None) -> tuple[float, float] | None:
    """Eingebaute Preistabelle (``core/pricing``), exakt oder per Präfix — nur zur Anzeige
    im KI-Konto als Vorschlag, NICHT für die Nachrechnung (siehe ``preis_fuer_agent``)."""
    if not modell:
        return None
    from app.core.pricing import MODEL_PRICING

    if modell in MODEL_PRICING:
        return MODEL_PRICING[modell]
    treffer = [k for k in MODEL_PRICING if modell.startswith(k)]
    return MODEL_PRICING[max(treffer, key=len)] if treffer else None


def betrag(gemeldet, eingabe_token, ausgabe_token, preis: tuple[float, float] | None) -> float | None:
    """Kosten eines Laufs in USD.

    Gemeldeter Betrag > 0 gewinnt. Sonst, wenn Preis und Token bekannt sind: nachgerechnet.
    Sonst der gemeldete Wert (0 oder None) unverändert — nie ein erfundener Betrag.
    """
    from app.services.chat_persistence import betrag_pruefen

    wert = betrag_pruefen(gemeldet)
    if wert:
        return wert
    if not preis:
        return wert
    try:
        ein = int(eingabe_token or 0)
        aus = int(ausgabe_token or 0)
    except (TypeError, ValueError):
        return wert
    if ein <= 0 and aus <= 0:
        return wert
    return round(ein / 1_000_000 * preis[0] + aus / 1_000_000 * preis[1], 6)


async def preis_fuer_agent(agent_id: str | None, modell: str | None = None, db=None) -> tuple[float, float] | None:
    """Preis für das Modell eines Agenten — NUR aus seinem KI-Konto.

    Bewusst ohne Rückfall auf die eingebaute Tabelle: 0 USD ist bei Abo-Läufen (Claude,
    Codex) richtig, und Modelle aus der Tabelle rechnet der Agent schon selbst. Nachgerechnet
    wird nur, was die Administration ausdrücklich im KI-Konto bepreist hat.
    ``modell`` überschreibt das Agentenmodell (ein Lauf kann auf ein anderes ausweichen).
    """
    if not agent_id:
        return None
    schluessel = f"{agent_id}:{modell or ''}"
    jetzt = time.monotonic()
    treffer = _cache.get(schluessel)
    if treffer and jetzt - treffer[0] < _TTL:
        return treffer[1]
    preis = None
    try:
        from app.models.agent import Agent
        from app.models.ai_account import AIAccount

        async def _nachschlagen(sitzung):
            agent = await sitzung.get(Agent, agent_id)
            if not agent or not agent.ai_account_id:
                return None
            konto = await sitzung.get(AIAccount, agent.ai_account_id)
            return preis_aus_konto(konto.models if konto else None, modell or agent.model)

        if db is not None:
            preis = await _nachschlagen(db)
        else:
            from app.db.session import async_session_factory

            async with async_session_factory() as sitzung:
                preis = await _nachschlagen(sitzung)
    except Exception as e:  # noqa: BLE001 — Kosten dürfen keinen Lauf und keinen Chat stören
        from app.core.log_redaction import scrub_log

        logger.warning("Modellpreis für Agent %s nicht ermittelbar: %s", scrub_log(agent_id), scrub_log(e))
        return None
    _cache[schluessel] = (jetzt, preis)
    return preis


def cache_leeren() -> None:
    """Nach einer Änderung am KI-Konto sofort mit den neuen Preisen rechnen."""
    _cache.clear()
