"""Finanzen: was die Agenten kosten — je Monat, je Projekt, je Agent.

Rechnet nichts selbst: alle Beträge kommen aus ``core/kosten`` (der einen
Kostenquelle) und gelten in USD; umgerechnet wird nur für die Anzeige.

Nutzerbezogen — für JEDEN, auch Administratoren:
* Monate und Agenten: die eigenen und freigegebenen Agenten des Nutzers
  (``eigener_bereich``). Die Kosten der ganzen Anlage stehen nur in der
  Admin-Konsole (``api/admin.py``), nicht hier;
* Projekte nur, die der Nutzer sehen darf (``sichtbares_projekt``: Ersteller oder
  freigegeben — auch Administratoren sehen fremde Projekte nicht).

Chat hat keinen Projektbezug: Chat-Kosten stehen beim Agenten und in der Zeile
„Chat (ohne Projekt)“, Projektkosten sind die Läufe des Projekts (Karten, Takt,
Gespräch, Besprechungen).
"""

from __future__ import annotations

import csv
import io
from datetime import datetime

from sqlalchemy import func, select

from app.core import kosten as _kosten

MAX_MONATE = 24


def _monat_plus(start: datetime, n: int) -> datetime:
    jahr, monat = start.year + (start.month - 1 + n) // 12, (start.month - 1 + n) % 12 + 1
    return start.replace(year=jahr, month=monat)


def monate(anzahl: int, jetzt: datetime | None = None) -> list[tuple[str, datetime, datetime]]:
    """Die letzten ``anzahl`` Kalendermonate, ältester zuerst: (\"2026-10\", von, bis)."""
    anzahl = max(1, min(int(anzahl or 1), MAX_MONATE))
    aktuell = _kosten.monatsbeginn(jetzt)
    erg = []
    for i in range(anzahl - 1, -1, -1):
        von = _monat_plus(aktuell, -i)
        erg.append((f"{von:%Y-%m}", von, _monat_plus(von, 1)))
    return erg


async def eigener_bereich(db, user) -> _kosten.Bereich:
    """Eigene und freigegebene Agenten — ohne die Ausnahme für Administratoren,
    die ``kosten.bereich_fuer_nutzer`` (Dashboard der Anlage) macht."""
    from app.models.agent import Agent
    from app.models.agent_access import AgentAccess

    uid = str(getattr(user, "id", "") or "")
    eigene = set((await db.execute(select(Agent.id).where(Agent.user_id == uid))).scalars().all())
    eigene |= set((await db.execute(select(AgentAccess.agent_id).where(AgentAccess.user_id == uid))).scalars().all())
    return _kosten.Bereich.agenten(eigene)


async def sichtbare_projekte(db, user) -> list:
    from app.api.teams import _is_team_member
    from app.models.team import Team

    teams = (await db.execute(select(Team).where(Team.is_active == True))).scalars().all()  # noqa: E712
    return [t for t in teams if await _is_team_member(t, user, db)]


async def _ohne_projekt(db, bereich: _kosten.Bereich, seit, bis, ausser: list[str] | None = None) -> float:
    """Aufgaben im Bereich ohne Projekt (lebende Zeilen und Historie).

    Mit ``ausser``: stattdessen Aufgaben MIT Projekt, aber nicht in diesen Projekten —
    gelöschte Projekte oder solche, die der Nutzer nicht (mehr) sieht. Ohne diese Zeile
    ging die Summe der Seite nicht auf."""
    from app.models.kosten_historie import KostenHistorie as H
    from app.models.task import Task

    if bereich.ist_leer:
        return 0.0
    if ausser is None:
        bed_t, bed_h = Task.team_id.is_(None), H.team_id.is_(None)
    else:
        bed_t, bed_h = Task.team_id.isnot(None), H.team_id.isnot(None)
        if ausser:
            bed_t, bed_h = bed_t & Task.team_id.notin_(ausser), bed_h & H.team_id.notin_(ausser)
    stmt = select(func.coalesce(func.sum(Task.cost_usd), 0)).where(
        bed_t, Task.cost_usd.isnot(None), Task.created_at >= seit, Task.created_at < bis)
    hist = select(func.coalesce(func.sum(H.betrag_usd), 0)).where(
        bed_h, H.quelle == _kosten.QUELLE_AUFGABEN,
        H.tag >= seit.date(), H.tag < bis.date())
    if bereich.agent_ids is not None:
        stmt = stmt.where(Task.agent_id.in_(sorted(bereich.agent_ids)))
        hist = hist.where(H.agent_id.in_(sorted(bereich.agent_ids)))
    return float(await db.scalar(stmt) or 0) + float(await db.scalar(hist) or 0)


