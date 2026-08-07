"""Den Tagesplan schreiben — EINE Stelle fuer API und Sprachfront.

Der Sprachfront konnte den Plan bisher nur lesen. Auf „mach die Tagesplanung fuer heute
fertig" antwortete der Agent deshalb „ich richte das ein" — und tat nichts, weil ihm das
Werkzeug fehlte. Ankuendigen ohne Ausfuehren ist schlimmer als ein ehrliches „kann ich
nicht": man wartet auf ein Ergebnis, das nie kommt.

Damit die zwei Wege nicht auseinanderlaufen, liegen die Regeln hier:
  * Ersetzt werden nur ``planned``/``dropped`` — was schon laeuft oder erledigt ist,
    bleibt stehen (sonst loescht der Nachmittagslauf den Vormittag aus der Geschichte).
  * Prioritaet und Herkunft werden auf gueltige Werte gezwungen.
"""

from datetime import date as date_cls, datetime, timezone

import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_plan_item import AgentPlanItem
from app.models.schedule import Schedule

MAX_PLAN_ITEMS = 40
# Kein Block unter einer Viertelstunde. Agenten schaetzen sich notorisch zu kurz
# ("10 Minuten") und stapeln dann sechs Sachen in eine Stunde, die nie hinkommt —
# im Kalender werden daraus Striche, die man nicht lesen kann. Ohne Angabe gilt
# ebenfalls diese Viertelstunde, damit JEDER Block ein sichtbares Ende hat.
MIN_BLOCK_MINUTES = 15
VALID_SOURCES = ("responsibility", "todo", "self", "user")
VALID_PRIORITIES = ("high", "normal", "low")


def _parse_start(raw) -> datetime | None:
    """ISO-Zeit annehmen, auch mit ``Z``; Unsinn wird zu „ohne feste Zeit"."""
    if not raw:
        return None
    if isinstance(raw, datetime):
        return raw
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


async def replace_plan(
    db: AsyncSession, agent_id: str, items: list[dict],
    plan_date: date_cls | None = None,
) -> list[AgentPlanItem]:
    """Plan des Tages ersetzen und die neu geschriebenen Bloecke zurueckgeben."""
    plan_date = plan_date or datetime.now(timezone.utc).date()
    if len(items) > MAX_PLAN_ITEMS:
        raise ValueError(f"Höchstens {MAX_PLAN_ITEMS} Blöcke pro Tag")

    # Alte Bloecke UND ihre Zeitplaene weg — sonst feuert ein gestrichener Block weiter.
    doomed = (await db.execute(
        select(AgentPlanItem).where(
            AgentPlanItem.agent_id == agent_id,
            AgentPlanItem.plan_date == plan_date,
            AgentPlanItem.status.in_(("planned", "dropped")),
        )
    )).scalars().all()
    stale_ids = [d.schedule_id for d in doomed if d.schedule_id]
    if stale_ids:
        await db.execute(delete(Schedule).where(Schedule.id.in_(stale_ids)))
    await db.execute(
        delete(AgentPlanItem).where(
            AgentPlanItem.agent_id == agent_id,
            AgentPlanItem.plan_date == plan_date,
            AgentPlanItem.status.in_(("planned", "dropped")),
        )
    )
    created: list[AgentPlanItem] = []
    for item in items:
        title = str(item.get("title") or "").strip()
        if not title:
            continue                                  # ein Block ohne Titel ist kein Block
        source = str(item.get("source") or "self")
        priority = str(item.get("priority") or "normal")
        try:
            minutes = int(item.get("estimated_minutes") or MIN_BLOCK_MINUTES)
        except (TypeError, ValueError):
            minutes = MIN_BLOCK_MINUTES
        row = AgentPlanItem(
            agent_id=agent_id,
            plan_date=plan_date,
            title=title[:200],
            notes=str(item.get("notes") or "").strip()[:2000],
            planned_start=_parse_start(item.get("planned_start")),
            estimated_minutes=min(max(minutes, MIN_BLOCK_MINUTES), 1440),
            source=source if source in VALID_SOURCES else "self",
            priority=priority if priority in VALID_PRIORITIES else "normal",
            todo_id=item.get("todo_id") if isinstance(item.get("todo_id"), int) else None,
        )
        db.add(row)
        created.append(row)
    await db.flush()

    # Jeder Block mit Uhrzeit bekommt einen EINMAL-Zeitplan. Damit laeuft er ueber
    # genau die Maschinerie, die Zeitplaene seit jeher ausfuehrt — kein zweiter
    # Ausloeser, keine Sonderbehandlung. Ohne Uhrzeit bleibt der Block eine Notiz,
    # die der naechste proaktive Lauf aufgreift.
    for row in created:
        if not row.planned_start:
            continue
        schedule_id = uuid.uuid4().hex[:8]
        db.add(Schedule(
            id=schedule_id,
            name=f"[Plan] {row.title[:60]}",
            prompt=(
                f"Das ist ein Block aus DEINEM eigenen Tagesplan "
                f"({row.planned_start:%H:%M}, ca. {row.estimated_minutes} Min, "
                f"Priorität {row.priority}):\n\n{row.title}\n"
                + (f"\nPräzisierung: {row.notes}\n" if row.notes else "")
                + "\nArbeite ihn JETZT ab — vollständig, nicht nur beschreiben. Ist er "
                "größer als gedacht, mach den ersten sinnvollen Schritt fertig und halte "
                "den Rest in `.agent_state.md` fest. Melde am Ende in zwei Sätzen das "
                "Ergebnis und lege erzeugte Dateien nach /workspace/transfer/."
            ),
            interval_seconds=0,          # Einmal-Lauf: schaltet sich nach dem Feuern ab
            priority=0 if row.priority == "high" else 1,
            agent_id=agent_id,
            enabled=True,
            next_run_at=row.planned_start,
        ))
        row.schedule_id = schedule_id
    await db.flush()
    return created
