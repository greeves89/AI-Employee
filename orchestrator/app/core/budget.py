"""Monatsbudget: EINE Entscheidung für Aufgaben und Chat (#898).

Bis v1.362 prüfte nur der Aufgabenweg (``task_router._apply_budget_policy``) das
Budget. Web-Chat, Kiosk, Telegram, die Kanäle und die Sprachfront legten ihre
Nachrichten ungeprüft in ``agent:{id}:chat`` — ein Agent mit aufgebrauchtem Budget
arbeitete im Chat einfach weiter. Dazu zählte die Prüfung nur Aufgabenkosten.

Jetzt gilt für beide Wege dieselbe Entscheidung, ``budget_pruefen``:

* **erlauben** — kein Budget gesetzt oder noch Luft;
* **sparmodell** — Budget aufgebraucht, Aktion „Sparmodus" und die Laufzeit hat
  laut Modellkatalog ein günstigeres Modell (``model_catalog.sparmodell_fuer``).
  Codex und Custom-LLM haben keins: früher bekamen sie trotzdem Claude-Haiku
  vorgesetzt, die Modellprüfung setzte es stillschweigend zurück, und das Budget
  war wirkungslos;
* **blockieren** — sonst. Der Auftrag wird nicht eingereiht, der Mensch bekommt
  einen deutschen Hinweis, Besitzer und Administratoren eine Benachrichtigung.

Vorgabe für neue Agenten ist „stop" (vorher „haiku"). Gezählt wird über die eine
Kostenquelle ``core/kosten.py`` — Aufgaben UND Chat des laufenden Monats.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select

from app.config import settings
from app.core import kosten as _kosten

logger = logging.getLogger(__name__)

ERLAUBEN = "erlauben"
SPARMODELL = "sparmodell"
BLOCKIEREN = "blockieren"

AKTION_STOPP = "stop"
AKTION_SPARMODUS = "haiku"

#: Merker in ``agent.config``: Bestandsagent mit Budget im Sparmodus, dessen
#: Einstellung ein Administrator nach der Umstellung noch nicht bestätigt hat.
HINWEIS_SCHLUESSEL = "budget_hinweis_offen"

#: Benachrichtigungen an die Administratoren laufen über den Systemabsender —
#: Administratoren sehen sonst nur die Meldungen ihrer EIGENEN Agenten.
SYSTEM_ABSENDER = "system"


@dataclass(frozen=True)
class Entscheid:
    art: str
    modell: str | None = None
    #: Was der Mensch liest (Chat, Telegram, Kanal) — deutsch, ohne Interna.
    hinweis: str = ""
    #: Welche Grenze gerissen ist: "agent" | "nutzer" | "anlage".
    grenze: str = ""
    verbraucht_usd: float = 0.0
    budget_usd: float = 0.0

    @property
    def erlaubt(self) -> bool:
        return self.art != BLOCKIEREN


ERLAUBT = Entscheid(ERLAUBEN)


def laufzeit(agent) -> tuple[str, str | None]:
    """(Laufzeit, Anbieter) — ein Claude-Code-Agent über den Codex-Anbieter ist Codex."""
    mode = getattr(agent, "mode", None) or "claude_code"
    provider = (getattr(agent, "config", None) or {}).get("model_provider") or settings.model_provider
    if mode == "claude_code" and provider == "codex":
        return "codex_cli", "codex"
    if mode == "codex_cli":
        return mode, "codex"
    return mode, provider


def sparmodell_fuer_agent(agent) -> str | None:
    from app.core.model_catalog import sparmodell_fuer

    mode, provider = laufzeit(agent)
    return sparmodell_fuer(mode, provider)


def _hinweis(agent, grenze: str, verbraucht: float, limit: float, sparmodus_fehlt: bool) -> str:
    wer = {
        "agent": f"Das Monatsbudget von „{agent.name}“",
        "nutzer": "Das Monatsbudget deines Kontos",
        "anlage": "Das Monatsbudget dieser Installation",
    }[grenze]
    satz = (
        f"{wer} ist aufgebraucht ({_kosten.betrag_anzeigen(verbraucht)} von "
        f"{_kosten.betrag_anzeigen(limit)}). Neue Nachrichten und Aufgaben werden bis "
        "zum Monatsende nicht bearbeitet."
    )
    if sparmodus_fehlt:
        satz += " Für diese Laufzeit gibt es kein günstigeres Modell, deshalb wird angehalten."
    return satz + " Ein Administrator kann das Budget unter Verwaltung → Budget erhöhen."


async def budget_pruefen(db, agent, *, fuer_chat: bool = False) -> Entscheid:
    """Darf ``agent`` jetzt arbeiten — und mit welchem Modell?

    Reihenfolge: Anlage, Agent, Besitzer. Ohne gesetzte Grenze wird nichts
    gezählt (eine Chat-Nachricht soll nicht für drei Summen bezahlen).
    """
    if agent is None:
        return ERLAUBT
    if fuer_chat and not getattr(settings, "budget_gilt_fuer_chat", True):
        return ERLAUBT

    monat = _kosten.monatsbeginn()
    grenze, verbraucht, limit = "", 0.0, 0.0

    anlage_limit = float(getattr(settings, "platform_budget_usd", 0) or 0)
    if anlage_limit > 0:
        summe = (await _kosten.kosten(db, _kosten.Bereich.anlage(), seit=monat)).gesamt
        if summe >= anlage_limit:
            grenze, verbraucht, limit = "anlage", summe, anlage_limit

    if not grenze and agent.budget_usd is not None and agent.budget_usd > 0:
        summe = (await _kosten.kosten(db, _kosten.Bereich.agent(agent.id), seit=monat)).gesamt
        if summe >= agent.budget_usd:
            grenze, verbraucht, limit = "agent", summe, float(agent.budget_usd)

    if not grenze and agent.user_id:
        from app.models.user import User

        besitzer = await db.scalar(select(User).where(User.id == agent.user_id))
        if besitzer is not None and besitzer.budget_usd is not None and besitzer.budget_usd > 0:
            summe = (await _kosten.kosten(
                db, _kosten.Bereich.besitzer(agent.user_id), seit=monat)).gesamt
            if summe >= besitzer.budget_usd:
                grenze, verbraucht, limit = "nutzer", summe, float(besitzer.budget_usd)

    if not grenze:
        return ERLAUBT

    # Die Anlagengrenze kennt keinen Sparmodus — sie ist die harte Obergrenze.
    if grenze != "anlage" and (agent.budget_exceeded_action or AKTION_STOPP) == AKTION_SPARMODUS:
        modell = sparmodell_fuer_agent(agent)
        if modell:
            return Entscheid(SPARMODELL, modell=modell, grenze=grenze,
                             verbraucht_usd=verbraucht, budget_usd=limit)
        return Entscheid(BLOCKIEREN, hinweis=_hinweis(agent, grenze, verbraucht, limit, True),
                         grenze=grenze, verbraucht_usd=verbraucht, budget_usd=limit)
    return Entscheid(BLOCKIEREN, hinweis=_hinweis(agent, grenze, verbraucht, limit, False),
                     grenze=grenze, verbraucht_usd=verbraucht, budget_usd=limit)


async def _schon_gemeldet(db, absender: str, ziel: str) -> bool:
    from app.models.notification import Notification

    return bool(await db.scalar(
        select(Notification.id).where(
            Notification.agent_id == absender,
            Notification.action_url == ziel,
            Notification.created_at >= _kosten.monatsbeginn(),
        ).limit(1)
    ))


async def _melden(db, *, absender: str, ziel: str, typ: str, titel: str, text: str,
                  agent_id: str) -> bool:
    """Eine Benachrichtigung je Empfänger, Anlass und Monat."""
    from app.models.notification import Notification

    if await _schon_gemeldet(db, absender, ziel):
        return False
    db.add(Notification(
        agent_id=absender, type=typ, title=titel, message=text, priority="high",
        action_url=ziel, meta={"budget_agent_id": agent_id},
    ))
    return True


async def sperre_melden(db, agent, entscheid: Entscheid) -> bool:
    """Besitzer und Administratoren benachrichtigen — einmal je Agent und Monat.

    Gibt zurück, ob etwas Neues gemeldet wurde. Committet selbst: der Aufrufer
    lehnt gerade einen Auftrag ab und hat sonst nichts mehr zu speichern.
    """
    if entscheid.art != BLOCKIEREN or agent is None:
        return False
    text = (
        f"„{agent.name}“ hat einen Auftrag abgelehnt: das Budget ist aufgebraucht "
        f"({_kosten.betrag_anzeigen(entscheid.verbraucht_usd)} von "
        f"{_kosten.betrag_anzeigen(entscheid.budget_usd)}). Neue Nachrichten und "
        "Aufgaben bleiben bis zum Monatsende liegen, sofern das Budget nicht erhöht wird."
    )
    titel = f"Budget aufgebraucht: {agent.name}"
    neu_admin = await _melden(
        db, absender=SYSTEM_ABSENDER, ziel=f"/admin?tab=budget&agent={agent.id}",
        typ="error", titel=titel, text=text, agent_id=agent.id,
    )
    neu_besitzer = await _melden(
        db, absender=agent.id, ziel=f"/agents/{agent.id}?budget=gesperrt",
        typ="error", titel=titel, text=text, agent_id=agent.id,
    )
    if neu_admin or neu_besitzer:
        await db.commit()
    return neu_admin or neu_besitzer


async def schwellen_pruefen(db, agent) -> None:
    """Nach einem Lauf: Warnung ab 80 % des Agentenbudgets, einmal je Monat.

    Ersetzt ``TaskRouter._check_budget_thresholds``, das nur Aufgabenkosten kannte
    und bei JEDER fertigen Aufgabe erneut „überschritten" meldete.
    """
    if agent is None or agent.budget_usd is None or agent.budget_usd <= 0:
        return
    summe = (await _kosten.kosten(
        db, _kosten.Bereich.agent(agent.id), seit=_kosten.monatsbeginn())).gesamt
    anteil = summe / agent.budget_usd
    if anteil >= 1.0:
        entscheid = await budget_pruefen(db, agent)
        if entscheid.art == BLOCKIEREN:
            await sperre_melden(db, agent, entscheid)
        return
    if anteil >= 0.8:
        await _melden(
            db, absender=agent.id, ziel=f"/agents/{agent.id}?budget=warnung", typ="warning",
            titel=f"Budget fast aufgebraucht: {agent.name}",
            text=(f"„{agent.name}“ hat {anteil:.0%} seines Monatsbudgets verbraucht "
                  f"({_kosten.betrag_anzeigen(summe)} von "
                  f"{_kosten.betrag_anzeigen(agent.budget_usd)})."),
            agent_id=agent.id,
        )


# ── Umstellung des Bestands ──────────────────────────────────────────────────

def hinweis_offen(agent) -> bool:
    return bool((getattr(agent, "config", None) or {}).get(HINWEIS_SCHLUESSEL))


def hinweis_erledigen(agent) -> None:
    """Ein Administrator hat die Budgetaktion gespeichert — der Hinweis ist erledigt."""
    if hinweis_offen(agent):
        config = dict(agent.config or {})
        config.pop(HINWEIS_SCHLUESSEL, None)
        agent.config = config


async def stopp_vorgabe_umstellen(db) -> tuple[int, int]:
    """Bestand auf die neue Vorgabe ziehen (einmalig beim Start, idempotent).

    * ohne Budget → „stop": wirkungsgleich, es gibt nichts aufzubrauchen;
    * mit Budget im Sparmodus → bleibt, wie eingestellt; der Concierge weist
      darauf hin, bis ein Administrator die Einstellung einmal speichert.

    Rückgabe: (auf „stop" umgestellt, Hinweise gesetzt).
    """
    from app.models.agent import Agent

    umgestellt = hinweise = 0
    for agent in (await db.execute(
        select(Agent).where(Agent.budget_exceeded_action == AKTION_SPARMODUS)
    )).scalars().all():
        if agent.budget_usd is None or agent.budget_usd <= 0:
            agent.budget_exceeded_action = AKTION_STOPP
            umgestellt += 1
        elif not hinweis_offen(agent):
            agent.config = {**(agent.config or {}), HINWEIS_SCHLUESSEL: True}
            hinweise += 1
    if umgestellt or hinweise:
        await db.commit()
    return umgestellt, hinweise
