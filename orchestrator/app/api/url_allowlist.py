"""URL Allowlist API — manage URL access templates and per-agent allowlists.

Agents can only access URLs that match their allowlist patterns.
Non-matching URLs trigger an approval request to the user.
If an agent has NO allowlist entries, all URLs are permitted (fail-open).
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies import require_auth
from app.models.url_allowlist import (
    AgentUrlAllowlist,
    UrlAllowlistTemplate,
    UrlAllowlistTemplateEntry,
)

router = APIRouter(prefix="/url-allowlist", tags=["url-allowlist"])

# ── Predefined Templates ────────────────────────────────────────────────────

URL_ALLOWLIST_TEMPLATES: dict[str, dict] = {
    "developer": {
        "name": "Developer",
        "description": "Zugriff auf Entwickler-Ressourcen: GitHub, Stack Overflow, Dokumentation, Package-Registries",
        "urls": [
            ("github.com", "GitHub"),
            ("*.github.com", "GitHub Subdomains"),
            ("raw.githubusercontent.com", "GitHub Raw Content"),
            ("api.github.com", "GitHub API"),
            ("stackoverflow.com", "Stack Overflow"),
            ("*.stackexchange.com", "Stack Exchange Netzwerk"),
            ("developer.mozilla.org", "MDN Web Docs"),
            ("docs.python.org", "Python Dokumentation"),
            ("pypi.org", "Python Package Index"),
            ("www.npmjs.com", "npm Registry"),
            ("registry.npmjs.org", "npm Registry API"),
            ("hub.docker.com", "Docker Hub"),
            ("docs.docker.com", "Docker Dokumentation"),
            ("crates.io", "Rust Crates"),
            ("pkg.go.dev", "Go Packages"),
            ("www.nuget.org", "NuGet"),
            ("learn.microsoft.com", "Microsoft Learn"),
        ],
    },
    "research": {
        "name": "Recherche",
        "description": "Zugriff auf Suchmaschinen, Nachschlagewerke und Nachrichtenquellen",
        "urls": [
            ("html.duckduckgo.com", "DuckDuckGo Suche"),
            ("duckduckgo.com", "DuckDuckGo"),
            ("*.wikipedia.org", "Wikipedia"),
            ("arxiv.org", "arXiv Papers"),
            ("*.arxiv.org", "arXiv Subdomains"),
            ("news.ycombinator.com", "Hacker News"),
            ("reddit.com", "Reddit"),
            ("*.reddit.com", "Reddit Subdomains"),
            ("medium.com", "Medium"),
        ],
    },
    "marketing": {
        "name": "Marketing",
        "description": "Zugriff auf Social Media, Analytics und Marketing-Plattformen",
        "urls": [
            ("api.twitter.com", "Twitter/X API"),
            ("x.com", "X (Twitter)"),
            ("graph.facebook.com", "Facebook Graph API"),
            ("api.instagram.com", "Instagram API"),
            ("api.linkedin.com", "LinkedIn API"),
            ("www.linkedin.com", "LinkedIn"),
            ("analytics.google.com", "Google Analytics"),
            ("www.google-analytics.com", "Google Analytics"),
            ("*.mailchimp.com", "Mailchimp"),
            ("api.sendgrid.com", "SendGrid API"),
            ("api.hubspot.com", "HubSpot API"),
        ],
    },
    "minimal": {
        "name": "Minimal",
        "description": "Nur DuckDuckGo-Suche und Wikipedia — stark eingeschränkt",
        "urls": [
            ("html.duckduckgo.com", "DuckDuckGo Suche"),
            ("duckduckgo.com", "DuckDuckGo"),
            ("*.wikipedia.org", "Wikipedia"),
        ],
    },
}


async def seed_url_allowlist_templates(db: AsyncSession) -> None:
    """Seed builtin URL allowlist templates on startup (skip if already present)."""
    for key, tmpl_data in URL_ALLOWLIST_TEMPLATES.items():
        existing = await db.scalar(
            select(UrlAllowlistTemplate).where(
                UrlAllowlistTemplate.name == tmpl_data["name"]
            )
        )
        if existing is not None:
            continue
        template = UrlAllowlistTemplate(
            name=tmpl_data["name"],
            description=tmpl_data["description"],
            is_builtin=True,
            created_by="system",
        )
        db.add(template)
        await db.flush()
        for i, (pattern, desc) in enumerate(tmpl_data["urls"]):
            db.add(UrlAllowlistTemplateEntry(
                template_id=template.id,
                url_pattern=pattern,
                description=desc,
                sort_order=i,
            ))
    await db.commit()


# ── URL matching helper ──────────────────────────────────────────────────────

def url_matches_pattern(url: str, pattern: str) -> bool:
    """Check if a URL's host matches a pattern.

    Patterns:
      - "github.com" → exact match
      - "*.github.com" → matches any subdomain of github.com
      - "*" → matches everything
    """
    import re
    from urllib.parse import urlparse

    if pattern == "*":
        return True

    try:
        parsed = urlparse(url if "://" in url else f"https://{url}")
        host = (parsed.hostname or "").lower().strip(".")
    except Exception:
        return False

    pattern = pattern.lower().strip(".")

    if pattern.startswith("*."):
        suffix = pattern[2:]
        return host == suffix or host.endswith("." + suffix)

    return host == pattern


# ── Pydantic Schemas ─────────────────────────────────────────────────────────

class CreateTemplate(BaseModel):
    name: str
    description: str = ""


class CreateTemplateEntry(BaseModel):
    url_pattern: str
    description: str = ""
    sort_order: int = 0


class ApplyTemplate(BaseModel):
    template_id: int


class CreateAgentEntry(BaseModel):
    url_pattern: str
    description: str = ""


class CheckUrlRequest(BaseModel):
    url: str


# ── Response helpers ─────────────────────────────────────────────────────────

def _template_to_dict(t: UrlAllowlistTemplate) -> dict:
    return {
        "id": t.id,
        "name": t.name,
        "description": t.description,
        "is_builtin": t.is_builtin,
        "created_by": t.created_by,
        "entries": [
            {
                "id": e.id,
                "url_pattern": e.url_pattern,
                "description": e.description,
                "sort_order": e.sort_order,
            }
            for e in sorted(t.entries, key=lambda x: x.sort_order)
        ],
        "entry_count": len(t.entries),
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


def _agent_entry_to_dict(e: AgentUrlAllowlist) -> dict:
    return {
        "id": e.id,
        "agent_id": e.agent_id,
        "url_pattern": e.url_pattern,
        "description": e.description,
        "is_active": e.is_active,
        "created_by": e.created_by,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    }


# ── Template CRUD ────────────────────────────────────────────────────────────

@router.get("/templates")
async def list_templates(db: AsyncSession = Depends(get_db)):
    """List all URL allowlist templates."""
    result = await db.execute(
        select(UrlAllowlistTemplate).order_by(UrlAllowlistTemplate.name)
    )
    templates = result.scalars().unique().all()
    return {"templates": [_template_to_dict(t) for t in templates]}


@router.post("/templates", status_code=201)
async def create_template(
    body: CreateTemplate,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Create a custom URL allowlist template."""
    existing = await db.scalar(
        select(UrlAllowlistTemplate).where(UrlAllowlistTemplate.name == body.name)
    )
    if existing:
        raise HTTPException(status_code=409, detail=f"Template '{body.name}' already exists")
    template = UrlAllowlistTemplate(
        name=body.name,
        description=body.description,
        is_builtin=False,
        created_by=str(user.id) if user.id != "__anonymous__" else None,
    )
    db.add(template)
    await db.commit()
    await db.refresh(template)
    return _template_to_dict(template)


