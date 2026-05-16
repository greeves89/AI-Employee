"""Custom Roles API — admin-only CRUD + user-role assignment + permission introspection."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import get_effective_permissions
from app.db.session import get_db
from app.dependencies import require_auth
from app.models.custom_role import CustomRole
from app.models.user import User, UserRole


router = APIRouter(prefix="/roles", tags=["roles"])


def _require_admin(user):
    if not (hasattr(user, "role") and user.role == UserRole.ADMIN):
        raise HTTPException(status_code=403, detail="Admin only")


@router.get("/")
async def list_roles(user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """List all custom roles. Visible to all authenticated users."""
    rows = (await db.execute(select(CustomRole).order_by(CustomRole.name))).scalars().all()
    return {
        "roles": [
            {
                "id": r.id,
                "name": r.name,
                "description": r.description,
                "permissions": r.permissions or {},
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
    await db.commit()
    await db.refresh(r)
    return {"id": r.id, "name": r.name, "description": r.description, "permissions": r.permissions}


@router.put("/users/{user_id}/assign")
async def assign_user_role(user_id: str, body: dict, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
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
    target.custom_role_id = role_id
    await db.commit()
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
async def update_role(role_id: int, body: dict, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
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
    if "permissions" in body and isinstance(body["permissions"], dict):
        r.permissions = body["permissions"]
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(r, "permissions")
    await db.commit()
    return {"id": r.id, "name": r.name, "description": r.description, "permissions": r.permissions}


@router.delete("/{role_id}", status_code=200)
async def delete_role(role_id: int, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    _require_admin(user)
    r = await db.get(CustomRole, role_id)
    if not r:
        raise HTTPException(status_code=404, detail="role not found")
    if r.is_system:
        raise HTTPException(status_code=403, detail="system roles cannot be deleted")
    # Users with this role get reset (FK ON DELETE SET NULL)
    await db.delete(r)
    await db.commit()
    return {"deleted": role_id}
