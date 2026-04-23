"""Approval Rules API — user-defined rules that tell agents when to request approval."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies import require_auth
from app.models.approval_rule import ApprovalRule
from app.models.autonomy_preset_rule import AutonomyPresetRule

# ── Default Preset Definitions ────────────────────────────────────────────────
# Seeded once on startup into autonomy_preset_rules table.
# Admins can add/edit/delete via UI afterwards.

AUTONOMY_DEFAULTS: dict[str, list[dict]] = {
    "l1": [
        {
            "name": "Dateien lesen",
            "description": "Du darfst Dateien und Verzeichnisse lesen, durchsuchen und analysieren (read-only).",
            "category": "file_read",
        },
        {
            "name": "Web-Recherche",
            "description": "Du darfst im Web suchen, Webseiten abrufen und öffentliche Informationen recherchieren.",
            "category": "web_search",
        },
        {
            "name": "Analysen und Zusammenfassungen ausgeben",
            "description": "Du darfst Analysen, Berichte und Zusammenfassungen als Text ausgeben — aber NICHT als Datei speichern.",
            "category": "custom",
        },
    ],
    "l2": [
        {
            "name": "Dateien lesen",
            "description": "Du darfst Dateien und Verzeichnisse lesen, durchsuchen und analysieren.",
            "category": "file_read",
        },
        {
            "name": "Web-Recherche",
            "description": "Du darfst im Web suchen, Webseiten abrufen und öffentliche Informationen recherchieren.",
            "category": "web_search",
        },
        {
            "name": "Dateien in /workspace/ schreiben",
            "description": "Du darfst Dateien erstellen und bearbeiten innerhalb von /workspace/ — ohne Rückfrage.",
            "category": "file_write",
        },
        {
            "name": "Entwürfe und Empfehlungen erstellen",
            "description": "Du darfst Entwürfe, Konzepte und Empfehlungen ausarbeiten und als Datei speichern.",
            "category": "file_write",
        },
    ],
    "l3": [
        {
            "name": "Dateien lesen und schreiben",
            "description": "Du darfst Dateien lesen und in /workspace/ schreiben, bearbeiten oder löschen.",
            "category": "file_write",
        },
        {
            "name": "Web-Recherche",
            "description": "Du darfst im Web suchen und öffentliche Informationen abrufen.",
            "category": "web_search",
        },
        {
            "name": "Shell-Befehle ausführen",
            "description": "Du darfst Shell-Befehle (Bash) ausführen, auch solche die den Systemzustand verändern.",
            "category": "shell_exec",
        },
        {
            "name": "Pakete installieren und Konfiguration ändern",
            "description": "Du darfst Software installieren und Systemkonfiguration anpassen.",
            "category": "system_config",
        },
    ],
    "l4": [
        {
            "name": "Alles erlaubt",
            "description": "Du darfst alle Aktionen ohne Einschränkung ausführen — inklusive Dateien, Shell, externe Kommunikation, Bestellungen und Systemänderungen.",
            "category": "custom",
        },
    ],
}

LEVEL_META: dict[str, dict] = {
    "l1": {"label": "L1 — Nur lesen", "description": "Erlaubt: Lesen, Recherche, Analysen ausgeben. Alles andere → Freigabe erforderlich."},
    "l2": {"label": "L2 — Empfehlungen", "description": "Erlaubt: Lesen, Recherche, Dateien in /workspace/ schreiben, Entwürfe erstellen. Shell & Extern → Freigabe."},
    "l3": {"label": "L3 — Ausführen mit Freigabe", "description": "Erlaubt: Lesen, Schreiben, Shell, Pakete installieren. Externe Kommunikation & Käufe → Freigabe."},
    "l4": {"label": "L4 — Vollständig autonom", "description": "Alles erlaubt — keine Freigaben nötig."},
}


async def seed_autonomy_presets(db: AsyncSession) -> None:
    """Seed default preset rules on startup.

    Seeds each level that has no entries yet. If the existing L1 rules look like the
    old blacklist format (contain 'Frage IMMER'), wipe and re-seed all levels so the
    whitelist content replaces stale data from a previous version.
    """
    from sqlalchemy import func as _func

    # Detect old blacklist format and wipe if found
    old_rule = await db.scalar(
        select(AutonomyPresetRule).where(
            AutonomyPresetRule.description.ilike("%Frage IMMER%")
        ).limit(1)
    )
    if old_rule is not None:
        await db.execute(delete(AutonomyPresetRule))
        await db.commit()

    for level, rules in AUTONOMY_DEFAULTS.items():
        existing = await db.scalar(
            select(AutonomyPresetRule).where(AutonomyPresetRule.level == level).limit(1)
        )
        if existing is not None:
            continue
        for i, rule_def in enumerate(rules):
            db.add(AutonomyPresetRule(
                level=level,
                name=rule_def["name"],
                description=rule_def["description"],
                category=rule_def["category"],
                sort_order=i,
            ))
    await db.commit()


async def apply_autonomy_preset(db: AsyncSession, agent_id: str, level: str) -> list[ApprovalRule]:
    """Delete auto-generated rules for this agent and insert rules from the DB preset for the given level."""
    await db.execute(
        delete(ApprovalRule).where(
            ApprovalRule.agent_id == agent_id,
            ApprovalRule.created_by == "system:autonomy",
        )
    )
    preset_rules = list((await db.execute(
        select(AutonomyPresetRule)
        .where(AutonomyPresetRule.level == level)
        .order_by(AutonomyPresetRule.sort_order, AutonomyPresetRule.id)
    )).scalars().all())

    rules = []
    for pr in preset_rules:
        rule = ApprovalRule(
            name=pr.name,
            description=pr.description,
            category=pr.category,
            agent_id=agent_id,
            created_by="system:autonomy",
            is_active=True,
        )
        db.add(rule)
        rules.append(rule)
    await db.commit()
    return rules


router = APIRouter(prefix="/approval-rules", tags=["approval-rules"])


# ── Approval Rule CRUD ────────────────────────────────────────────────────────

class CreateRule(BaseModel):
    name: str
    description: str
    category: str = "custom"
    threshold: float | None = None
    agent_id: str | None = None


class UpdateRule(BaseModel):
    name: str | None = None
    description: str | None = None
    category: str | None = None
    threshold: float | None = None
    is_active: bool | None = None
    agent_id: str | None = None


def _to_response(r: ApprovalRule) -> dict:
    return {
        "id": r.id,
        "name": r.name,
        "description": r.description,
        "category": r.category,
        "threshold": r.threshold,
        "is_active": r.is_active,
        "agent_id": r.agent_id,
        "created_by": r.created_by,
        "is_preset": r.created_by == "system:autonomy",
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


@router.get("/")
async def list_rules(
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    from app.models.user import UserRole
    from sqlalchemy import or_
    stmt = select(ApprovalRule).order_by(ApprovalRule.category, ApprovalRule.id)
    if not (hasattr(user, "role") and user.role == UserRole.ADMIN):
        stmt = stmt.where(
            or_(ApprovalRule.created_by.is_(None), ApprovalRule.created_by == str(user.id))
        )
    result = await db.execute(stmt)
    rules = result.scalars().all()
    return {"rules": [_to_response(r) for r in rules]}


@router.post("/", status_code=201)
async def create_rule(
    body: CreateRule,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    from app.models.audit_log import AuditLog, AuditEventType
    rule = ApprovalRule(
        name=body.name,
        description=body.description,
        category=body.category,
        threshold=body.threshold,
        agent_id=body.agent_id,
        created_by=user.id if user.id != "__anonymous__" else None,
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    db.add(AuditLog(
        agent_id=body.agent_id or "global",
        event_type=AuditEventType.APPROVAL_RULE_CREATED,
        command=f"rule: {body.name}",
        outcome="success",
        user_id=str(user.id),
        meta={"rule_id": rule.id, "category": body.category, "agent_id": body.agent_id},
    ))
    await db.commit()
    return _to_response(rule)


@router.patch("/{rule_id}")
async def update_rule(
    rule_id: int,
    body: UpdateRule,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    from app.models.audit_log import AuditLog, AuditEventType
    from app.models.user import UserRole
    rule = await db.scalar(select(ApprovalRule).where(ApprovalRule.id == rule_id))
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    if not (hasattr(user, "role") and user.role == UserRole.ADMIN):
        if rule.created_by and rule.created_by != str(user.id):
            raise HTTPException(status_code=403, detail="Access denied")
    changes = body.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(rule, field, value)
    await db.commit()
    await db.refresh(rule)
    db.add(AuditLog(
        agent_id=rule.agent_id or "global",
        event_type=AuditEventType.APPROVAL_RULE_UPDATED,
        command=f"rule: {rule.name}",
        outcome="success",
        user_id=str(user.id),
        meta={"rule_id": rule_id, "changes": changes},
    ))
    await db.commit()
    return _to_response(rule)


@router.delete("/{rule_id}")
async def delete_rule(
    rule_id: int,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    from app.models.audit_log import AuditLog, AuditEventType
    from app.models.user import UserRole
    rule = await db.scalar(select(ApprovalRule).where(ApprovalRule.id == rule_id))
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    if not (hasattr(user, "role") and user.role == UserRole.ADMIN):
        if rule.created_by and rule.created_by != str(user.id):
            raise HTTPException(status_code=403, detail="Access denied")
    rule_name = rule.name
    rule_agent = rule.agent_id or "global"
    await db.delete(rule)
    await db.commit()
    db.add(AuditLog(
        agent_id=rule_agent,
        event_type=AuditEventType.APPROVAL_RULE_DELETED,
        command=f"rule: {rule_name}",
        outcome="success",
        user_id=str(user.id),
        meta={"rule_id": rule_id},
    ))
    await db.commit()
    return {"status": "deleted"}


async def get_active_rules_for_agent(db: AsyncSession, agent_id: str) -> list[ApprovalRule]:
    """Return all active rules that apply to a given agent (global rules + agent-specific)."""
    result = await db.execute(
        select(ApprovalRule)
        .where(ApprovalRule.is_active == True)
        .where((ApprovalRule.agent_id.is_(None)) | (ApprovalRule.agent_id == agent_id))
    )
    return list(result.scalars().all())


# ── Autonomy Level Presets ────────────────────────────────────────────────────

def _preset_rule_to_dict(r: AutonomyPresetRule) -> dict:
    return {
        "id": r.id,
        "level": r.level,
        "name": r.name,
        "description": r.description,
        "category": r.category,
        "sort_order": r.sort_order,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


@router.get("/level-presets")
async def get_level_presets(db: AsyncSession = Depends(get_db)):
    """Return all preset rules grouped by level (L1–L4), read from DB."""
    result = await db.execute(
        select(AutonomyPresetRule).order_by(AutonomyPresetRule.level, AutonomyPresetRule.sort_order, AutonomyPresetRule.id)
    )
    all_rules = result.scalars().all()

    grouped: dict[str, list] = {level: [] for level in ("l1", "l2", "l3", "l4")}
    for r in all_rules:
        if r.level in grouped:
            grouped[r.level].append(_preset_rule_to_dict(r))

    return {
        "presets": {
            level: {
                "level": level,
                "label": LEVEL_META[level]["label"],
                "description": LEVEL_META[level]["description"],
                "rules": rules,
                "rule_count": len(rules),
            }
            for level, rules in grouped.items()
        }
    }


class CreatePresetRule(BaseModel):
    name: str
    description: str
    category: str = "custom"
    sort_order: int = 0


class UpdatePresetRule(BaseModel):
    name: str | None = None
    description: str | None = None
    category: str | None = None
    sort_order: int | None = None


@router.post("/level-presets/{level}/rules", status_code=201)
async def add_preset_rule(
    level: str,
    body: CreatePresetRule,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Add a rule to a level preset."""
    from app.models.audit_log import AuditLog, AuditEventType
    if level not in LEVEL_META:
        raise HTTPException(status_code=400, detail=f"Invalid level: {level}. Must be l1-l4.")
    rule = AutonomyPresetRule(
        level=level,
        name=body.name,
        description=body.description,
        category=body.category,
        sort_order=body.sort_order,
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    db.add(AuditLog(
        agent_id="global",
        event_type=AuditEventType.PRESET_RULE_ADDED,
        command=f"preset {level}: + {body.name}",
        outcome="success",
        user_id=str(user.id),
        meta={"level": level, "rule_id": rule.id, "category": body.category},
    ))
    await db.commit()
    return _preset_rule_to_dict(rule)


@router.patch("/level-presets/{level}/rules/{rule_id}")
async def update_preset_rule(
    level: str,
    rule_id: int,
    body: UpdatePresetRule,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    rule = await db.scalar(
        select(AutonomyPresetRule).where(
            AutonomyPresetRule.id == rule_id, AutonomyPresetRule.level == level
        )
    )
    if not rule:
        raise HTTPException(status_code=404, detail="Preset rule not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(rule, field, value)
    await db.commit()
    await db.refresh(rule)
    return _preset_rule_to_dict(rule)


@router.delete("/level-presets/{level}/rules/{rule_id}")
async def delete_preset_rule(
    level: str,
    rule_id: int,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    from app.models.audit_log import AuditLog, AuditEventType
    rule = await db.scalar(
        select(AutonomyPresetRule).where(
            AutonomyPresetRule.id == rule_id, AutonomyPresetRule.level == level
        )
    )
    if not rule:
        raise HTTPException(status_code=404, detail="Preset rule not found")
    rule_name = rule.name
    await db.delete(rule)
    await db.commit()
    db.add(AuditLog(
        agent_id="global",
        event_type=AuditEventType.PRESET_RULE_DELETED,
        command=f"preset {level}: - {rule_name}",
        outcome="success",
        user_id=str(user.id),
        meta={"level": level, "rule_id": rule_id},
    ))
    await db.commit()
    return {"status": "deleted"}


@router.get("/for-agent/{agent_id}")
async def get_rules_for_agent(
    agent_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Public endpoint — agents fetch their own applicable rules without auth required."""
    rules = await get_active_rules_for_agent(db, agent_id)
    return {
        "rules": [
            {
                "name": r.name,
                "description": r.description,
                "category": r.category,
                "threshold": r.threshold,
            }
            for r in rules
        ]
    }
