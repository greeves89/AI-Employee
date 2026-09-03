"""Eigene Menuepunkte verwalten und ausliefern.

Zwei Seiten derselben Sache:

* Der Administrator pflegt die Eintraege (``GET/POST/PATCH/DELETE /custom-pages/``).
* Jeder Angemeldete bekommt unter ``/custom-pages/mine`` NUR die Seiten, die
  seine Rolle sehen darf — gefiltert mit ``can_access_menu`` gegen denselben
  ``menu_paths``-Eintrag, der auch die uebrigen Menuepunkte steuert.

Das Filtern passiert hier im Server und nicht erst in der Seitenleiste. Sonst
waere die Adresse einer fremden Seite fuer jeden abrufbar, der die Liste
anfragt — das Ausblenden im Menue allein waere reine Kosmetik.
"""

import re
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import can_access_menu, get_effective_permissions
from app.db.session import get_db
from app.dependencies import require_admin, require_auth
from app.models.audit_log import AuditEventType, AuditLog
from app.models.custom_page import GROUP_KEYS, OPEN_MODES, CustomPage

router = APIRouter(prefix="/custom-pages", tags=["custom-pages"])


def _audit(user, event_type: AuditEventType, menu_path: str, meta: dict) -> AuditLog:
    """Ein Protokolleintrag pro Aenderung. ``agent_id`` ist im Modell Pflicht und
    steht bei Verwaltungsschritten auf ``admin`` — so machen es die uebrigen
    Verwaltungs-Endpunkte auch."""
    return AuditLog(
        agent_id="admin",
        user_id=str(user.id),
        event_type=event_type,
        command=menu_path,
        outcome="success",
        meta=meta,
    )

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")

# Slugs, die schon Bedeutung haben oder haben koennten. ``/p/<slug>`` liegt zwar
# in einem eigenen Zweig, aber ein Eintrag "neu" oder "admin" laedt nur dazu ein,
# spaeter aus Versehen zwei Dinge auf dieselbe Adresse zu legen.
_RESERVED_SLUGS = {"new", "neu", "admin", "api", "edit", "settings"}


def _validate_url(url: str) -> str:
    """Nur http/https. Ein ``javascript:``-Wert landete sonst als ``src`` im
    Rahmen und waere fremder Code in unserer Oberflaeche — der Administrator ist
    zwar vertrauenswuerdig, aber ein uebernommener Administrator-Zugang waere es
    nicht, und ein Tippfehler ist es nie."""
    value = (url or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="URL fehlt")
    parsed = urlparse(value)
    if parsed.scheme.lower() not in ("http", "https"):
        raise HTTPException(status_code=400, detail="URL muss mit http:// oder https:// beginnen")
    if not parsed.netloc:
        raise HTTPException(status_code=400, detail="URL hat keinen Host")
    return value


def _validate_slug(slug: str) -> str:
    value = (slug or "").strip().lower()
    if not _SLUG_RE.match(value):
        raise HTTPException(
            status_code=400,
            detail="Kurzname: nur Kleinbuchstaben, Ziffern und Bindestriche (max. 63 Zeichen)",
        )
    if value in _RESERVED_SLUGS:
        raise HTTPException(status_code=400, detail=f"Kurzname '{value}' ist reserviert")
    return value


def _validate_choice(value: str, allowed: tuple[str, ...], label: str) -> str:
    if value not in allowed:
        raise HTTPException(status_code=400, detail=f"{label}: erlaubt sind {', '.join(allowed)}")
    return value


def _serialize(page: CustomPage) -> dict:
    return {
        "id": page.id,
        "slug": page.slug,
        "title": page.title,
        "description": page.description,
        "url": page.url,
        "icon": page.icon,
        "group_key": page.group_key,
        "open_mode": page.open_mode,
        "sort_order": page.sort_order,
        "enabled": page.enabled,
        "allow_media": page.allow_media,
        "menu_path": page.menu_path,
    }


class CustomPageCreate(BaseModel):
    slug: str
    title: str
    url: str
    description: str | None = None
    icon: str = "Globe"
    group_key: str = "collab"
    open_mode: str = "iframe"
    sort_order: int = 0
    enabled: bool = True
    allow_media: bool = False


class CustomPageUpdate(BaseModel):
    slug: str | None = None
    title: str | None = None
    url: str | None = None
    description: str | None = None
    icon: str | None = None
    group_key: str | None = None
    open_mode: str | None = None
    sort_order: int | None = None
    enabled: bool | None = None
    allow_media: bool | None = None


async def _visible_pages(user, db: AsyncSession) -> list[CustomPage]:
    """Aktive Seiten, die diese Rolle sehen darf — in Menue-Reihenfolge."""
    rows = (
        await db.execute(
            select(CustomPage)
            .where(CustomPage.enabled.is_(True))
            .order_by(CustomPage.sort_order, CustomPage.title)
        )
    ).scalars().all()
    permissions = await get_effective_permissions(user, db)
    return [p for p in rows if can_access_menu(permissions, p.menu_path)]