async def uebersicht(db, user, anzahl_monate: int = 6) -> dict:
    from app.dependencies import is_agent_principal
    from app.models.agent import Agent
    from app.models.user import User

    bereich = await eigener_bereich(db, user)
    zeitraeume = monate(anzahl_monate)
    monat_von, monat_bis = zeitraeume[-1][1], zeitraeume[-1][2]
    vormonat_von = _monat_plus(monat_von, -1)

    verlauf = []
    for schluessel, von, bis in zeitraeume:
        k = await _kosten.kosten(db, bereich, seit=von, bis=bis)
        verlauf.append({"monat": schluessel, "aufgaben_usd": round(k.aufgaben, 4), "chat_usd": round(k.chat, 4),
                        "gesamt_usd": round(k.gesamt, 4)})

    diesen = await _kosten.kosten(db, bereich, seit=monat_von, bis=monat_bis)
    vorher = await _kosten.kosten(db, bereich, seit=vormonat_von, bis=monat_von)

    # Agenten: Aufgaben und Chat getrennt, dazu Tokens des Monats.
    nur_aufgaben = await _kosten.kosten(db, bereich, seit=monat_von, bis=monat_bis, quellen=(_kosten.QUELLE_AUFGABEN,))
    agent_ids = set(diesen.je_agent) | set(vorher.je_agent)
    # Agenten mit Tokens, aber ohne Betrag: Für ihr Modell ist kein Preis hinterlegt
    # (Codex mit gpt-6). Ohne diese Ergänzung fehlten sie in der Liste ganz — ihr
    # Verbrauch war unsichtbar statt als „Preis unbekannt" markiert.
    tok = await _kosten.tokens(db, agent_ids=sorted(set(bereich.agent_ids or ()) | agent_ids),
                               seit=monat_von, bis=monat_bis)
    agent_ids = sorted(agent_ids | {aid for aid, (ein, aus) in tok.items() if ein or aus})
    agenten = {a.id: a for a in (await db.execute(select(Agent).where(Agent.id.in_(agent_ids)))).scalars()} \
        if agent_ids else {}
    je_agent = []
    for aid in agent_ids:
        a = agenten.get(aid)
        if a is None:
            continue
        gesamt = diesen.je_agent.get(aid, 0.0)
        auf = nur_aufgaben.je_agent.get(aid, 0.0)
        je_agent.append({
            "id": aid, "name": a.name, "monat_usd": round(gesamt, 4), "aufgaben_usd": round(auf, 4),
            "chat_usd": round(gesamt - auf, 4), "vormonat_usd": round(vorher.je_agent.get(aid, 0.0), 4),
            "budget_usd": a.budget_usd, "tokens_ein": tok.get(aid, (0, 0))[0], "tokens_aus": tok.get(aid, (0, 0))[1],
            "preis_unbekannt": not gesamt and any(tok.get(aid, (0, 0))),
        })
    je_agent.sort(key=lambda z: -z["monat_usd"])

    # Projekte des Nutzers.
    projekte = await sichtbare_projekte(db, user)
    ids = [t.id for t in projekte]
    p_monat = await _kosten.je_projekt(db, ids, seit=monat_von, bis=monat_bis)
    p_vor = await _kosten.je_projekt(db, ids, seit=vormonat_von, bis=monat_von)
    p_gesamt = await _kosten.je_projekt(db, ids)
    p_tok = await _kosten.tokens(db, team_ids=ids, seit=monat_von, bis=monat_bis, je="projekt")
    je_projekt = []
    for t in projekte:
        monat = p_monat.get(t.id, 0.0)
        je_projekt.append({
            "id": t.id, "name": t.name, "farbe": t.farbe, "monat_usd": round(monat, 4),
            "vormonat_usd": round(p_vor.get(t.id, 0.0), 4), "gesamt_usd": round(p_gesamt.get(t.id, 0.0), 4),
            "budget_usd": t.budget_usd,
            "anteil_budget": round(monat / t.budget_usd, 4) if t.budget_usd else None,
            "tokens_ein": p_tok.get(t.id, (0, 0))[0], "tokens_aus": p_tok.get(t.id, (0, 0))[1],
            "darf_budget": _darf_budget(t, user),
        })
    je_projekt.sort(key=lambda z: -z["monat_usd"])

    budget = None
    if not is_agent_principal(user):
        konto = await db.get(User, str(getattr(user, "id", "")))
        if konto is not None and konto.budget_usd:
            eigen = await _kosten.kosten(db, _kosten.Bereich.besitzer(konto.id), seit=monat_von)
            budget = {"budget_usd": konto.budget_usd, "verbraucht_usd": round(eigen.gesamt, 4)}

    return {
        "monat": zeitraeume[-1][0],
        "summe": {
            "monat_usd": round(diesen.gesamt, 4), "aufgaben_usd": round(diesen.aufgaben, 4),
            "chat_usd": round(diesen.chat, 4), "vormonat_usd": round(vorher.gesamt, 4),
            "geloescht_usd": round(diesen.geloescht, 4),
            "ohne_projekt_aufgaben_usd": round(await _ohne_projekt(db, bereich, monat_von, monat_bis), 4),
            "andere_projekte_aufgaben_usd": round(await _ohne_projekt(db, bereich, monat_von, monat_bis, ausser=ids), 4),
        },
        "budget": budget,
        "verlauf": verlauf,
        "je_projekt": je_projekt,
        "je_agent": je_agent,
    }


