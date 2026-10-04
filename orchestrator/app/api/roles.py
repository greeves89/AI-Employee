"""Custom Roles API — admin-only CRUD + user-role assignment + permission introspection."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.secrets import _get_agent_manager  # Agenten nach Key-Entzug neu starten
from app.core.permissions import get_effective_permissions
from app.db.session import get_db
from app.dependencies import require_auth
from app.models.custom_role import CustomRole
from app.models.user import User, UserRole


router = APIRouter(prefix="/roles", tags=["roles"])


def _require_admin(user):
    if not (hasattr(user, "role") and user.role == UserRole.ADMIN):
        raise HTTPException(status_code=403, detail="Admin only")


async def _rolle_protokollieren(db: AsyncSession, user, befehl: str, **meta) -> None:
    """Rollen-/Gruppenaenderung vormerken (#908) — committet der Aufrufer."""
    from app.core.audit import protokolliere
    from app.models.audit_log import AuditEventType

    await protokolliere(db, AuditEventType.ROLE_CHANGED, user_id=getattr(user, "id", None),
                        command=befehl, meta={k: v for k, v in meta.items() if v is not None})


@router.get("/")
async def list_roles(user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """List all custom roles. Visible to all authenticated users."""
    from app.core.ownership import is_admin
    ist_admin = is_admin(user)
    rows = (await db.execute(select(CustomRole).order_by(CustomRole.name))).scalars().all()
    return {
        "roles": [
            {
                "id": r.id,
                "name": r.name,
                "description": r.description,
                # Was eine Rolle freigibt (Secrets, Konten, Mounts), sieht nur,
                # wer Rollen verwaltet. Alle anderen bekommen Name und Text.
                "permissions": (r.permissions or {}) if ist_admin else {},
                "is_system": r.is_system,
            }
            for r in rows
        ]
    }


@router.post("/", status_code=201)
async def create_role(body: dict, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    _require_admin(user)
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="name is required")
    if (await db.execute(select(CustomRole).where(CustomRole.name == name))).scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"role '{name}' already exists")
    r = CustomRole(
        name=name,
        description=body.get("description"),
        permissions=body.get("permissions") or {},
        is_system=False,
    )
    db.add(r)
    await _rolle_protokollieren(db, user, f"Gruppe angelegt: {name}", aktion="angelegt", gruppe=name)
    await db.commit()
    await db.refresh(r)
    return {"id": r.id, "name": r.name, "description": r.description, "permissions": r.permissions}


@router.put("/users/{user_id}/assign")
async def assign_user_role(user_id: str, body: dict, user=Depends(require_auth), db: AsyncSession = Depends(get_db),
                           manager=Depends(_get_agent_manager)):
    """Assign a custom role to a user. Body: {"custom_role_id": int | null}"""
    _require_admin(user)
    target = await db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="user not found")
    role_id = body.get("custom_role_id")
    if role_id is not None:
        r = await db.get(CustomRole, role_id)
        if not r:
            raise HTTPException(status_code=422, detail="role not found")
    alte = await db.get(CustomRole, target.custom_role_id) if target.custom_role_id else None
    alte_keys = set(((alte.permissions if alte else None) or {}).get("secret_ids") or [])
    vorher_id = target.custom_role_id
    target.custom_role_id = role_id
    if vorher_id != role_id:
        await _rolle_protokollieren(
            db, user, f"Gruppe zugewiesen: {target.email}", aktion="zugewiesen",
            target_user_id=target.id, target_email=target.email,
            von=vorher_id, nach=role_id,
        )
    await db.commit()
    if alte_keys:
        await _keys_neu_pruefen(db, manager, [target], alte_keys)
    return {"user_id": user_id, "custom_role_id": role_id}


