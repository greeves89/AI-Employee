"""Die EINE Kostenquelle (#896) — Aufgaben und Chat, nutzergetrennt, je Zeitraum.

Bis v1.362 rechnete jede Seite selbst, und jede anders:

* Dashboard: nur Aufgaben, im Browser aus der geladenen Liste summiert;
* Budget-Kachel: alle Aufgaben aller Zeiten, ohne Chat, inklusive gelöschter Agenten;
* Budget-Liste darunter: Aufgaben + Chat des laufenden Monats;
* Budgetprüfung: nur Aufgaben des Monats — Chat lief am Budget vorbei.

Im Markttest stand deshalb „89,26 €" über einer Liste, die zusammen 8,50 € ergab.
Jetzt fragen alle hier: ``kosten(db, bereich, seit, quellen)``.

Abgerechnet wird bei den Anbietern in USD, und so wird auch gespeichert. Alle
Beträge hier sind USD; umgerechnet wird nur für die Anzeige (``betrag_anzeigen``
serverseitig, ``lib/money.ts`` im Browser).

Gelöschte Zeilen nehmen ihre Kosten nicht mit: Vor jedem Löschen von
Chatnachrichten oder Aufgaben verdichtet ``verdichten`` die Beträge in
``kosten_historie`` (Tag, Agent, Besitzer, Quelle — kein Inhalt), und ``kosten``
zählt Historie plus noch vorhandene Zeilen. Verdichtet wird in DERSELBEN
Transaktion wie gelöscht — sonst zählte ein Betrag doppelt oder gar nicht.
Ebenfalls dort landen Chat-Läufe ohne eigene Verlaufszeile (Telegram,
Sprachfront): ``lauf_ohne_verlauf_buchen``.

Nutzertrennung: ``bereich_fuer_nutzer`` liefert für ein Mitglied genau seine
sichtbaren Agenten (``ownership.visible_agent_ids``), für Administratoren die ganze
Anlage. Eine leere Menge heisst „nichts" — nie „alles".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy import func, select

from app.config import settings

QUELLE_AUFGABEN = "aufgaben"
QUELLE_CHAT = "chat"
ALLE_QUELLEN = (QUELLE_AUFGABEN, QUELLE_CHAT)


def monatsbeginn(jetzt: datetime | None = None) -> datetime:
    """Erster Augenblick des laufenden Kalendermonats (UTC) — Grenze aller Budgets."""
    jetzt = jetzt or datetime.now(timezone.utc)
    return jetzt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def vor_tagen(tage: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=tage)


@dataclass(frozen=True)
class Bereich:
    """Wessen Kosten? Genau eine der drei Formen.

    * ``agent_ids``: diese Agenten (auch leer → nichts)
    * ``besitzer_id``: alle Agenten eines Nutzers (für das Nutzerbudget)
    * beides ``None``: die ganze Anlage, inklusive gelöschter Agenten
    """

    agent_ids: frozenset[str] | None = None
    besitzer_id: str | None = None

    @classmethod
    def anlage(cls) -> "Bereich":
        return cls()

    @classmethod
    def agent(cls, agent_id: str) -> "Bereich":
        return cls(agent_ids=frozenset({agent_id}))

    @classmethod
    def agenten(cls, agent_ids: Iterable[str]) -> "Bereich":
        return cls(agent_ids=frozenset(str(a) for a in agent_ids))

    @classmethod
    def besitzer(cls, user_id: str) -> "Bereich":
        return cls(besitzer_id=user_id)

    @property
    def ist_anlage(self) -> bool:
        return self.agent_ids is None and self.besitzer_id is None

    @property
    def ist_leer(self) -> bool:
        return self.agent_ids is not None and not self.agent_ids


async def bereich_fuer_nutzer(user, db) -> Bereich:
    """Was dieser Nutzer sehen darf: Administratoren die Anlage, sonst die eigenen
    und freigegebenen Agenten."""
    from app.core.ownership import visible_agent_ids

    sichtbar = await visible_agent_ids(user, db)
    if sichtbar is None:
        return Bereich.anlage()
    return Bereich.agenten(sichtbar)


@dataclass
class Kosten:
    """Ergebnis einer Abfrage. Alle Beträge in USD.

    ``je_agent`` nennt nur Agenten, die es noch gibt (bzw. die im Bereich stehen);
    was keinem bestehenden Agenten mehr gehört, steht in ``geloescht``. Damit gilt
    immer: Summe der Liste + ``geloescht`` = ``gesamt``.
    """

    aufgaben: float = 0.0
    chat: float = 0.0
    geloescht: float = 0.0
    je_agent: dict[str, float] = field(default_factory=dict)
    je_tag: dict[str, float] = field(default_factory=dict)

    @property
    def gesamt(self) -> float:
        return round(self.aufgaben + self.chat, 6)


def _filter(stmt, spalte_agent, bereich: Bereich):
    from app.models.agent import Agent

    if bereich.agent_ids is not None:
        return stmt.where(spalte_agent.in_(sorted(bereich.agent_ids)))
    if bereich.besitzer_id is not None:
        return stmt.join(Agent, spalte_agent == Agent.id).where(Agent.user_id == bereich.besitzer_id)
    return stmt


def _tag(wert) -> str:
    if isinstance(wert, datetime):
        return wert.date().isoformat()
    return str(wert)[:10]


def _als_datum(wert) -> date:
    if isinstance(wert, datetime):
        return wert.date()
    if isinstance(wert, date):
        return wert
    return date.fromisoformat(str(wert)[:10])


async def _historie(db, quelle: str, bereich: Bereich, seit, bis, je_tag: bool):
    """Verdichtete Kosten gelöschter Zeilen — (agent_id, tag, betrag).

    Tagesgenau: ein Zeitraum, der mitten am Tag beginnt, zählt den ganzen Tag
    (Monatsgrenzen liegen immer auf Tagesgrenzen). Beim Nutzerbudget zählt der
    Besitzer beim Verdichten — auch wenn der Agent inzwischen gelöscht ist.
    """
    from app.models.kosten_historie import KostenHistorie as H

    spalten = [H.agent_id, func.coalesce(func.sum(H.betrag_usd), 0)]
    gruppe = [H.agent_id]
    if je_tag:
        spalten.insert(1, H.tag)
        gruppe.append(H.tag)
    stmt = select(*spalten).where(H.quelle == quelle)
    if seit is not None:
        stmt = stmt.where(H.tag >= _als_datum(seit))
    if bis is not None:
        stmt = stmt.where(H.tag < _als_datum(bis))
    if bereich.agent_ids is not None:
        stmt = stmt.where(H.agent_id.in_(sorted(bereich.agent_ids)))
    elif bereich.besitzer_id is not None:
        stmt = stmt.where(H.user_id == bereich.besitzer_id)
    return (await db.execute(stmt.group_by(*gruppe))).all()


async def verdichten(db, quelle: str, bedingung) -> float:
    """Kosten der Zeilen, die gleich gelöscht werden, in die Historie übernehmen.

    ``bedingung``: dieselbe WHERE-Bedingung wie das folgende Löschen (auf
    ``ChatMessage`` bzw. ``Task``). Committet NICHT — der Aufrufer löscht und
    schreibt in einem Zug fest. Liefert den übernommenen Betrag (USD).
    """
    from app.models.agent import Agent
    from app.models.chat_message import ChatMessage
    from app.models.kosten_historie import KostenHistorie
    from app.models.task import Task

    modell, zeit = (ChatMessage, ChatMessage.timestamp) if quelle == QUELLE_CHAT \
        else (Task, Task.created_at)
    tag = func.date(zeit)
    zeilen = (await db.execute(
        select(modell.agent_id, tag, func.sum(modell.cost_usd))
        .where(bedingung, modell.cost_usd.isnot(None))
        .group_by(modell.agent_id, tag)
    )).all()
    zeilen = [(a, t, float(b or 0)) for a, t, b in zeilen if b]
    if not zeilen:
        return 0.0
    agenten = sorted({str(a) for a, _, _ in zeilen if a})
    besitzer = dict((await db.execute(
        select(Agent.id, Agent.user_id).where(Agent.id.in_(agenten))
    )).all()) if agenten else {}
    summe = 0.0
    for agent_id, t, betrag in zeilen:
        db.add(KostenHistorie(
            tag=_als_datum(t), agent_id=agent_id, user_id=besitzer.get(agent_id),
            quelle=quelle, betrag_usd=betrag,
        ))
        summe += betrag
    await db.flush()
    return summe


async def lauf_ohne_verlauf_buchen(db, agent_id: str, betrag_usd: float) -> bool:
    """Kosten eines Chat-Laufs, zu dem es keine Verlaufszeile gibt, festhalten.

    Telegram und die Sprachfront legen keine Zeile in ``chat_messages`` an; das
    ``done`` ihrer Läufe fand keine Nutzernachricht und wurde samt Kosten
    übergangen — Budget und Dashboard sahen diese Gespräche nie. Sie landen hier
    in ``kosten_historie`` (Tag, Agent, Besitzer, Betrag — kein Inhalt) und
    zählen in ``kosten`` wie jede andere Chat-Antwort. Committet selbst.

    Doppelte Zustellung verhindert der Aufrufer (``main._persist_chat_completion``).
    """
    from app.models.agent import Agent
    from app.models.kosten_historie import KostenHistorie

    from app.services.chat_persistence import betrag_pruefen

    betrag = betrag_pruefen(betrag_usd)
    if not betrag or not agent_id:
        return False
    besitzer = await db.scalar(select(Agent.user_id).where(Agent.id == agent_id))
    db.add(KostenHistorie(
        tag=datetime.now(timezone.utc).date(), agent_id=agent_id, user_id=besitzer,
        quelle=QUELLE_CHAT, betrag_usd=betrag,
    ))
    await db.commit()
    return True


async def kosten(
    db,
    bereich: Bereich,
    seit: datetime | None = None,
    quellen: Iterable[str] = ALLE_QUELLEN,
    *,
    bis: datetime | None = None,
    je_tag: bool = False,
) -> Kosten:
    """Kosten im ``bereich`` ab ``seit`` (``None`` = seit Beginn).

    Aufgaben zählen nach ``created_at``, Chat nach ``timestamp`` — beide Spalten
    sind die, nach denen auch bisher jede einzelne Abfrage gefiltert hat.
    """
    from app.models.chat_message import ChatMessage
    from app.models.task import Task

    erg = Kosten()
    if bereich.ist_leer:
        return erg
    quellen = set(quellen)

    zeilen: list[tuple[str, str | None, object, float]] = []
    if QUELLE_AUFGABEN in quellen:
        spalten = [Task.agent_id, func.coalesce(func.sum(Task.cost_usd), 0)]
        gruppe = [Task.agent_id]
        if je_tag:
            spalten.insert(1, func.date(Task.created_at))
            gruppe.append(func.date(Task.created_at))
        stmt = select(*spalten).where(Task.cost_usd.isnot(None))
        if seit is not None:
            stmt = stmt.where(Task.created_at >= seit)
        if bis is not None:
            stmt = stmt.where(Task.created_at < bis)
        stmt = _filter(stmt, Task.agent_id, bereich).group_by(*gruppe)
        for row in (await db.execute(stmt)).all():
            tag = row[1] if je_tag else None
            zeilen.append((QUELLE_AUFGABEN, row[0], tag, float(row[-1] or 0)))
        for row in await _historie(db, QUELLE_AUFGABEN, bereich, seit, bis, je_tag):
            tag = row[1] if je_tag else None
            zeilen.append((QUELLE_AUFGABEN, row[0], tag, float(row[-1] or 0)))

    if QUELLE_CHAT in quellen:
        spalten = [ChatMessage.agent_id, func.coalesce(func.sum(ChatMessage.cost_usd), 0)]
        gruppe = [ChatMessage.agent_id]
        if je_tag:
            spalten.insert(1, func.date(ChatMessage.timestamp))
            gruppe.append(func.date(ChatMessage.timestamp))
        stmt = select(*spalten).where(ChatMessage.cost_usd.isnot(None))
        if seit is not None:
            stmt = stmt.where(ChatMessage.timestamp >= seit)
        if bis is not None:
            stmt = stmt.where(ChatMessage.timestamp < bis)
        stmt = _filter(stmt, ChatMessage.agent_id, bereich).group_by(*gruppe)
        for row in (await db.execute(stmt)).all():
            tag = row[1] if je_tag else None
            zeilen.append((QUELLE_CHAT, row[0], tag, float(row[-1] or 0)))
        for row in await _historie(db, QUELLE_CHAT, bereich, seit, bis, je_tag):
            tag = row[1] if je_tag else None
            zeilen.append((QUELLE_CHAT, row[0], tag, float(row[-1] or 0)))

    # Welche Agenten gibt es noch? Nur für die ganze Anlage nötig: in den anderen
    # Bereichen stehen ohnehin nur bestehende Agenten.
    bestehend: set[str] | None = None
    if bereich.ist_anlage:
        from app.models.agent import Agent

        bestehend = {str(a) for a in (await db.execute(select(Agent.id))).scalars().all()}

    for quelle, agent_id, tag, betrag in zeilen:
        if not betrag:
            continue
        if quelle == QUELLE_AUFGABEN:
            erg.aufgaben += betrag
        else:
            erg.chat += betrag
        if agent_id is None or (bestehend is not None and str(agent_id) not in bestehend):
            erg.geloescht += betrag
        else:
            erg.je_agent[str(agent_id)] = erg.je_agent.get(str(agent_id), 0.0) + betrag
        if je_tag and tag is not None:
            schluessel = _tag(tag)
            erg.je_tag[schluessel] = erg.je_tag.get(schluessel, 0.0) + betrag
    return erg


async def chat_antworten(db, bereich: Bereich, seit: datetime | None = None) -> int:
    """Wie viele Antworten die Agenten im Chat gegeben haben — für „Erledigt".

    Ein Chat-Auftrag legt keine Aufgabe an. Ohne diese Zahl stand im Dashboard
    „Erledigt 0", obwohl den ganzen Tag gearbeitet wurde.
    """
    from app.models.chat_message import ChatMessage

    if bereich.ist_leer:
        return 0
    stmt = select(func.count(ChatMessage.id)).where(ChatMessage.role == "assistant")
    if seit is not None:
        stmt = stmt.where(ChatMessage.timestamp >= seit)
    stmt = _filter(stmt, ChatMessage.agent_id, bereich)
    return int((await db.execute(stmt)).scalar() or 0)


def betrag_anzeigen(usd: float) -> str:
    """USD-Betrag in der Anzeigewährung — für Texte, die der Server schreibt
    (Hinweise, Benachrichtigungen). Gegenstück zu ``formatMoney`` im Browser."""
    wert = float(usd or 0)
    waehrung = (settings.display_currency or "USD").upper()
    kurs = float(getattr(settings, "usd_eur_rate", 0) or 0)
    if waehrung == "EUR" and kurs > 0:
        wert, zeichen = wert * kurs, "€"
    else:
        zeichen = "$"
    text = f"{wert:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{text} {zeichen}"
