"""Workflow API (#392): manage declarative multi-step agent workflows and their runs.

The execution engine lives in ``services.workflow_engine`` and is driven by the
scheduler tick; these endpoints just create/edit definitions and start/inspect runs.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies import require_auth
from app.models.workflow import Workflow, WorkflowRun

router = APIRouter(prefix="/workflows", tags=["workflows"])

_VALID_TYPES = {"agent_task", "condition", "wait"}


def _validate_definition(defn: dict) -> None:
    """Reject malformed definitions early (start present, ids resolvable, types known)."""
    if not isinstance(defn, dict):
        raise HTTPException(status_code=400, detail="definition must be an object")
    steps = defn.get("steps")
    if not isinstance(steps, dict) or not steps:
        raise HTTPException(status_code=400, detail="definition.steps must be a non-empty object")
    start = defn.get("start")
    if start not in steps:
        raise HTTPException(status_code=400, detail="definition.start must reference an existing step")
    ids = set(steps)
    for sid, step in steps.items():
        if not isinstance(step, dict):
            raise HTTPException(status_code=400, detail=f"step '{sid}' must be an object")
        t = step.get("type")
        if t not in _VALID_TYPES:
            raise HTTPException(status_code=400, detail=f"step '{sid}': unknown type '{t}'")
        # every referenced target must exist (or be null = end)
        refs = []
        if t == "condition":
            refs += [step.get("true"), step.get("false")]
            if not isinstance(step.get("check"), dict):
                raise HTTPException(status_code=400, detail=f"condition '{sid}' needs a check object")
        else:
            refs.append(step.get("next"))
        for r in refs:
            if r is not None and r not in ids:
                raise HTTPException(status_code=400, detail=f"step '{sid}' references unknown step '{r}'")


def _is_admin(user) -> bool:
    from app.models.user import UserRole
    return getattr(user, "role", None) == UserRole.ADMIN


async def _get_owned(workflow_id: str, user, db: AsyncSession) -> Workflow:
    wf = (await db.execute(select(Workflow).where(Workflow.id == workflow_id))).scalar_one_or_none()
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found")
    if not _is_admin(user) and wf.user_id not in (None, str(user.id)):
        raise HTTPException(status_code=403, detail="Access denied")
    return wf


class WorkflowUpsert(BaseModel):
    name: str
    definition: dict
    trigger: dict | None = None
    enabled: bool = True


def _wf_dict(wf: Workflow) -> dict:
    return {
        "id": wf.id, "name": wf.name, "user_id": wf.user_id, "enabled": wf.enabled,
        "definition": wf.definition, "trigger": wf.trigger,
        "created_at": wf.created_at.isoformat() if wf.created_at else None,
        "updated_at": wf.updated_at.isoformat() if wf.updated_at else None,
    }


def _run_dict(r: WorkflowRun) -> dict:
    return {
        "id": r.id, "workflow_id": r.workflow_id, "status": r.status,
        "current_step": r.current_step, "current_task_id": r.current_task_id,
        "steps_done": r.steps_done, "context": r.context, "error": r.error,
        "resume_at": r.resume_at.isoformat() if r.resume_at else None,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "completed_at": r.completed_at.isoformat() if r.completed_at else None,
    }


@router.get("")
async def list_workflows(user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    q = select(Workflow).order_by(Workflow.created_at.desc())
    if not _is_admin(user):
        q = q.where((Workflow.user_id == str(user.id)) | (Workflow.user_id.is_(None)))
    rows = (await db.execute(q)).scalars().all()
    return {"workflows": [_wf_dict(w) for w in rows]}


@router.post("", status_code=201)
async def create_workflow(body: WorkflowUpsert, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    _validate_definition(body.definition)
    wf = Workflow(
        id=f"wf_{uuid.uuid4().hex[:12]}", name=body.name, user_id=str(user.id),
        enabled=body.enabled, definition=body.definition, trigger=body.trigger,
    )
    db.add(wf)
    await db.commit()
    await db.refresh(wf)
    return _wf_dict(wf)


@router.get("/{workflow_id}")
async def get_workflow(workflow_id: str, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    return _wf_dict(await _get_owned(workflow_id, user, db))


@router.put("/{workflow_id}")
async def update_workflow(workflow_id: str, body: WorkflowUpsert, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    _validate_definition(body.definition)
    wf = await _get_owned(workflow_id, user, db)
    wf.name = body.name
    wf.definition = body.definition
    wf.trigger = body.trigger
    wf.enabled = body.enabled
    await db.commit()
    await db.refresh(wf)
    return _wf_dict(wf)


@router.delete("/{workflow_id}")
async def delete_workflow(workflow_id: str, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    wf = await _get_owned(workflow_id, user, db)
    await db.delete(wf)
    await db.commit()
    return {"deleted": workflow_id}


@router.post("/{workflow_id}/run", status_code=201)
async def run_workflow(workflow_id: str, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Start a run. The scheduler tick advances it; poll GET /workflows/runs/{id}."""
    from app.services.workflow_engine import start_run

    wf = await _get_owned(workflow_id, user, db)
    if not (wf.definition or {}).get("start"):
        raise HTTPException(status_code=400, detail="Workflow has no start step")
    run = await start_run(wf, db)
    return _run_dict(run)


@router.get("/{workflow_id}/runs")
async def list_runs(workflow_id: str, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    await _get_owned(workflow_id, user, db)
    rows = (await db.execute(
        select(WorkflowRun).where(WorkflowRun.workflow_id == workflow_id).order_by(WorkflowRun.started_at.desc()).limit(50)
    )).scalars().all()
    return {"runs": [_run_dict(r) for r in rows]}


@router.get("/runs/{run_id}")
async def get_run(run_id: str, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    run = (await db.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))).scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    await _get_owned(run.workflow_id, user, db)   # ownership via parent workflow
    return _run_dict(run)
