"""Admin-Verwaltung: welche IdP-Gruppe auf welche Rolle zeigt.

Getrennt von ``roles.py``: dort geht es um die Rollen/CustomRoles selbst, hier nur
um die Zuordnung EXTERNER Gruppennamen (Entra, ADFS, Keycloak, ...) zu einem
bestehenden Rollen-Ziel. Zwei verschiedene Verantwortlichkeiten, zwei Router.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies import require_admin
from app.models.custom_role import CustomRole
from app.models.sso_group_mapping import PROVIDERS, TARGET_KINDS, SsoGroupRoleMapping
from app.models.sso_observed_group import SsoObservedGroup
from app.models.user import UserRole

router = APIRouter(prefix="/sso-group-mappings", tags=["sso-group-mappings"])

_ROLE_VALUES = {r.value for r in UserRole if r not in (UserRole.UNASSIGNED,)}


def _serialize(row: SsoGroupRoleMapping, custom_role_name: str | None = None) -> dict:
    return {
        "id": row.id,
        "provider": row.provider,
        "group_name": row.group_name,
        "target_kind": row.target_kind,
        "target_value": row.target_value,
        "custom_role_name": custom_role_name,
        "priority": row.priority,
    }


async def _validate_target(db: AsyncSession, target_kind: str, target_value: str) -> None:
    if target_kind not in TARGET_KINDS:
        raise HTTPException(status_code=400, detail=f"target_kind: erlaubt sind {', '.join(TARGET_KINDS)}")
    if target_kind == "role":
        if target_value not in _ROLE_VALUES:
            raise HTTPException(status_code=400, detail=f"target_value: erlaubt sind {', '.join(sorted(_ROLE_VALUES))}")
    else:
        try:
            role_id = int(target_value)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="target_value muss bei custom_role eine Zahl sein")
        if not await db.get(CustomRole, role_id):
            raise HTTPException(status_code=400, detail="CustomRole nicht gefunden")


async def _custom_role_names(db: AsyncSession, rows: list[SsoGroupRoleMapping]) -> dict[int, str]:
    ids = {int(r.target_value) for r in rows if r.target_kind == "custom_role" and r.target_value.isdigit()}
    if not ids:
        return {}
    found = (await db.execute(select(CustomRole).where(CustomRole.id.in_(ids)))).scalars().all()
    return {c.id: c.name for c in found}


async def _serialize_with_custom_role_name(db: AsyncSession, row: SsoGroupRoleMapping) -> dict:
    """``_serialize`` plus die Aufloesung des CustomRole-Namens — fuer die
    Einzelantworten von create/update, nicht nur fuer die Liste."""
    if row.target_kind == "custom_role" and row.target_value.isdigit():
        role = await db.get(CustomRole, int(row.target_value))
        return _serialize(row, role.name if role else None)
    return _serialize(row)


class MappingCreate(BaseModel):
    provider: str
    group_name: str
    target_kind: str
    target_value: str
    priority: int = 0


class MappingUpdate(BaseModel):
    target_kind: str | None = None
    target_value: str | None = None
    priority: int | None = None


@router.get("/")
async def list_mappings(
    provider: str | None = Query(None),
    user=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(SsoGroupRoleMapping).order_by(SsoGroupRoleMapping.provider, SsoGroupRoleMapping.group_name)
    if provider:
        stmt = stmt.where(SsoGroupRoleMapping.provider == provider)
    rows = (await db.execute(stmt)).scalars().all()
    names = await _custom_role_names(db, rows)
    return {
        "mappings": [
            _serialize(r, names.get(int(r.target_value)) if r.target_kind == "custom_role" and r.target_value.isdigit() else None)
            for r in rows
        ],
        "providers": list(PROVIDERS),
        "target_kinds": list(TARGET_KINDS),
        "roles": sorted(_ROLE_VALUES),
    }


@router.get("/observed")
async def list_observed_groups(
    provider: str = Query(...),
    user=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Gruppen, die beim Login tatsaechlich gesehen wurden — zum Anklicken statt
    Abtippen. Markiert, welche schon eine Zuordnung haben."""
    if provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"provider: erlaubt sind {', '.join(PROVIDERS)}")
    observed = (await db.execute(
        select(SsoObservedGroup)
        .where(SsoObservedGroup.provider == provider)
        .order_by(SsoObservedGroup.last_seen_at.desc())
    )).scalars().all()
    mapped = (await db.execute(
        select(SsoGroupRoleMapping.group_name).where(SsoGroupRoleMapping.provider == provider)
    )).scalars().all()
    mapped_lower = {m.lower() for m in mapped}
    return {
        "groups": [
            {
                "group_name": row.group_name,
                "first_seen_at": row.first_seen_at.isoformat() if row.first_seen_at else None,
                "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
                "mapped": row.group_name.lower() in mapped_lower,
            }
            for row in observed
        ]
    }


@router.post("/")
async def create_mapping(
    body: MappingCreate, user=Depends(require_admin), db: AsyncSession = Depends(get_db)
):
    if body.provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"provider: erlaubt sind {', '.join(PROVIDERS)}")
    group_name = (body.group_name or "").strip()
    if not group_name:
        raise HTTPException(status_code=400, detail="Gruppenname fehlt")
    await _validate_target(db, body.target_kind, body.target_value)

    existing = (await db.execute(
        select(SsoGroupRoleMapping).where(
            SsoGroupRoleMapping.provider == body.provider,
            SsoGroupRoleMapping.group_name == group_name,
        )
    )).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="Fuer diese Gruppe existiert bereits eine Zuordnung")

    row = SsoGroupRoleMapping(
        provider=body.provider, group_name=group_name,
        target_kind=body.target_kind, target_value=body.target_value,
        priority=body.priority,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return await _serialize_with_custom_role_name(db, row)


@router.patch("/{mapping_id}")
async def update_mapping(
    mapping_id: int, body: MappingUpdate,
    user=Depends(require_admin), db: AsyncSession = Depends(get_db),
):
    row = (await db.execute(
        select(SsoGroupRoleMapping).where(SsoGroupRoleMapping.id == mapping_id)
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Zuordnung nicht gefunden")

    target_kind = body.target_kind if body.target_kind is not None else row.target_kind
    target_value = body.target_value if body.target_value is not None else row.target_value
    if body.target_kind is not None or body.target_value is not None:
        await _validate_target(db, target_kind, target_value)
        row.target_kind = target_kind
        row.target_value = target_value
    if body.priority is not None:
        row.priority = body.priority

    await db.commit()
    await db.refresh(row)
    return await _serialize_with_custom_role_name(db, row)


@router.delete("/{mapping_id}")
async def delete_mapping(
    mapping_id: int, user=Depends(require_admin), db: AsyncSession = Depends(get_db)
):
    row = (await db.execute(
        select(SsoGroupRoleMapping).where(SsoGroupRoleMapping.id == mapping_id)
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Zuordnung nicht gefunden")
    await db.delete(row)
    await db.commit()
    return {"deleted": mapping_id}
