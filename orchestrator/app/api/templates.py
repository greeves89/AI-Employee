"""API endpoints for agent templates."""

import logging
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agent_manager import PERMISSION_PACKAGES, AgentManager
from app.core.agent_templates import vorlagen_sortierschluessel
from app.core.log_redaction import scrub_log
from app.core.vorlagen_skills import SKILL_ANZEIGE
from app.db.session import get_db
from app.dependencies import get_docker_service, get_redis_service, require_auth
from app.models.agent_template import AgentTemplate
from app.schemas.agent import BUDGET_VORGABE, BudgetExceededAction
from app.services.docker_service import DockerService
from app.services.redis_service import RedisService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/templates", tags=["templates"])


class TemplateCreate(BaseModel):
    name: str
    display_name: str
    description: str = ""
    icon: str = "Bot"
    category: str = "general"
    model: str = "claude-sonnet-4-6"
    role: str = ""
    permissions: list[str] = []
    integrations: list[str] = []
    mcp_server_ids: list[int] = []
    skill_ids: list[int] = []
    knowledge_template: str = ""
    responsibilities: list[dict] = []
    claude_md: str = ""


class TemplateUpdate(BaseModel):
    display_name: str | None = None
    description: str | None = None
    icon: str | None = None
    category: str | None = None
    model: str | None = None
    role: str | None = None
    permissions: list[str] | None = None
    integrations: list[str] | None = None
    mcp_server_ids: list[int] | None = None
    skill_ids: list[int] | None = None
    knowledge_template: str | None = None
    responsibilities: list[dict] | None = None
    claude_md: str | None = None


class CreateFromTemplate(BaseModel):
    name: str | None = None  # Override agent name
    budget_usd: float | None = None  # Monthly budget cap (None = unlimited)
    budget_exceeded_action: BudgetExceededAction = BUDGET_VORGABE
    # Schlaegt die Vorlage vollen Root-Zugriff vor, gilt er nur mit diesem Haken (#910).
    root_bestaetigt: bool = False


