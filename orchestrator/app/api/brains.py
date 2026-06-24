"""Second Brains API — admin-managed, department-shared knowledge vaults.

A Second Brain is a DB-managed mount-catalog entry. Creating one provisions a
shared Markdown vault under ``/srv/secondbrain/<slug>/`` (mkdir + local ``git
init`` + an ``index.md`` scaffold) and makes its ``label`` available everywhere
the mount machinery already works: the mount-permissions modal (per-user ro/rw),
custom roles (``mount_labels``) and the per-agent mount selector. No .env edit,
no orchestrator restart.

Admins create/edit/delete; any authenticated user may list them (so they can
attach one to an agent). The host path is never returned to the UI.
"""
import logging
import os
import re
import subprocess
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.dependencies import require_auth
from app.models.audit_log import AuditEventType, AuditLog
from app.models.second_brain import SecondBrain
from app.models.user import UserRole

log = logging.getLogger(__name__)
router = APIRouter(prefix="/brains", tags=["brains"])

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_INDEX_TEMPLATE = (
    "# {name} — Second Brain\n\n"
    "Geteilte Wissensablage der Abteilung **{name}** (Wikimedia-artig).\n\n"
    "## Konventionen\n"
    "- Eine `.md` pro Thema, sprechender Dateiname, Schlagworte/Fehlercodes im Klartext.\n"
    "- Verlinkung zwischen Artikeln mit `[[Titel]]`.\n"
    "- Ordner nach Bereich (z.B. `Drucker/`, `Netzwerk/`, `Zugaenge/`).\n\n"
    "## Index\n"
    "- (hier Artikel verlinken)\n"
)


def _require_admin(user) -> None:
    if not (hasattr(user, "role") and user.role == UserRole.ADMIN):
        raise HTTPException(status_code=403, detail="Admin only")


def _slugify(raw: str) -> str:
    s = raw.strip().lower().replace(" ", "_")
    s = re.sub(r"[^a-z0-9_-]", "", s)
    return s.strip("-_")


def _provision_vault(host_path: str, name: str) -> None:
    """Create the vault dir, init a LOCAL git repo (no remote) and seed index.md.

    Best-effort: a missing ``git`` binary or a non-writable base must not break
    brain creation — the mount still works, only file history is unavailable.
    """
    base = os.path.realpath(settings.secondbrain_base)
    real = os.path.realpath(host_path)
    if real != base and not real.startswith(base + os.sep):
        raise HTTPException(status_code=400, detail="resolved path escapes the second-brain base")
    os.makedirs(real, exist_ok=True)
    # The orchestrator runs as root but agent containers run as a non-root user
    # (uid 1000). Make the vault writable by the agent so rw brains work, and the
    # seeded index.md editable. .git stays root-owned (the host auto-commit timer
    # runs as root). World-writable is acceptable for an internal shared vault.
    try:
        os.chmod(real, 0o777)
    except OSError as e:
        log.warning("Could not chmod vault %s: %s", real, e)
    index = os.path.join(real, "index.md")
    if not os.path.exists(index):
        try:
            with open(index, "w", encoding="utf-8") as fh:
                fh.write(_INDEX_TEMPLATE.format(name=name))
            os.chmod(index, 0o666)
        except OSError as e:
            log.warning("Could not write index.md for vault %s: %s", real, e)
    if not os.path.isdir(os.path.join(real, ".git")):
        try:
            env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
            subprocess.run(["git", "init", "-q"], cwd=real, check=True, env=env, timeout=20)
            subprocess.run(["git", "config", "user.email", "secondbrain@ai-employee.local"], cwd=real, check=False, timeout=10)
            subprocess.run(["git", "config", "user.name", "Second Brain"], cwd=real, check=False, timeout=10)
            subprocess.run(["git", "add", "-A"], cwd=real, check=False, timeout=20)
            subprocess.run(["git", "commit", "-q", "-m", "init vault"], cwd=real, check=False, env=env, timeout=20)
        except (OSError, subprocess.SubprocessError) as e:
            log.warning("git init for vault %s failed (history disabled): %s", real, e)


