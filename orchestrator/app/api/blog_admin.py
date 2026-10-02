"""Verwaltung des Blogs in der Oberflaeche.

Der Betreiber pflegt die Beitraege mit seinem gewohnten Konto: Liste, Editor,
Pruefung, Vorschau, Veroeffentlichen. Nur fuer Administratoren — wer hier
schreibt, veroeffentlicht im Namen der Seite.

Alles Fachliche liegt in ``core/blog.py`` und ist dasselbe wie im MCP-Dienst
(``blog_mcp.py``): dieselbe Pruefung der Angaben, dieselbe Sperre fuer duenne
Beitraege, dasselbe Protokoll. Hier steht nur die Anmeldung davor.

Ohne ``BLOG_ENABLED`` gibt nur ``/blog/status`` Auskunft (damit die Oberflaeche
erklaeren kann, wie man den Blog einschaltet); alles andere antwortet mit 404.
"""

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import blog
from app.db.session import get_db
from app.dependencies import require_admin
from app.models.blog_post import STATUS_PUBLISHED

router = APIRouter(prefix="/blog", tags=["blog-admin"])


class FaqEintrag(BaseModel):
    frage: str = Field(max_length=200)
    antwort: str = Field(max_length=1200)


class BeitragEingabe(BaseModel):
    """Felder eines Beitrags. Beim Aendern bleibt stehen, was nicht genannt ist."""

    slug: str | None = Field(default=None, max_length=blog.MAX_SLUG)
    title: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=320)
    keyword: str | None = Field(default=None, max_length=120)
    body_markdown: str | None = Field(default=None, max_length=blog.MAX_BODY)
    tags: list[str] | None = Field(default=None, max_length=blog.MAX_TAGS)
    faq: list[FaqEintrag] | None = Field(default=None, max_length=blog.MAX_FAQ)
    author: str | None = Field(default=None, max_length=120)
    cover: str | None = Field(default=None, max_length=120)


def _nur_wenn_aktiv() -> None:
    if not blog.blog_aktiv():
        raise HTTPException(status_code=404, detail="Der Blog ist auf dieser Installation nicht eingeschaltet.")


def _angaben(body: BeitragEingabe) -> dict:
    return body.model_dump(exclude_unset=True, exclude_none=True)


async def _lies_begrenzt(datei: UploadFile) -> bytes:
    """Hoechstens ein Byte mehr lesen als erlaubt — ein zu grosses Bild wird nicht erst ganz geladen."""
    daten = await datei.read(blog.MAX_BILD + 1)
    if len(daten) > blog.MAX_BILD:
        raise HTTPException(status_code=413, detail=f"Das Bild ist größer als {blog.MAX_BILD // 1000} kB.")
    return daten


async def _ausfuehren(aufruf):
    """Eine fachliche Ablehnung wird zur verstaendlichen 422, nicht zum Absturz."""
    try:
        return await aufruf
    except blog.BlogFehler as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


async def _vorhanden(db: AsyncSession, slug: str):
    """Der Beitrag — oder 404, auch bei einer unzulaessigen Adresse."""
    post = await blog.hole_oder_none(db, slug)
    if not post:
        raise HTTPException(status_code=404, detail="Beitrag nicht gefunden.")
    return post


@router.get("/status")
async def blog_status(user=Depends(require_admin)):
    """Ob der Blog laeuft und wo er zu finden ist — fuer die Verwaltungsseite."""
    zustand = blog.blog_zustand()
    aktiv = zustand == "an"
    return {
        "enabled": aktiv,
        # an | aus | ohne_adresse — damit die Oberflaeche sagen kann, was fehlt.
        "state": zustand,
        "base_url": blog.basis_url(),
        "blog_url": blog.absolut("/blog") if aktiv else None,
        # Nur OB der Dienst bereit ist — der Schluessel selbst verlaesst den Server nie.
        "mcp_ready": aktiv and bool(blog.mcp_token()),
        "mcp_url": blog.absolut("/api/v1/mcp/blog") if aktiv else None,
    }


