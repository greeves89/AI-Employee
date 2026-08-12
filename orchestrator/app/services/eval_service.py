"""Golden-Tests ausführen und auswerten (#391).

Der Läufer schickt **echte Aufträge** an den Agenten — über dieselbe Warteschlange
wie jede andere Arbeit. Das ist der Punkt: geprüft wird der Agent, wie er wirklich
läuft, samt Systemprompt, Skills, Modell und Werkzeugen. Ein Prüfstand daneben
prüfte einen Agenten, den es so nicht gibt.

Deshalb hängt die Auswertung auch am normalen Abschluss (``handle_task_completion``)
und nicht an einer eigenen Warteschleife: die Antwort trifft dort ohnehin ein.
"""

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import eval_harness
from app.models.eval_set import EvalRun, EvalSet
from app.models.task import Task, TaskStatus

logger = logging.getLogger(__name__)

# Kennzeichen im Auftrag. Daran erkennt der Abschluss, dass die Antwort bewertet
# werden muss — und die Selbstheilung, dass sie sich raushalten soll.
META_RUN = "eval_run_id"
META_ITEM = "eval_item_id"

BASELINE_KEY = "eval_baseline"
GATE_KEY = "eval_gate"


def new_id(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:10]}"


async def start_run(
    db: AsyncSession,
    router,
    eval_set: EvalSet,
    agent_id: str,
    *,
    trigger: str = "manual",
) -> EvalRun:
    """Einen Lauf anlegen und alle Aufgaben als Aufträge abschicken.

    Die Fassung der Sammlung wird **mitgeschrieben**, nicht nachgeschlagen: ändert
    jemand später eine Aufgabe, bleibt dieser Lauf trotzdem deutbar. Ohne das wäre
    ein besserer Wert womöglich nur eine leichtere Aufgabe.
    """
    items = eval_set.items or []
    if not items:
        raise ValueError("Die Sammlung enthält keine Aufgaben")

    run = EvalRun(
        id=new_id("ev"),
        set_id=eval_set.id,
        set_version=eval_set.version,
        agent_id=agent_id,
        status="running",
        total=len(items),
        baseline_score=await baseline_for(db, agent_id, eval_set.id),
        trigger=trigger,
        results=[],
    )
    db.add(run)
    await db.commit()

    tasks: list[Task] = []
    for item in items:
        task = Task(
            id=f"t{uuid.uuid4().hex[:8]}",
            title=f"[Test] {item.get('title') or item.get('id')}"[:200],
            prompt=item["prompt"],
            status=TaskStatus.QUEUED,
            agent_id=agent_id,
            priority=0,  # Tests draengeln sich nicht vor echte Arbeit
            metadata_={
                META_RUN: run.id,
                META_ITEM: item["id"],
                # Eine wiederholte Testaufgabe wuerde den Wert verfaelschen: der
                # Agent bekaeme einen zweiten Anlauf, den es im Betrieb nicht gab.
                "no_self_healing": True,
            },
            started_at=datetime.now(timezone.utc),
        )
        db.add(task)
        tasks.append(task)
    # Erst festschreiben, dann abschicken: waere die Reihenfolge umgekehrt, koennte
    # der Agent antworten, bevor es den Auftrag in der Datenbank gibt — die Antwort
    # fiele dann ins Leere.
    await db.commit()

    import json
    for task in tasks:
        await router.redis.push_task(agent_id, json.dumps({
            "id": task.id, "prompt": task.prompt, "model": task.model, "priority": 0,
        }))

    logger.info(
        "[Eval] Lauf %s gestartet: %s Aufgaben aus %s (v%s) gegen %s",
        run.id, len(items), eval_set.id, eval_set.version, agent_id,
    )
    return run


async def baseline_for(db: AsyncSession, agent_id: str, set_id: str) -> float | None:
    """Die Grundlinie: der beste bisher abgeschlossene Lauf dieser Sammlung.

    Bewusst der beste und nicht der letzte. Sonst könnte man eine Verschlechterung
    festschreiben, indem man zweimal schlecht läuft — beim zweiten Mal wäre der
    schlechte erste Lauf schon die Grundlinie, und nichts fiele mehr auf.
    """
    scores = (await db.execute(
        select(EvalRun.score).where(
            EvalRun.agent_id == agent_id,
            EvalRun.set_id == set_id,
            EvalRun.status == "completed",
            EvalRun.score.isnot(None),
        )
    )).scalars().all()
    return max(scores) if scores else None