def _template_to_dict(t: AgentTemplate) -> dict:
    return {
        "id": t.id,
        "name": t.name,
        "display_name": t.display_name,
        "description": t.description,
        "icon": t.icon,
        "category": t.category,
        "model": t.model,
        "role": t.role,
        "permissions": t.permissions or [],
        "integrations": t.integrations or [],
        "mcp_server_ids": t.mcp_server_ids or [],
        "skill_ids": t.skill_ids or [],
        "knowledge_template": t.knowledge_template,
        "responsibilities": list(getattr(t, "responsibilities", None) or []),
        "claude_md": t.claude_md or "",
        "build_tools": bool(t.build_tools),
        "is_builtin": t.is_builtin,
        "is_published": t.is_published,
        "published_at": t.published_at.isoformat() if t.published_at else None,
        "created_by": t.created_by,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


async def _skills_der_vorlagen(db: AsyncSession, vorlagen) -> dict[int, dict]:
    """Name + Beschreibung der Skills aller übergebenen Vorlagen, in EINER Abfrage —
    damit die Oberfläche zeigt, was eine Vorlage mitbringt (nur IDs sagen niemandem etwas)."""
    from app.models.skill import Skill

    ids = {i for v in vorlagen for i in (v.skill_ids or [])}
    if not ids:
        return {}
    # Nur der Anfang des Inhalts — dort steht die Überschrift, aus der der Titel kommt.
    rows = (await db.execute(
        select(Skill.id, Skill.name, Skill.description, func.substr(Skill.content, 1, 600)).where(Skill.id.in_(ids))
    )).all()
    # Fremde Skills sind englisch beschrieben — daneben die deutsche Anzeige aus
    # EINER Stelle (core/vorlagen_skills.SKILL_ANZEIGE, #902). ``description``
    # bleibt, wie die Quelle ihn liefert; die Oberfläche zeigt ``beschreibung``,
    # wenn es sie gibt.
    return {
        sid: {
            "id": sid,
            "name": name,
            "titel": SKILL_ANZEIGE[name][0] if name in SKILL_ANZEIGE else _skill_titel(name, anfang),
            "description": (beschr or "")[:300],
            "beschreibung": SKILL_ANZEIGE[name][1] if name in SKILL_ANZEIGE else None,
        }
        for sid, name, beschr, anfang in rows
    }


# Datei-Skills heißen wie ihr Format; für Fachanwender steht da, was sie damit können.
_FORMAT_TITEL = {
    "docx": "Word-Dokumente",
    "xlsx": "Excel-Tabellen",
    "pptx": "PowerPoint-Folien",
    "pdf": "PDF-Dokumente",
}


def _skill_titel(name: str, inhalt_anfang: str | None) -> str:
    """Lesbarer Skill-Titel für die Vorlagen-Auswahl (#903): „Belege vorkontieren“
    statt ``buchhaltung-vorkontieren``. Quelle ist die erste Überschrift der
    Anleitung; ohne Überschrift bleibt der Name."""
    if name in _FORMAT_TITEL:
        return _FORMAT_TITEL[name]
    text = re.sub(r"^---.*?---\s*", "", inhalt_anfang or "", count=1, flags=re.DOTALL)
    for zeile in text.splitlines():
        if zeile.startswith("# "):
            titel = zeile[2:].strip()
            return titel[:60] if titel else name
    return name


def _rechte_anzeige(permissions: list[str] | None) -> list[str]:
    """Deutsche Namen der Rechte-Pakete (#903) — „Paketinstallation“ statt
    ``package-install``. Die Schlüssel bleiben in ``permissions`` für die Anlage."""
    return [PERMISSION_PACKAGES.get(p, {}).get("label", p) for p in (permissions or [])]


def _mit_skills(t: AgentTemplate, skills: dict[int, dict]) -> dict:
    d = _template_to_dict(t)
    d["skills"] = [skills[i] for i in (t.skill_ids or []) if i in skills]
    d["permissions_anzeige"] = _rechte_anzeige(t.permissions)
    return d


@router.get("")
async def list_templates(
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    List templates.
    - Admins see ALL templates (published + unpublished drafts).
    - Regular users see only published templates.
    """
    from app.models.user import UserRole

    query = select(AgentTemplate)

    # Non-admins only see published templates
    if user.role not in (UserRole.ADMIN, UserRole.MANAGER):
        query = query.where(AgentTemplate.is_published == True)  # noqa: E712

    result = await db.execute(query)
    # Fachbereiche zuerst, Technik danach (#903) — eine Reihenfolge für Web und iOS.
    templates = sorted(
        result.scalars().all(),
        key=lambda t: (*vorlagen_sortierschluessel(t.category, t.display_name), t.id),
    )
    skills = await _skills_der_vorlagen(db, templates)
    return {"templates": [_mit_skills(t, skills) for t in templates]}


@router.get("/{template_id}")
async def get_template(
    template_id: int,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Get a single template by ID. Non-admins can only see published templates."""
    from app.models.user import UserRole

    template = await db.scalar(
        select(AgentTemplate).where(AgentTemplate.id == template_id)
    )
    if not template:
        raise HTTPException(status_code=404, detail="Vorlage nicht gefunden")

    if user.role not in (UserRole.ADMIN, UserRole.MANAGER) and not template.is_published:
        raise HTTPException(status_code=404, detail="Vorlage nicht gefunden")

    return _mit_skills(template, await _skills_der_vorlagen(db, [template]))


@router.post("", status_code=201)
async def create_template(
    body: TemplateCreate,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Create a custom template (admin/manager only — drafts are not visible to users yet)."""
    from app.models.user import UserRole
    if user.role not in (UserRole.ADMIN, UserRole.MANAGER):
        raise HTTPException(status_code=403, detail="Nur Administratoren und die Leitung können Vorlagen anlegen.")

    existing = await db.scalar(
        select(AgentTemplate).where(AgentTemplate.name == body.name)
    )
    if existing:
        raise HTTPException(status_code=409, detail=f"Eine Vorlage „{body.name}“ gibt es bereits.")

    template = AgentTemplate(
        name=body.name,
        display_name=body.display_name,
        description=body.description,
        icon=body.icon,
        category=body.category,
        model=body.model,
        role=body.role,
        permissions=body.permissions,
        integrations=body.integrations,
        mcp_server_ids=body.mcp_server_ids,
        skill_ids=body.skill_ids,
        knowledge_template=body.knowledge_template,
        responsibilities=body.responsibilities or [],
        claude_md=body.claude_md,
        is_builtin=False,
        is_published=False,
        created_by=user.id if user.id != "__anonymous__" else None,
    )
    db.add(template)
    await db.commit()
    await db.refresh(template)
    return _template_to_dict(template)


@router.patch("/{template_id}")
async def update_template(
    template_id: int,
    body: TemplateUpdate,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Update a template (builtin templates: admin only; custom templates: owner or admin)."""
    from app.models.user import UserRole

    template = await db.scalar(
        select(AgentTemplate).where(AgentTemplate.id == template_id)
    )
    if not template:
        raise HTTPException(status_code=404, detail="Vorlage nicht gefunden")

    if template.is_builtin and user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Mitgelieferte Vorlagen können nur Administratoren ändern.")
    if not template.is_builtin and template.created_by != user.id and user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Das ist nicht deine Vorlage.")

    for field in body.model_fields_set:
        setattr(template, field, getattr(body, field))

    await db.commit()
    return _template_to_dict(template)


@router.post("/{template_id}/publish")
async def publish_template(
    template_id: int,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Publish a template so users can see and start agents from it (admin only)."""
    from app.models.user import UserRole
    if user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Nur Administratoren können Vorlagen veröffentlichen.")

    template = await db.scalar(
        select(AgentTemplate).where(AgentTemplate.id == template_id)
    )
    if not template:
        raise HTTPException(status_code=404, detail="Vorlage nicht gefunden")

    template.is_published = True
    template.published_at = datetime.now(timezone.utc)
    await db.commit()
    logger.info(f"Template {scrub_log(template_id)} published by {scrub_log(user.id)}")
    return _template_to_dict(template)


@router.post("/{template_id}/unpublish")
async def unpublish_template(
    template_id: int,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Unpublish a template — hides it from users (admin only)."""
    from app.models.user import UserRole
    if user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Nur Administratoren können Vorlagen zurückziehen.")

    template = await db.scalar(
        select(AgentTemplate).where(AgentTemplate.id == template_id)
    )
    if not template:
        raise HTTPException(status_code=404, detail="Vorlage nicht gefunden")

    template.is_published = False
    template.published_at = None
    await db.commit()
    logger.info(f"Template {scrub_log(template_id)} unpublished by {scrub_log(user.id)}")
    return _template_to_dict(template)


@router.delete("/{template_id}")
async def delete_template(
    template_id: int,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Delete a template (builtin templates cannot be deleted)."""
    from app.models.user import UserRole

    template = await db.scalar(
        select(AgentTemplate).where(AgentTemplate.id == template_id)
    )
    if not template:
        raise HTTPException(status_code=404, detail="Vorlage nicht gefunden")

    if template.is_builtin:
        raise HTTPException(status_code=400, detail="Mitgelieferte Vorlagen lassen sich nicht löschen.")
    if template.created_by != user.id and user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Das ist nicht deine Vorlage.")

    await db.delete(template)
    await db.commit()
    return {"deleted": True}


async def vorlage_fuer_nutzer(template_id: int, user, db: AsyncSession) -> AgentTemplate:
    """Die Vorlage, wenn DIESER Nutzer daraus einen Agenten bauen darf — sonst 404/403.

    Eine Pruefung fuer beide Wege, auf denen ein Agent aus einer Vorlage entsteht:
    ``/templates/{id}/create-agent`` und ``POST /agents`` mit ``template_id`` (so
    legt das Anlegen-Fenster Agenten mit KI-Konto, Codex oder Custom-LLM an). Die
    Vorlage bestimmt, womit der Container gebaut wird — etwa Build-Werkzeuge —,
    also darf sie niemand ueber den zweiten Weg an seiner Rolle vorbei nutzen.
    """
    from app.models.user import UserRole

    template = await db.scalar(select(AgentTemplate).where(AgentTemplate.id == template_id))
    if not template:
        raise HTTPException(status_code=404, detail="Vorlage nicht gefunden")
    # Users can only start from published templates
    if user.role not in (UserRole.ADMIN, UserRole.MANAGER) and not template.is_published:
        raise HTTPException(status_code=403, detail="Diese Vorlage ist noch nicht veröffentlicht.")
    if user.id != "__anonymous__":
        from app.core.permissions import can_use_template, get_effective_permissions

        perms = await get_effective_permissions(user, db)
        if not can_use_template(perms, template.id):
            raise HTTPException(
                status_code=403,
                detail=f"Die Vorlage „{template.display_name or template.name}“ ist für deine Rolle nicht freigegeben.",
            )
    return template


async def vorlage_anwenden(agent, template: AgentTemplate, db: AsyncSession, docker) -> None:
    """Was eine Vorlage einem frisch angelegten Agenten mitgibt: Anleitung, Wissen,
    Daueraufgaben, Skills. Fuer beide Anlege-Wege dieselbe Stelle — bisher bekam ein
    Agent, der mit KI-Konto aus einer Vorlage entstand, nur deren Rolle.
    """
    if template.claude_md and agent.container_id:
        try:
            docker.write_file_in_container(
                agent.container_id, "/workspace/CLAUDE.md", template.claude_md
            )
        except Exception as e:
            logger.warning(f"Failed to write template CLAUDE.md: {e}")

    if template.knowledge_template and agent.container_id:
        try:
            docker.write_file_in_container(
                agent.container_id, "/workspace/knowledge.md", template.knowledge_template
            )
            agent.config = {
                **agent.config,
                "onboarding_complete": True,
                "knowledge_template": template.knowledge_template,
            }
            await db.commit()
        except Exception as e:
            logger.warning(f"Failed to write knowledge template: {e}")

    # Daueraufgaben der Vorlage uebernehmen — sonst startet jeder neue Agent ohne
    # Auftrag und muss einzeln gebrieft werden (V5). Ueber dieselbe Validierung wie
    # die Handeingabe, damit eine kaputte Vorlage nicht still Muell hinterlegt.
    template_duties = list(getattr(template, "responsibilities", None) or [])
    if template_duties:
        from app.core.responsibilities import validated_responsibilities
        from sqlalchemy.orm.attributes import flag_modified
        try:
            duties = validated_responsibilities(template_duties)
        except Exception as e:  # noqa: BLE001
            logger.warning("Vorlage %s hat unbrauchbare Verantwortungsbereiche: %s", scrub_log(template.name), e)
            duties = []
        if duties:
            cfg = dict(agent.config or {})
            proactive = dict(cfg.get("proactive") or {})
            proactive["responsibilities"] = duties
            cfg["proactive"] = proactive
            cfg["onboarding_complete"] = True   # Auftrag steht: er kann sofort planen
            agent.config = cfg
            flag_modified(agent, "config")
            # Mit Bereichen arbeitet er selbststaendig: System-Zeitplaene anlegen (#913).
            # Ohne Hinweis — das Anlegen-Fenster hat es schon angekuendigt.
            from app.core import eigeninitiative
            await eigeninitiative.abgleichen(db, agent, hinweis=False)
            await db.commit()
            logger.info("Vorlage %s: %d Verantwortungsbereich(e) uebernommen", scrub_log(template.name), len(duties))

    # Auto-assign template skills
    if template.skill_ids:
        from app.models.skill import Skill, AgentSkillAssignment
        for skill_id in template.skill_ids:
            skill = await db.get(Skill, skill_id)
            if skill and skill.status == "active":
                existing = await db.scalar(
                    select(AgentSkillAssignment).where(
                        AgentSkillAssignment.agent_id == agent.id,
                        AgentSkillAssignment.skill_id == skill_id,
                    )
                )
                if not existing:
                    db.add(AgentSkillAssignment(
                        agent_id=agent.id,
                        skill_id=skill_id,
                        assigned_by="template",
                    ))
        await db.commit()


@router.post("/{template_id}/create-agent")
async def create_agent_from_template(
    template_id: int,
    body: CreateFromTemplate,
    request: Request,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    docker: DockerService = Depends(get_docker_service),
    redis: RedisService = Depends(get_redis_service),
):
    """Create an agent from a template. Users can only start from published templates."""
    template = await vorlage_fuer_nutzer(template_id, user, db)

    # Rollen-Grenze fuer die Zahl der Agenten
    if user.id != "__anonymous__":
        from app.core.permissions import get_effective_permissions
        from sqlalchemy import func
        from app.models.agent import Agent as _Agent

        perms = await get_effective_permissions(user, db)
        max_agents = perms.get("max_agents")
        if max_agents is not None:
            count = (await db.execute(
                select(func.count(_Agent.id)).where(_Agent.user_id == user.id)
            )).scalar() or 0
            if count >= max_agents:
                raise HTTPException(
                    status_code=403,
                    detail=f"Agent-Limit erreicht ({max_agents}).",
                )

    agent_name = body.name or template.display_name
    uid = user.id if user.id != "__anonymous__" else None

    manager = AgentManager(db, docker, redis)

    try:
        agent = await manager.create_agent(
            name=agent_name,
            model=template.model,
            role=template.role,
            integrations=template.integrations or [],
            # ``None`` statt ``[]``: eine leere Liste hiess „von Hand gewaehlt"
            # und hing JEDEN Vorlagen-Agenten von seiner Autonomiestufe ab (#910).
            permissions=template.permissions or None,
            root_bestaetigt=body.root_bestaetigt,
            user_id=uid,
            budget_usd=body.budget_usd,
            budget_exceeded_action=body.budget_exceeded_action,
            # Die Herkunft gleich beim Anlegen: davon haengt ab, womit der
            # Container gebaut wird (Build-Werkzeuge der Vorlage).
            template_id=template.id,
        )

        await vorlage_anwenden(agent, template, db, docker)

        metrics = await manager.get_agent_with_metrics(agent.id)
        return {**metrics, "template_id": template.id, "template_name": template.name}
    except HTTPException:
        # z. B. 402 „Lizenz-Limit erreicht" — nicht zu einer 500 mit Rohtext machen.
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