@router.get("/posts")
async def blog_posts(status: str | None = None, user=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    _nur_wenn_aktiv()
    posts = await _ausfuehren(blog.alle(db, status))
    return {"posts": [{**blog.kurz(p), "beschreibung": p.description} for p in posts]}


@router.post("/posts", status_code=201)
async def blog_post_anlegen(body: BeitragEingabe, user=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    _nur_wenn_aktiv()
    angaben = _angaben(body)
    slug = angaben.get("slug") or blog.slug_aus(angaben.get("title") or "")
    if await blog.hole_oder_none(db, slug):
        raise HTTPException(status_code=409, detail=f"Unter „{slug}“ gibt es schon einen Beitrag.")
    post, _ = await _ausfuehren(blog.speichere(db, angaben, wer=str(user.id), ueber=blog.UEBER_OBERFLAECHE))
    return blog.voll(post)


@router.get("/posts/{slug}")
async def blog_post_lesen(slug: str, user=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    _nur_wenn_aktiv()
    return blog.voll(await _vorhanden(db, slug))


@router.put("/posts/{slug}")
async def blog_post_aendern(slug: str, body: BeitragEingabe, user=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    _nur_wenn_aktiv()
    await _vorhanden(db, slug)
    # Die Adresse steht im Pfad und aendert sich nicht.
    angaben = {**_angaben(body), "slug": slug}
    post, _ = await _ausfuehren(blog.speichere(db, angaben, wer=str(user.id), ueber=blog.UEBER_OBERFLAECHE))
    return blog.voll(post)


@router.post("/posts/{slug}/publish")
async def blog_post_veroeffentlichen(slug: str, user=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    _nur_wenn_aktiv()
    await _vorhanden(db, slug)
    post, _ = await _ausfuehren(blog.veroeffentliche(db, slug, wer=str(user.id), ueber=blog.UEBER_OBERFLAECHE))
    return blog.voll(post)


@router.post("/posts/{slug}/unpublish")
async def blog_post_zurueckziehen(slug: str, user=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    _nur_wenn_aktiv()
    await _vorhanden(db, slug)
    post = await _ausfuehren(blog.ziehe_zurueck(db, slug, wer=str(user.id), ueber=blog.UEBER_OBERFLAECHE))
    return blog.voll(post)


@router.delete("/posts/{slug}")
async def blog_post_loeschen(slug: str, user=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    _nur_wenn_aktiv()
    post = await _vorhanden(db, slug)
    war_oeffentlich = post.status == STATUS_PUBLISHED
    await _ausfuehren(blog.loesche(db, slug, wer=str(user.id), ueber=blog.UEBER_OBERFLAECHE))
    return {"deleted": slug, "was_published": war_oeffentlich}


# --- Bilder -------------------------------------------------------------------


@router.get("/images")
async def blog_bilder(user=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    _nur_wenn_aktiv()
    return {"images": [blog.bild_kurz(b) for b in await blog.bilder(db)], "max_kb": blog.MAX_BILD // 1000}


@router.post("/images", status_code=201)
async def blog_bild_hochladen(
    file: UploadFile = File(...),
    name: str = Form("", max_length=120),
    alt: str = Form("", max_length=300),
    user=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    _nur_wenn_aktiv()
    daten = await _lies_begrenzt(file)
    # Ohne eigenen Namen: aus dem Dateinamen ableiten (Endung bleibt, Rest wird vereinfacht).
    quelle = (name or file.filename or "").strip().lower()
    stamm, _, endung = quelle.rpartition(".")
    endung = "jpg" if endung == "jpeg" else endung
    bildname = f"{blog.slug_aus(stamm)}.{endung}" if stamm else quelle
    bild, _ = await _ausfuehren(blog.speichere_bild(
        db, bildname, daten, alt, wer=str(user.id), ueber=blog.UEBER_OBERFLAECHE))
    return blog.bild_kurz(bild)


@router.delete("/images/{name}")
async def blog_bild_loeschen(name: str, user=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    _nur_wenn_aktiv()
    if not await blog.hole_bild(db, name):
        raise HTTPException(status_code=404, detail="Bild nicht gefunden.")
    await _ausfuehren(blog.loesche_bild(db, name, wer=str(user.id), ueber=blog.UEBER_OBERFLAECHE))
    return {"deleted": name}