async def gather_facts(db: AsyncSession, task: Task) -> dict:
    """Was ist bei diesem Lauf WIRKLICH passiert — nachgemessen, nicht behauptet.

    Der Antworttext des Agenten taugt nicht als Beleg für sein eigenes Handeln.
    Am 2026-08-12 stand beim Kunden eine erfundene Statustabelle im Chat („Mr.
    Develop — läuft"), während kein einziger Auftrag existierte. Eine Textprüfung
    hätte diese Antwort für BESSER gehalten als die ehrliche, denn sie enthielt
    mehr von dem, was man erwartet.

    Hier zählen deshalb nur Spuren im System:

    * ``tools_called`` — aus ``task_steps`` (``event_type == "tool_call"``), also
      dem, was der Läufer tatsächlich ausgeführt hat.
    * ``delegated_tasks`` / ``delegated_completed`` — Aufträge, die dieser Agent
      für andere angelegt hat (``metadata_["created_by_agent"]``), und wie viele
      davon fertig sind.
    """
    from app.models.task_step import TaskStep

    steps = (await db.execute(
        select(TaskStep.event_data).where(
            TaskStep.task_id == task.id,
            TaskStep.event_type == "tool_call",
        )
    )).scalars().all()
    tools = sorted({
        str((d or {}).get("name") or (d or {}).get("tool") or "").strip()
        for d in steps
    } - {""})

    delegated: list[Task] = []
    if task.agent_id and task.started_at:
        rows = (await db.execute(
            select(Task).where(
                Task.agent_id != task.agent_id,
                Task.created_at >= task.started_at,
            )
        )).scalars().all()
        delegated = [
            t for t in rows
            if (t.metadata_ or {}).get("created_by_agent") == task.agent_id
        ]

    return {
        "tools_called": tools,
        "delegated_tasks": len(delegated),
        "delegated_completed": sum(
            1 for t in delegated if t.status == TaskStatus.COMPLETED
        ),
    }


async def record_answer(db: AsyncSession, task: Task) -> None:
    """Die Antwort auf eine Testaufgabe bewerten und im Lauf ablegen.

    Wird vom normalen Abschluss aufgerufen. Fehlgeschlagene Aufgaben zählen als
    **nicht bestanden** — ein Agent, der abstürzt, hat die Aufgabe nicht gelöst,
    und sie stillschweigend zu überspringen würde den Wert schönen.
    """
    meta = task.metadata_ or {}
    run_id, item_id = meta.get(META_RUN), meta.get(META_ITEM)
    if not run_id or not item_id:
        return

    run = (await db.execute(select(EvalRun).where(EvalRun.id == run_id))).scalar_one_or_none()
    if run is None or run.status != "running":
        return
    eval_set = (await db.execute(
        select(EvalSet).where(EvalSet.id == run.set_id)
    )).scalar_one_or_none()
    if eval_set is None:
        return

    item = next((i for i in (eval_set.items or []) if i.get("id") == item_id), None)
    if item is None:
        logger.warning("[Eval] Aufgabe %s gibt es in %s nicht mehr", item_id, run.set_id)
        return

    if task.status == TaskStatus.COMPLETED:
        result = eval_harness.check_item(item, task.result, await gather_facts(db, task))
    else:
        result = {
            "id": item_id,
            "title": item.get("title") or item_id,
            "ok": False,
            "weight": item.get("weight", 1),
            "checks": [{"kind": "task", "value": task.status.value, "ok": False,
                        "error": (task.error or "Auftrag nicht abgeschlossen")[:300]}],
            "answer_excerpt": "",
        }

    results = [r for r in (run.results or []) if r.get("id") != item_id]
    results.append(result)
    run.results = results
    run.passed = sum(1 for r in results if r.get("ok"))
    await db.commit()

    if len(results) >= run.total:
        await finalize_run(db, run)


async def finalize_run(db: AsyncSession, run: EvalRun) -> EvalRun:
    """Den Lauf abschliessen: Wert berechnen, mit der Grundlinie vergleichen."""
    run.score = eval_harness.score_results(run.results or [])
    run.regression = eval_harness.is_regression(run.score, run.baseline_score)
    run.status = "completed"
    run.completed_at = datetime.now(timezone.utc)
    await db.commit()
    logger.info(
        "[Eval] Lauf %s fertig: %.1f Punkte (%s/%s), Grundlinie %s%s",
        run.id, run.score, run.passed, run.total,
        f"{run.baseline_score:.1f}" if run.baseline_score is not None else "keine",
        " — RUECKSCHRITT" if run.regression else "",
    )
    return run


async def gate_for_agent(db: AsyncSession, agent) -> dict:
    """Darf dieser Agent aktualisiert werden? (#391)

    Ausgewertet wird der **letzte abgeschlossene** Lauf, nicht der beste: die Frage
    ist, wie der Agent JETZT dasteht, nicht wie gut er einmal war.
    """
    config = getattr(agent, "config", None) or {}
    settings = config.get(GATE_KEY) or {}
    if settings.get("enabled") is False:
        return {"allowed": True, "reason": "disabled", "message": "Gatter ist aus."}

    run = (await db.execute(
        select(EvalRun)
        .where(EvalRun.agent_id == agent.id, EvalRun.status == "completed")
        .order_by(EvalRun.created_at.desc())
        .limit(1)
    )).scalar_one_or_none()

    try:
        tolerance = float(settings.get("tolerance", eval_harness.DEFAULT_TOLERANCE))
    except (TypeError, ValueError):
        tolerance = eval_harness.DEFAULT_TOLERANCE

    decision = eval_harness.gate_decision(
        score=run.score if run else None,
        baseline=run.baseline_score if run else None,
        tolerance=tolerance,
        require_run=bool(settings.get("require_run")),
    )
    if run:
        decision["run_id"] = run.id
        decision["score"] = run.score
        decision["baseline"] = run.baseline_score
    return decision