@router.delete("/templates/{template_id}")
async def delete_template(
    template_id: int,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Delete a custom template (builtin templates cannot be deleted)."""
    template = await db.scalar(
        select(UrlAllowlistTemplate).where(UrlAllowlistTemplate.id == template_id)
    )
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    if template.is_builtin:
        raise HTTPException(status_code=403, detail="Builtin templates cannot be deleted")
    await db.delete(template)
    await db.commit()
    return {"status": "deleted"}


# ── Template Entry CRUD ──────────────────────────────────────────────────────

@router.post("/templates/{template_id}/entries", status_code=201)
async def add_template_entry(
    template_id: int,
    body: CreateTemplateEntry,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Add a URL pattern to a template."""
    template = await db.scalar(
        select(UrlAllowlistTemplate).where(UrlAllowlistTemplate.id == template_id)
    )
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    entry = UrlAllowlistTemplateEntry(
        template_id=template_id,
        url_pattern=body.url_pattern,
        description=body.description,
        sort_order=body.sort_order,
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    return {
        "id": entry.id,
        "url_pattern": entry.url_pattern,
        "description": entry.description,
        "sort_order": entry.sort_order,
    }


@router.delete("/templates/{template_id}/entries/{entry_id}")
async def delete_template_entry(
    template_id: int,
    entry_id: int,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Remove a URL pattern from a template."""
    entry = await db.scalar(
        select(UrlAllowlistTemplateEntry).where(
            UrlAllowlistTemplateEntry.id == entry_id,
            UrlAllowlistTemplateEntry.template_id == template_id,
        )
    )
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    await db.delete(entry)
    await db.commit()
    return {"status": "deleted"}


# ── Per-Agent Allowlist ──────────────────────────────────────────────────────

@router.get("/agent/{agent_id}")
async def get_agent_allowlist(
    agent_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get all URL allowlist entries for an agent. No auth required (agents call this)."""
    result = await db.execute(
        select(AgentUrlAllowlist)
        .where(AgentUrlAllowlist.agent_id == agent_id)
        .order_by(AgentUrlAllowlist.id)
    )
    entries = result.scalars().all()
    return {
        "agent_id": agent_id,
        "entries": [_agent_entry_to_dict(e) for e in entries],
        "is_restricted": len(entries) > 0,
    }


@router.post("/agent/{agent_id}/apply-template")
async def apply_template_to_agent(
    agent_id: str,
    body: ApplyTemplate,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Apply a URL allowlist template to an agent.

    Removes all template-applied entries and replaces them with entries from the new template.
    Manually added entries (created_by not starting with 'template:') are preserved.
    """
    from app.models.audit_log import AuditLog, AuditEventType

    template = await db.scalar(
        select(UrlAllowlistTemplate).where(UrlAllowlistTemplate.id == body.template_id)
    )
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    # Remove previous template-applied entries
    await db.execute(
        delete(AgentUrlAllowlist).where(
            AgentUrlAllowlist.agent_id == agent_id,
            AgentUrlAllowlist.created_by.like("template:%"),
        )
    )

    created_by = f"template:{template.name}"
    entries = []
    for te in sorted(template.entries, key=lambda x: x.sort_order):
        entry = AgentUrlAllowlist(
            agent_id=agent_id,
            url_pattern=te.url_pattern,
            description=te.description,
            is_active=True,
            created_by=created_by,
        )
        db.add(entry)
        entries.append(entry)

    db.add(AuditLog(
        agent_id=agent_id,
        event_type=AuditEventType.URL_ALLOWLIST_APPLIED,
        command=f"template: {template.name}",
        outcome="success",
        user_id=str(user.id),
        meta={"template_id": template.id, "template_name": template.name, "entry_count": len(entries)},
    ))
    await db.commit()

    return {
        "agent_id": agent_id,
        "template": template.name,
        "entries_applied": len(entries),
    }


@router.post("/agent/{agent_id}", status_code=201)
async def add_agent_url(
    agent_id: str,
    body: CreateAgentEntry,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Manually add a URL pattern to an agent's allowlist."""
    entry = AgentUrlAllowlist(
        agent_id=agent_id,
        url_pattern=body.url_pattern,
        description=body.description,
        is_active=True,
        created_by=str(user.id) if user.id != "__anonymous__" else "manual",
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    return _agent_entry_to_dict(entry)


@router.delete("/agent/{agent_id}/{entry_id}")
async def delete_agent_url(
    agent_id: str,
    entry_id: int,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Remove a URL pattern from an agent's allowlist."""
    entry = await db.scalar(
        select(AgentUrlAllowlist).where(
            AgentUrlAllowlist.id == entry_id,
            AgentUrlAllowlist.agent_id == agent_id,
        )
    )
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    await db.delete(entry)
    await db.commit()
    return {"status": "deleted"}


@router.post("/agent/{agent_id}/clear")
async def clear_agent_allowlist(
    agent_id: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Remove all URL allowlist entries for an agent (unrestrict)."""
    await db.execute(
        delete(AgentUrlAllowlist).where(AgentUrlAllowlist.agent_id == agent_id)
    )
    await db.commit()
    return {"status": "cleared", "agent_id": agent_id}


# ── URL Check Endpoint (called by agents) ───────────────────────────────────

@router.post("/check/{agent_id}")
async def check_url(
    agent_id: str,
    body: CheckUrlRequest,
    db: AsyncSession = Depends(get_db),
):
    """Check if a URL is allowed for an agent. No auth required (agents call this).

    Returns {"allowed": true/false, "reason": "..."}.
    If the agent has no allowlist entries, all URLs are allowed (fail-open).
    """
    from app.core.permissions import get_effective_permissions
    from app.models.agent import Agent
    from app.models.user import User

    agent = await db.get(Agent, agent_id)
    if agent and agent.user_id:
        owner = await db.get(User, agent.user_id)
        if owner:
            perms = await get_effective_permissions(owner, db)
            role_patterns = perms.get("url_host_patterns")
            if role_patterns is not None:
                if not role_patterns:
                    return {"allowed": False, "reason": "role_url_denied", "url": body.url}
                role_allowed = any(url_matches_pattern(body.url, pattern) for pattern in role_patterns)
                if not role_allowed:
                    return {
                        "allowed": False,
                        "reason": "role_url_not_allowed",
                        "url": body.url,
                        "allowlist_count": len(role_patterns),
                    }

    result = await db.execute(
        select(AgentUrlAllowlist).where(
            AgentUrlAllowlist.agent_id == agent_id,
            AgentUrlAllowlist.is_active == True,
        )
    )
    entries = result.scalars().all()

    if not entries:
        return {"allowed": True, "reason": "no_allowlist", "url": body.url}

    for entry in entries:
        if url_matches_pattern(body.url, entry.url_pattern):
            return {
                "allowed": True,
                "reason": "pattern_match",
                "matched_pattern": entry.url_pattern,
                "url": body.url,
            }

    return {
        "allowed": False,
        "reason": "not_in_allowlist",
        "url": body.url,
        "allowlist_count": len(entries),
    }
