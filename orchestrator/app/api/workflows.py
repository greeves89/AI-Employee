"""Workflow API (#392/#394) + organisation: folders and sharing.

Workflows can live in folders ("projects") and be shared with individual users
(viewer|editor), directly or via a shared folder. The execution engine lives in
``services.workflow_engine`` and is driven by the scheduler tick.
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.load_balancer import LoadBalancer
from app.core.task_router import TaskRouter
from app.db.session import get_db
from app.dependencies import get_redis_service, require_auth
from app.models.workflow import Workflow, WorkflowFolder, WorkflowRun, WorkflowShare
from app.services.redis_service import RedisService

router = APIRouter(prefix="/workflows", tags=["workflows"])


def _get_task_router(
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis: RedisService = Depends(get_redis_service),
) -> TaskRouter:
    lb = LoadBalancer(redis)
    docker = getattr(request.app.state, "docker", None)
    return TaskRouter(db, redis, lb, docker_service=docker)

_VALID_TYPES = {"agent_task", "condition", "wait"}
_ROLES = {"viewer", "editor"}

# Portable share format for export/import (#470). Bump the version only on a
# breaking change to the envelope; the importer accepts any version <= current.
WORKFLOW_EXPORT_FORMAT = "ai-employee-workflow"
WORKFLOW_EXPORT_VERSION = 1


def _cron_shape_valid(expr: str) -> bool:
    return len(expr.split()) == 5


def _validate_definition(defn: dict) -> None:
    if not isinstance(defn, dict):
        raise HTTPException(status_code=400, detail="definition must be an object")
    steps = defn.get("steps")
    if not isinstance(steps, dict) or not steps:
        raise HTTPException(status_code=400, detail="definition.steps must be a non-empty object")
    if defn.get("start") not in steps:
        raise HTTPException(status_code=400, detail="definition.start must reference an existing step")
    ids = set(steps)
    for sid, step in steps.items():
        if not isinstance(step, dict):
            raise HTTPException(status_code=400, detail=f"step '{sid}' must be an object")
        t = step.get("type")
        if t not in _VALID_TYPES:
            raise HTTPException(status_code=400, detail=f"step '{sid}': unknown type '{t}'")
        refs = [step.get("true"), step.get("false")] if t == "condition" else [step.get("next")]
        if t == "condition" and not isinstance(step.get("check"), dict):
            raise HTTPException(status_code=400, detail=f"condition '{sid}' needs a check object")
        for r in refs:
            if r is not None and r not in ids:
                raise HTTPException(status_code=400, detail=f"step '{sid}' references unknown step '{r}'")


def _validate_trigger(trigger: dict | None) -> None:
    """Validate workflow trigger config before create/import persists it."""
    if trigger is None:
        return
    if not isinstance(trigger, dict):
        raise HTTPException(status_code=422, detail="trigger must be an object")
    cron = trigger.get("cron")
    if cron is None:
        return
    if not isinstance(cron, str) or not cron.strip():
        raise HTTPException(status_code=422, detail="trigger.cron must be a non-empty string")
    try:
        from croniter import croniter
        if not croniter.is_valid(cron):
            raise ValueError
    except ImportError:
        if _cron_shape_valid(cron):
            return
        raise HTTPException(status_code=422, detail="trigger.cron must be a valid cron expression")
    except Exception:
        raise HTTPException(status_code=422, detail="trigger.cron must be a valid cron expression")


def _is_admin(user) -> bool:
    from app.models.user import UserRole
    return getattr(user, "role", None) == UserRole.ADMIN


async def _shared_folder_ids(user, db: AsyncSession) -> set[str]:
    rows = (await db.execute(
        select(WorkflowShare.folder_id).where(
            WorkflowShare.user_id == str(user.id), WorkflowShare.folder_id.isnot(None)
        )
    )).all()
    return {r[0] for r in rows}


async def _access_role(wf: Workflow, user, db: AsyncSession) -> str | None:
    """Return 'owner' | 'editor' | 'viewer' | None for this user on a workflow."""
    if _is_admin(user) or wf.user_id in (None, str(user.id)):
        return "owner"
    # direct share
    direct = (await db.execute(
        select(WorkflowShare.role).where(
            WorkflowShare.workflow_id == wf.id, WorkflowShare.user_id == str(user.id)
        )
    )).scalar_one_or_none()
    role = direct
    # folder share
    if wf.folder_id:
        fshare = (await db.execute(
            select(WorkflowShare.role).where(
                WorkflowShare.folder_id == wf.folder_id, WorkflowShare.user_id == str(user.id)
            )
        )).scalar_one_or_none()
        if fshare == "editor" or (fshare and role != "editor"):
            role = fshare
    return role


async def _get_wf(workflow_id: str, user, db: AsyncSession, *, edit: bool = False) -> Workflow:
    wf = (await db.execute(select(Workflow).where(Workflow.id == workflow_id))).scalar_one_or_none()
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found")
    role = await _access_role(wf, user, db)
    if role is None:
        raise HTTPException(status_code=403, detail="Access denied")
    if edit and role not in ("owner", "editor"):
        raise HTTPException(status_code=403, detail="Nur Ansehen — keine Bearbeitungsrechte")
    return wf


def _is_owner(wf: Workflow, user) -> bool:
    return _is_admin(user) or wf.user_id in (None, str(user.id))


async def _get_wf_owned(workflow_id: str, user, db: AsyncSession) -> Workflow:
    """Load a workflow and require the caller to be its owner (or admin)."""
    wf = (await db.execute(select(Workflow).where(Workflow.id == workflow_id))).scalar_one_or_none()
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found")
    if not _is_owner(wf, user):
        raise HTTPException(status_code=403, detail="Nur der Eigentümer darf das")
    return wf


async def _assert_owns_folder(folder_id: str | None, user, db: AsyncSession) -> None:
    """A workflow may only be placed into a folder the caller owns (prevents leaking
    a workflow into someone else's — potentially shared — folder)."""
    if not folder_id:
        return
    f = (await db.execute(select(WorkflowFolder).where(WorkflowFolder.id == folder_id))).scalar_one_or_none()
    if not f or (not _is_admin(user) and f.user_id != str(user.id)):
        raise HTTPException(status_code=403, detail="Ordner gehört dir nicht")


def _wf_dict(wf: Workflow, role: str = "owner") -> dict:
    return {
        "id": wf.id, "name": wf.name, "user_id": wf.user_id, "enabled": wf.enabled,
        "folder_id": wf.folder_id, "role": role,
        "definition": wf.definition, "trigger": wf.trigger,
        "created_at": wf.created_at.isoformat() if wf.created_at else None,
        "updated_at": wf.updated_at.isoformat() if wf.updated_at else None,
    }


def _export_dict(wf: Workflow) -> dict:
    """A self-contained, portable snapshot of a workflow — just the shareable
    content (name + definition + trigger), stripped of owner/folder/share/run
    state so it can be handed to another user and re-imported cleanly."""
    return {
        "format": WORKFLOW_EXPORT_FORMAT,
        "version": WORKFLOW_EXPORT_VERSION,
        "name": wf.name,
        "definition": wf.definition,
        "trigger": wf.trigger,
        "exported_at": datetime.now(timezone.utc).isoformat(),
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


class WorkflowUpsert(BaseModel):
    name: str
    definition: dict
    trigger: dict | None = None
    enabled: bool = True
    folder_id: str | None = None


# ── list / create ────────────────────────────────────────────────────────────

@router.get("")
async def list_workflows(user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    if _is_admin(user):
        rows = (await db.execute(select(Workflow).order_by(Workflow.created_at.desc()))).scalars().all()
        return {"workflows": [_wf_dict(w, "owner") for w in rows]}
    uid = str(user.id)
    folder_ids = await _shared_folder_ids(user, db)
    shared_wf = (await db.execute(
        select(WorkflowShare.workflow_id).where(WorkflowShare.user_id == uid, WorkflowShare.workflow_id.isnot(None))
    )).all()
    shared_ids = {r[0] for r in shared_wf}
    rows = (await db.execute(select(Workflow).order_by(Workflow.created_at.desc()))).scalars().all()
    out = []
    for w in rows:
        if w.user_id in (None, uid):
            out.append(_wf_dict(w, "owner"))
        elif w.id in shared_ids or (w.folder_id and w.folder_id in folder_ids):
            role = await _access_role(w, user, db)
            out.append(_wf_dict(w, role or "viewer"))
    return {"workflows": out}


@router.post("", status_code=201)
async def create_workflow(body: WorkflowUpsert, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    _validate_definition(body.definition)
    _validate_trigger(body.trigger)
    await _assert_owns_folder(body.folder_id, user, db)
    wf = Workflow(
        id=f"wf_{uuid.uuid4().hex[:12]}", name=body.name, user_id=str(user.id),
        enabled=body.enabled, definition=body.definition, trigger=body.trigger, folder_id=body.folder_id,
    )
    db.add(wf)
    await db.commit()
    await db.refresh(wf)
    return _wf_dict(wf, "owner")


# ── import (static path — must precede the /{workflow_id} routes) ────────────

class WorkflowImport(BaseModel):
    definition: dict
    name: str | None = None
    trigger: dict | None = None
    folder_id: str | None = None
    # Envelope fields as produced by /export — optional so a bare definition also imports.
    format: str | None = None
    version: int | None = None


@router.post("/import", status_code=201)
async def import_workflow(body: WorkflowImport, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Create a new workflow owned by the caller from an exported snapshot.

    The import is always created *disabled*: an imported workflow may carry a
    cron trigger, and we must not silently start firing runs on the importer's
    account. The user reviews it and enables it explicitly.
    """
    if body.format is not None and body.format != WORKFLOW_EXPORT_FORMAT:
        raise HTTPException(status_code=400, detail=f"Unbekanntes Format '{body.format}'")
    if body.version is not None and body.version > WORKFLOW_EXPORT_VERSION:
        raise HTTPException(status_code=400, detail=f"Format-Version {body.version} wird nicht unterstützt")
    _validate_definition(body.definition)
    _validate_trigger(body.trigger)
    await _assert_owns_folder(body.folder_id, user, db)
    name = (body.name or "").strip() or "Importierter Workflow"
    wf = Workflow(
        id=f"wf_{uuid.uuid4().hex[:12]}", name=name, user_id=str(user.id),
        enabled=False, definition=body.definition, trigger=body.trigger, folder_id=body.folder_id,
    )
    db.add(wf)
    await db.commit()
    await db.refresh(wf)
    return _wf_dict(wf, "owner")


# ── directory (minimal user list for share pickers) ──────────────────────────

@router.get("/directory")
async def user_directory(user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Minimal id+name+email of users, so an owner can pick who to share with.
    Excludes the caller. No sensitive fields."""
    from app.models.user import User
    rows = (await db.execute(select(User.id, User.name, User.email).order_by(User.name))).all()
    return {"users": [{"id": r[0], "name": r[1], "email": r[2]} for r in rows if r[0] != str(user.id)]}


# ── folders ──────────────────────────────────────────────────────────────────

class FolderCreate(BaseModel):
    name: str


def _folder_dict(f: WorkflowFolder, shared: bool = False) -> dict:
    return {"id": f.id, "name": f.name, "user_id": f.user_id, "shared": shared,
            "created_at": f.created_at.isoformat() if f.created_at else None}


@router.get("/folders")
async def list_folders(user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    uid = str(user.id)
    own = (await db.execute(select(WorkflowFolder).where(WorkflowFolder.user_id == uid).order_by(WorkflowFolder.name))).scalars().all()
    shared_ids = await _shared_folder_ids(user, db)
    shared = []
    if shared_ids:
        shared = (await db.execute(select(WorkflowFolder).where(WorkflowFolder.id.in_(shared_ids)))).scalars().all()
    return {"folders": [_folder_dict(f) for f in own] + [_folder_dict(f, shared=True) for f in shared]}


@router.post("/folders", status_code=201)
async def create_folder(body: FolderCreate, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    f = WorkflowFolder(id=f"wff_{uuid.uuid4().hex[:12]}", name=body.name, user_id=str(user.id))
    db.add(f)
    await db.commit()
    await db.refresh(f)
    return _folder_dict(f)


@router.delete("/folders/{folder_id}")
async def delete_folder(folder_id: str, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    f = (await db.execute(select(WorkflowFolder).where(WorkflowFolder.id == folder_id))).scalar_one_or_none()
    if not f:
        raise HTTPException(status_code=404, detail="Folder not found")
    if not _is_admin(user) and f.user_id != str(user.id):
        raise HTTPException(status_code=403, detail="Access denied")
    # unassign workflows in this folder, drop its shares, then delete
    for w in (await db.execute(select(Workflow).where(Workflow.folder_id == folder_id))).scalars().all():
        w.folder_id = None
    for s in (await db.execute(select(WorkflowShare).where(WorkflowShare.folder_id == folder_id))).scalars().all():
        await db.delete(s)
    await db.delete(f)
    await db.commit()
    return {"deleted": folder_id}


# ── sharing ──────────────────────────────────────────────────────────────────

class ShareCreate(BaseModel):
    user_id: str
    role: str = "viewer"


def _share_dict(s: WorkflowShare, name: str | None = None) -> dict:
    return {"id": s.id, "user_id": s.user_id, "user_name": name, "role": s.role,
            "workflow_id": s.workflow_id, "folder_id": s.folder_id}


@router.delete("/shares/{share_id}")
async def revoke_share(share_id: str, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    s = (await db.execute(select(WorkflowShare).where(WorkflowShare.id == share_id))).scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="Share not found")
    # only the owner of the shared workflow/folder (or admin) may revoke
    if s.workflow_id:
        await _get_wf_owned(s.workflow_id, user, db)
    elif s.folder_id and not _is_admin(user):
        f = (await db.execute(select(WorkflowFolder).where(WorkflowFolder.id == s.folder_id))).scalar_one_or_none()
        if not f or f.user_id != str(user.id):
            raise HTTPException(status_code=403, detail="Access denied")
    await db.delete(s)
    await db.commit()
    return {"deleted": share_id}


@router.post("/folders/{folder_id}/share", status_code=201)
async def share_folder(folder_id: str, body: ShareCreate, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    if body.role not in _ROLES:
        raise HTTPException(status_code=400, detail="role must be viewer or editor")
    f = (await db.execute(select(WorkflowFolder).where(WorkflowFolder.id == folder_id))).scalar_one_or_none()
    if not f or (not _is_admin(user) and f.user_id != str(user.id)):
        raise HTTPException(status_code=404, detail="Folder not found")
    existing = (await db.execute(select(WorkflowShare).where(
        WorkflowShare.folder_id == folder_id, WorkflowShare.user_id == body.user_id))).scalar_one_or_none()
    if existing:
        existing.role = body.role
        s = existing
    else:
        s = WorkflowShare(id=f"wfs_{uuid.uuid4().hex[:12]}", folder_id=folder_id, user_id=body.user_id, role=body.role, granted_by=str(user.id))
        db.add(s)
    await db.commit()
    return _share_dict(s)


# ── run history (static path before /{workflow_id}) ──────────────────────────

@router.get("/runs/{run_id}")
async def get_run(run_id: str, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    run = (await db.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))).scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    await _get_wf(run.workflow_id, user, db)
    return _run_dict(run)


# ── single workflow (parametrised — keep after the static routes above) ──────

@router.get("/{workflow_id}")
async def get_workflow(workflow_id: str, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    wf = await _get_wf(workflow_id, user, db)
    return _wf_dict(wf, await _access_role(wf, user, db) or "viewer")


@router.get("/{workflow_id}/export")
async def export_workflow(workflow_id: str, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Portable snapshot of a workflow for sharing. Any user who can view the
    workflow may export it."""
    wf = await _get_wf(workflow_id, user, db)
    return _export_dict(wf)


@router.put("/{workflow_id}")
async def update_workflow(workflow_id: str, body: WorkflowUpsert, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    _validate_definition(body.definition)
    _validate_trigger(body.trigger)
    wf = await _get_wf(workflow_id, user, db, edit=True)
    # Re-parenting into a folder is owner-only and only into a folder you own — an
    # editor must not move a shared workflow (could leak it via a shared folder).
    if body.folder_id != wf.folder_id:
        if not _is_owner(wf, user):
            raise HTTPException(status_code=403, detail="Nur der Eigentümer kann den Ordner ändern")
        await _assert_owns_folder(body.folder_id, user, db)
        wf.folder_id = body.folder_id
    wf.name = body.name
    wf.definition = body.definition
    wf.trigger = body.trigger
    wf.enabled = body.enabled
    await db.commit()
    await db.refresh(wf)
    return _wf_dict(wf, await _access_role(wf, user, db) or "editor")


@router.delete("/{workflow_id}")
async def delete_workflow(workflow_id: str, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    wf = (await db.execute(select(Workflow).where(Workflow.id == workflow_id))).scalar_one_or_none()
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found")
    if not _is_admin(user) and wf.user_id not in (None, str(user.id)):
        raise HTTPException(status_code=403, detail="Nur der Eigentümer kann löschen")
    for s in (await db.execute(select(WorkflowShare).where(WorkflowShare.workflow_id == workflow_id))).scalars().all():
        await db.delete(s)
    await db.delete(wf)
    await db.commit()
    return {"deleted": workflow_id}


@router.post("/{workflow_id}/run", status_code=201)
async def run_workflow(
    workflow_id: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    task_router: TaskRouter = Depends(_get_task_router),
):
    from app.services.workflow_engine import advance_run, start_run
    wf = await _get_wf(workflow_id, user, db, edit=True)
    if not (wf.definition or {}).get("start"):
        raise HTTPException(status_code=400, detail="Workflow has no start step")
    run = await start_run(wf, db)
    # Advance immediately instead of waiting for the next scheduler tick (up to 30s) —
    # the user clicked "Ausführen" and expects the first step to kick off right away.
    await advance_run(run, wf, db, task_router)
    return _run_dict(run)


@router.get("/{workflow_id}/runs")
async def list_runs(workflow_id: str, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    await _get_wf(workflow_id, user, db)
    rows = (await db.execute(
        select(WorkflowRun).where(WorkflowRun.workflow_id == workflow_id).order_by(WorkflowRun.started_at.desc()).limit(50)
    )).scalars().all()
    return {"runs": [_run_dict(r) for r in rows]}


@router.get("/{workflow_id}/shares")
async def list_shares(workflow_id: str, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    await _get_wf_owned(workflow_id, user, db)
    from app.models.user import User
    rows = (await db.execute(select(WorkflowShare).where(WorkflowShare.workflow_id == workflow_id))).scalars().all()
    names = {}
    if rows:
        urows = (await db.execute(select(User.id, User.name).where(User.id.in_([s.user_id for s in rows])))).all()
        names = {u[0]: u[1] for u in urows}
    return {"shares": [_share_dict(s, names.get(s.user_id)) for s in rows]}


@router.post("/{workflow_id}/share", status_code=201)
async def share_workflow(workflow_id: str, body: ShareCreate, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    if body.role not in _ROLES:
        raise HTTPException(status_code=400, detail="role must be viewer or editor")
    await _get_wf_owned(workflow_id, user, db)   # only the owner may manage shares
    existing = (await db.execute(select(WorkflowShare).where(
        WorkflowShare.workflow_id == workflow_id, WorkflowShare.user_id == body.user_id))).scalar_one_or_none()
    if existing:
        existing.role = body.role
        s = existing
    else:
        s = WorkflowShare(id=f"wfs_{uuid.uuid4().hex[:12]}", workflow_id=workflow_id, user_id=body.user_id, role=body.role, granted_by=str(user.id))
        db.add(s)
    await db.commit()
    return _share_dict(s)