def _darf_budget(team, user) -> bool:
    from app.api.teams import _ist_ersteller

    return _ist_ersteller(team, user)


def als_csv(daten: dict) -> str:
    """Die Übersicht als CSV (Semikolon, Beträge in USD mit Punkt). Namen schreiben
    Nutzer und Agenten — deshalb gegen Formel-Einschleusung entschärft."""
    from app.api.audit import _csv_zelle as _zelle

    puffer = io.StringIO()
    w = csv.writer(puffer, delimiter=";")
    w.writerow(["Art", "Name", "Monat", "Aufgaben (USD)", "Chat (USD)", "Gesamt (USD)", "Budget (USD)",
                "Tokens ein", "Tokens aus"])
    for m in daten["verlauf"]:
        w.writerow(["Monat", "", m["monat"], m["aufgaben_usd"], m["chat_usd"], m["gesamt_usd"], "", "", ""])
    for p in daten["je_projekt"]:
        w.writerow(["Projekt", _zelle(p["name"]), daten["monat"], p["monat_usd"], "", p["monat_usd"],
                    p["budget_usd"] or "", p["tokens_ein"], p["tokens_aus"]])
    for a in daten["je_agent"]:
        w.writerow(["Agent", _zelle(a["name"]), daten["monat"], a["aufgaben_usd"], a["chat_usd"], a["monat_usd"],
                    a["budget_usd"] or "", a["tokens_ein"], a["tokens_aus"]])
    return puffer.getvalue()