@router.put("/users/{user_id}/budget")
async def set_user_budget(user_id: str, body: dict, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Set the monthly spend cap across all of a user's agents.

    Body: {"budget_usd": float | null}  (null = unlimited)
    """
    _require_admin(user)
    target = await db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="user not found")
    target.budget_usd = body.get("budget_usd")
    await db.commit()
    return {"user_id": user_id, "budget_usd": target.budget_usd}


@router.get("/me/permissions")
async def my_permissions(user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Return the effective permissions for the calling user."""
    perms = await get_effective_permissions(user, db)
    return {"permissions": perms, "custom_role_id": getattr(user, "custom_role_id", None)}


@router.put("/{role_id}")
async def update_role(role_id: int, body: dict, user=Depends(require_auth), db: AsyncSession = Depends(get_db),
                      manager=Depends(_get_agent_manager)):
    _require_admin(user)
    r = await db.get(CustomRole, role_id)
    if not r:
        raise HTTPException(status_code=404, detail="role not found")
    if r.is_system:
        raise HTTPException(status_code=403, detail="system roles cannot be modified")
    if "name" in body:
        r.name = (body["name"] or "").strip() or r.name
    if "description" in body:
        r.description = body["description"]
    entzogen: set[int] = set()
    if "permissions" in body and isinstance(body["permissions"], dict):
        vorher = set((r.permissions or {}).get("secret_ids") or [])
        nachher = set(body["permissions"].get("secret_ids") or [])
        entzogen = vorher - nachher
        r.permissions = body["permissions"]
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(r, "permissions")
    await _rolle_protokollieren(db, user, f"Gruppe geändert: {r.name}", aktion="geändert",
                                gruppe=r.name, felder=sorted(k for k in body if k in ("name", "description", "permissions")))
    await db.commit()
    if entzogen:
        await _key_entzug_fuer_rolle(db, manager, role_id, entzogen)
    return {"id": r.id, "name": r.name, "description": r.description, "permissions": r.permissions}


async def _key_entzug_fuer_rolle(db: AsyncSession, manager, role_id: int, entzogen: set[int]) -> None:
    """Rolle hat Keys verloren: bei ihren Mitgliedern nachziehen."""
    mitglieder = (await db.execute(select(User).where(User.custom_role_id == role_id))).scalars().all()
    await _keys_neu_pruefen(db, manager, list(mitglieder), entzogen)


async def _keys_neu_pruefen(db: AsyncSession, manager, mitglieder: list, entzogen: set[int]) -> None:
    """Keys, die der Rolle entzogen wurden, aus den Agenten ihrer Mitglieder nehmen.

    Nur, wo der Key nicht auf anderem Weg erlaubt bleibt (eigener Key, Freigabe an die
    Person). Die Zuweisung wird geloescht und der Agent neu gestartet — sonst bliebe
    der Key bis zum naechsten Start in einem laufenden Agenten (03.10.2026).
    """
    from sqlalchemy import delete as _delete
    from app.api.secrets import _refresh_agents_for_secret
    from app.core.secret_zugriff import nutzbare_secret_ids
    from app.models.agent import Agent
    from app.models.agent_secret import AgentSecretAssignment

    for mitglied in mitglieder:
        bleibt = await nutzbare_secret_ids(db, mitglied)
        if bleibt is None:   # Admin: darf jeden Key nutzen, nichts entziehen
            continue
        weg = entzogen - bleibt
        if not weg:
            continue
        zeilen = (await db.execute(
            select(AgentSecretAssignment.agent_id, AgentSecretAssignment.secret_id)
            .join(Agent, Agent.id == AgentSecretAssignment.agent_id)
            .where(Agent.user_id == mitglied.id, AgentSecretAssignment.secret_id.in_(weg))
        )).all()
        for agent_id, secret_id in zeilen:
            await db.execute(_delete(AgentSecretAssignment).where(
                AgentSecretAssignment.agent_id == agent_id, AgentSecretAssignment.secret_id == secret_id,
            ))
        await db.commit()
        for secret_id in {z[1] for z in zeilen}:
            await _refresh_agents_for_secret(db, manager, secret_id, [z[0] for z in zeilen if z[1] == secret_id])


@router.delete("/{role_id}", status_code=200)
async def delete_role(role_id: int, user=Depends(require_auth), db: AsyncSession = Depends(get_db),
                      manager=Depends(_get_agent_manager)):
    _require_admin(user)
    r = await db.get(CustomRole, role_id)
    if not r:
        raise HTTPException(status_code=404, detail="role not found")
    if r.is_system:
        raise HTTPException(status_code=403, detail="system roles cannot be deleted")
    # Users with this role get reset (FK ON DELETE SET NULL)
    alte_keys = set((r.permissions or {}).get("secret_ids") or [])
    mitglieder_ids = list((await db.execute(select(User.id).where(User.custom_role_id == role_id))).scalars().all())
    await _rolle_protokollieren(db, user, f"Gruppe gelöscht: {r.name}", aktion="gelöscht",
                                gruppe=r.name, betroffene=len(mitglieder_ids))
    await db.delete(r)
    await db.commit()
    if alte_keys and mitglieder_ids:
        mitglieder = (await db.execute(select(User).where(User.id.in_(mitglieder_ids)))).scalars().all()
        await _keys_neu_pruefen(db, manager, list(mitglieder), alte_keys)
    return {"deleted": role_id}