@router.get("/mine")
async def list_my_pages(user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Die Menuepunkte fuer den angemeldeten Nutzer."""
    return {"pages": [_serialize(p) for p in await _visible_pages(user, db)]}


@router.get("/by-slug/{slug}")
async def get_page_by_slug(
    slug: str, user=Depends(require_auth), db: AsyncSession = Depends(get_db)
):
    """Eine einzelne Seite zum Anzeigen. Gleicher Riegel wie bei der Liste:
    wer den Menuepunkt nicht sehen darf, bekommt auch die Adresse nicht — sonst
    waere die Seite ueber die geratene URL trotzdem erreichbar."""
    page = (
        await db.execute(select(CustomPage).where(CustomPage.slug == slug.strip().lower()))
    ).scalar_one_or_none()
    if not page or not page.enabled:
        raise HTTPException(status_code=404, detail="Seite nicht gefunden")
    permissions = await get_effective_permissions(user, db)
    if not can_access_menu(permissions, page.menu_path):
        raise HTTPException(status_code=403, detail="Kein Zugriff auf diese Seite")
    return _serialize(page)


@router.get("/")
async def list_pages(user=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Alle Seiten inklusive der abgeschalteten — nur fuer Administratoren."""
    rows = (
        await db.execute(select(CustomPage).order_by(CustomPage.sort_order, CustomPage.title))
    ).scalars().all()
    return {"pages": [_serialize(p) for p in rows], "groups": list(GROUP_KEYS), "modes": list(OPEN_MODES)}


@router.post("/")
async def create_page(
    body: CustomPageCreate, user=Depends(require_admin), db: AsyncSession = Depends(get_db)
):
    slug = _validate_slug(body.slug)
    title = (body.title or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="Titel fehlt")
    url = _validate_url(body.url)
    group_key = _validate_choice(body.group_key, GROUP_KEYS, "Menuegruppe")
    open_mode = _validate_choice(body.open_mode, OPEN_MODES, "Oeffnen-Art")

    existing = (
        await db.execute(select(CustomPage).where(CustomPage.slug == slug))
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail=f"Kurzname '{slug}' ist bereits vergeben")

    page = CustomPage(
        slug=slug,
        title=title,
        description=(body.description or "").strip() or None,
        url=url,
        icon=(body.icon or "Globe").strip() or "Globe",
        group_key=group_key,
        open_mode=open_mode,
        sort_order=body.sort_order,
        enabled=body.enabled,
        allow_media=body.allow_media,
        created_by=str(user.id),
    )
    db.add(page)
    await db.flush()
    db.add(_audit(
        user, AuditEventType.CUSTOM_PAGE_CREATED, page.menu_path,
        {"url": page.url, "open_mode": page.open_mode},
    ))
    await db.commit()
    await db.refresh(page)
    return _serialize(page)


@router.patch("/{page_id}")
async def update_page(
    page_id: int,
    body: CustomPageUpdate,
    user=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    page = (
        await db.execute(select(CustomPage).where(CustomPage.id == page_id))
    ).scalar_one_or_none()
    if not page:
        raise HTTPException(status_code=404, detail="Seite nicht gefunden")

    if body.slug is not None:
        slug = _validate_slug(body.slug)
        if slug != page.slug:
            clash = (
                await db.execute(select(CustomPage).where(CustomPage.slug == slug))
            ).scalar_one_or_none()
            if clash:
                raise HTTPException(status_code=409, detail=f"Kurzname '{slug}' ist bereits vergeben")
            page.slug = slug
    if body.title is not None:
        title = body.title.strip()
        if not title:
            raise HTTPException(status_code=400, detail="Titel fehlt")
        page.title = title
    if body.url is not None:
        page.url = _validate_url(body.url)
    if body.description is not None:
        page.description = body.description.strip() or None
    if body.icon is not None:
        page.icon = body.icon.strip() or "Globe"
    if body.group_key is not None:
        page.group_key = _validate_choice(body.group_key, GROUP_KEYS, "Menuegruppe")
    if body.open_mode is not None:
        page.open_mode = _validate_choice(body.open_mode, OPEN_MODES, "Oeffnen-Art")
    if body.sort_order is not None:
        page.sort_order = body.sort_order
    if body.enabled is not None:
        page.enabled = body.enabled
    if body.allow_media is not None:
        page.allow_media = body.allow_media

    db.add(_audit(
        user, AuditEventType.CUSTOM_PAGE_UPDATED, page.menu_path,
        {"url": page.url, "enabled": page.enabled},
    ))
    await db.commit()
    await db.refresh(page)
    return _serialize(page)


@router.delete("/{page_id}")
async def delete_page(
    page_id: int, user=Depends(require_admin), db: AsyncSession = Depends(get_db)
):
    page = (
        await db.execute(select(CustomPage).where(CustomPage.id == page_id))
    ).scalar_one_or_none()
    if not page:
        raise HTTPException(status_code=404, detail="Seite nicht gefunden")
    menu_path = page.menu_path
    await db.delete(page)
    db.add(_audit(user, AuditEventType.CUSTOM_PAGE_DELETED, menu_path, {}))
    await db.commit()
    return {"deleted": page_id, "menu_path": menu_path}
