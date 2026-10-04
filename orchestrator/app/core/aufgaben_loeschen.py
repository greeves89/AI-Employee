"""Was beim Löschen einer Aufgabe mit weg muss — an EINER Stelle.

Zwei Fremdschlüssel auf ``tasks`` löschen nicht von selbst mit:

- ``task_ratings.task_id`` — jede erledigte Aufgabe bekommt vom Reflexions-Richter
  eine Bewertung. Ohne diesen Schritt ließ sich praktisch keine erledigte Aufgabe
  löschen (HTTP 500, ``task_ratings_task_id_fkey``).
- ``tasks.parent_task_id`` — Unteraufgaben sind eigene Arbeit mit eigenem Ergebnis;
  sie bleiben bestehen und verlieren nur den Verweis auf die gelöschte Aufgabe.

Die Müllabfuhr im Scheduler kannte die Bewertungen schon, das Löschen über die
Oberfläche nicht. Beide rufen jetzt diese Funktion.

Und die Kosten bleiben (#896): Sie werden vor dem Löschen in der Kostenhistorie
verdichtet. Vorher sank mit jeder gelöschten Aufgabe das Monatsbudget.
"""

from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import Task
from app.models.task_rating import TaskRating


async def abhaengiges_loesen(db: AsyncSession, task_ids: list[str]) -> None:
    """Vor ``db.delete(task)`` aufrufen; committet nicht."""
    if not task_ids:
        return
    from app.core.kosten import QUELLE_AUFGABEN, verdichten

    await verdichten(db, QUELLE_AUFGABEN, Task.id.in_(task_ids))
    await db.execute(delete(TaskRating).where(TaskRating.task_id.in_(task_ids)))
    await db.execute(
        update(Task).where(Task.parent_task_id.in_(task_ids)).values(parent_task_id=None)
    )