async def _audit(db: AsyncSession, event: AuditEventType, brain: SecondBrain, user_id: str) -> None:
    try:
        db.add(AuditLog(
            agent_id="system",
            event_type=event.value,
            command=brain.label,
            outcome="success",
            user_id=str(user_id),
            meta={"name": brain.name, "slug": brain.slug, "mode": brain.default_mode},
        ))
        await db.commit()
    except Exception:  # auditing must never break the operation
        await db.rollback()


class BrainCreate(BaseModel):
    name: str
    slug: str | None = None
    default_mode: str = "rw"  # "ro" | "rw"
    description: str | None = None


class BrainUpdate(BaseModel):
    name: str | None = None
    default_mode: str | None = None
    description: str | None = None
    is_active: bool | None = None


class BrainResponse(BaseModel):
    id: int
    label: str
    name: str
    slug: str
    container_path: str  # host_path is intentionally NOT exposed
    default_mode: str
    description: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


def _to_response(b: SecondBrain) -> BrainResponse:
    return BrainResponse(
        id=b.id, label=b.label, name=b.name, slug=b.slug,
        container_path=b.container_path, default_mode=b.default_mode,
        description=b.description, is_active=b.is_active,
        created_at=b.created_at, updated_at=b.updated_at,
    )


@router.get("/", response_model=list[BrainResponse])
async def list_brains(
    active_only: bool = False,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """List Second Brains (any authenticated user — needed to attach to agents)."""
    stmt = select(SecondBrain).order_by(SecondBrain.name)
    if active_only:
        stmt = stmt.where(SecondBrain.is_active.is_(True))
    rows = (await db.execute(stmt)).scalars().all()
    return [_to_response(b) for b in rows]


@router.post("/", response_model=BrainResponse, status_code=201)
async def create_brain(
    body: BrainCreate,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Create a Second Brain (admin only): provision the vault + register the mount."""
    _require_admin(user)
    if body.default_mode not in ("ro", "rw"):
        raise HTTPException(status_code=422, detail="default_mode must be 'ro' or 'rw'")
    slug = _slugify(body.slug or body.name)
    if not _SLUG_RE.match(slug):
        raise HTTPException(status_code=422, detail="invalid slug (use a-z, 0-9, _ or -)")

    label = f"brain-{slug}"
    host_path = f"{settings.secondbrain_base.rstrip('/')}/{slug}"
    container_path = f"{settings.secondbrain_container_base.rstrip('/')}/{slug}"

    brain = SecondBrain(
        label=label, name=body.name.strip(), slug=slug,
        host_path=host_path, container_path=container_path,
        default_mode=body.default_mode, description=body.description,
        created_by=str(user.id),
    )
    db.add(brain)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise HTTPException(status_code=409, detail="A brain with this name/slug already exists")
    await db.refresh(brain)

    _provision_vault(host_path, brain.name)
    await _audit(db, AuditEventType.BRAIN_CREATED, brain, user.id)
    return _to_response(brain)


@router.patch("/{brain_id}", response_model=BrainResponse)
async def update_brain(
    brain_id: int,
    body: BrainUpdate,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Update a Second Brain (admin only). Slug/path are immutable once created."""
    _require_admin(user)
    brain = await db.get(SecondBrain, brain_id)
    if not brain:
        raise HTTPException(status_code=404, detail="Brain not found")
    if body.name is not None:
        brain.name = body.name.strip()
    if body.default_mode is not None:
        if body.default_mode not in ("ro", "rw"):
            raise HTTPException(status_code=422, detail="default_mode must be 'ro' or 'rw'")
        brain.default_mode = body.default_mode
    if body.description is not None:
        brain.description = body.description
    if body.is_active is not None:
        brain.is_active = body.is_active
    await db.commit()
    await db.refresh(brain)
    await _audit(db, AuditEventType.BRAIN_UPDATED, brain, user.id)
    return _to_response(brain)


@router.delete("/{brain_id}")
async def delete_brain(
    brain_id: int,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Delete a Second Brain (admin only). The vault folder on disk is KEPT
    (data is never destroyed); only the mount registration is removed."""
    _require_admin(user)
    brain = await db.get(SecondBrain, brain_id)
    if not brain:
        raise HTTPException(status_code=404, detail="Brain not found")
    await _audit(db, AuditEventType.BRAIN_DELETED, brain, user.id)
    await db.delete(brain)
    await db.commit()
    return {"ok": True, "id": brain_id}
