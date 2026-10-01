"""Die Zielkette eines Auftrags — wozu er dient, vom Ausgangsauftrag bis zu ihm selbst.

Arbeiten Agenten lange und arbeitsteilig, entfernt sich die Arbeit Schritt fuer
Schritt vom eigentlichen Zweck: jeder einzelne Schritt ist aus Sicht des Agenten
sinnvoll, das Ergebnis will am Ende niemand (#881). Dagegen hilft, dem Agenten
zu sagen, wozu sein Auftrag dient — nicht nur, was er tun soll.

Die Kette entsteht aus ``Task.parent_task_id`` (seit #880 fuer alle Laufzeiten
gesetzt) und, wenn der Auftrag an ein Team ging, aus dessen Beschreibung. Sie
ist bewusst kurz: Titel, kein Prompt — sie soll den Auftrag einordnen, nicht
verdraengen.

EINE Stelle baut sie; der Vorspann eines delegierten Auftrags, die Rueckfrage
an einen Kollegen (#884) und der Endpunkt ``GET /tasks/{id}/zielkette`` lesen
alle von hier.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import Task

#: Mehr Glieder ordnet niemand mehr ein — und eine Schleife in den Daten darf
#: die Abfrage nicht endlos laufen lassen.
MAX_TIEFE = 6
MAX_TITEL = 140
MAX_ZWECK = 300


def _kurz(text: str | None, grenze: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= grenze else text[: grenze - 1].rstrip() + "…"


async def zielkette(db: AsyncSession, task: Task) -> list[dict]:
    """Vom Ausgangsauftrag bis zu ``task``: ``[{"id", "titel", "agent_id"}, …]``.

    Bricht an einer Luecke (Eltern-Auftrag geloescht), bei einer Schleife und
    nach ``MAX_TIEFE`` Gliedern ab — dann beginnt die Kette eben spaeter.
    """
    kette = [task]
    gesehen = {task.id}
    aktuell = task
    while aktuell.parent_task_id and len(kette) < MAX_TIEFE:
        eltern = await db.get(Task, aktuell.parent_task_id)
        if eltern is None or eltern.id in gesehen:
            break
        kette.append(eltern)
        gesehen.add(eltern.id)
        aktuell = eltern
    kette.reverse()
    return [{"id": t.id, "titel": _kurz(t.title, MAX_TITEL), "agent_id": t.agent_id} for t in kette]


async def team_zweck(db: AsyncSession, task: Task) -> str | None:
    """Wofuer das Team da ist, an das der Ausgangsauftrag ging — falls es eines gab."""
    from app.models.team import Team

    aktuell, gesehen = task, {task.id}
    for _ in range(MAX_TIEFE):
        team_id = (aktuell.metadata_ or {}).get("team_id")
        if team_id:
            team = await db.get(Team, team_id)
            if team is None:
                return None
            zweck = _kurz(team.description, MAX_ZWECK)
            return f"{team.name}: {zweck}" if zweck else team.name
        if not aktuell.parent_task_id:
            return None
        eltern = await db.get(Task, aktuell.parent_task_id)
        if eltern is None or eltern.id in gesehen:
            return None
        gesehen.add(eltern.id)
        aktuell = eltern
    return None


def als_text(kette: list[dict], zweck: str | None = None) -> str:
    """Die Kette als kurzer Abschnitt fuer einen Prompt. Leer, wenn es nichts
    einzuordnen gibt (Auftrag ohne Eltern und ohne Team)."""
    if len(kette) < 2 and not zweck:
        return ""
    zeilen = ["## Wozu dieser Auftrag dient"]
    if zweck:
        zeilen.append(f"Team: {zweck}")
    for i, glied in enumerate(kette):
        marke = "→ Dein Auftrag:" if i == len(kette) - 1 else f"{i + 1}."
        zeilen.append(f"{marke} {glied['titel']}")
    zeilen.append(
        "Prüfe bei größeren Schritten, ob sie noch diesem Ziel dienen. Wenn nicht: "
        "zurück zum Auftrag oder nachfragen, statt weiterzubauen."
    )
    return "\n".join(zeilen) + "\n\n"


async def vorspann(db: AsyncSession, task: Task) -> str:
    """Der Abschnitt fuer ``task`` — oder leer. Nie ein Grund, einen Auftrag
    nicht zu starten: geht etwas schief, laeuft er ohne Einordnung."""
    try:
        return als_text(await zielkette(db, task), await team_zweck(db, task))
    except Exception:  # noqa: BLE001
        return ""
