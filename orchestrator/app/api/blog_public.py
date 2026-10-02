"""Oeffentlicher Blog der Landingpage.

Liefert fertiges HTML aus — Uebersicht, Beitrag, Feed, Sitemap, robots.txt —,
damit Suchmaschinen den Inhalt ohne JavaScript lesen. Die Seiten sind ohne
Anmeldung erreichbar und zeigen ausschliesslich veroeffentlichte Beitraege;
ein Entwurf ist nur ueber seine Vorschau-Adresse zu sehen.

Opt-in wie das Kontaktformular: ohne ``BLOG_ENABLED`` antwortet jede Adresse
hier mit 404, auf Anlagen ohne Landingpage gibt es den Blog nicht. Der
Reverse-Proxy muss ``/blog*``, ``/sitemap.xml`` und ``/robots.txt`` an den
Orchestrator geben (siehe ``docs/BLOG.md``).

Die Seiten setzen ihre eigene, engere Content-Security-Policy: keine Skripte
ausser der optionalen Besucherzaehlung des Betreibers.
"""

import json
import logging
import os
from datetime import datetime
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import urlsplit
from xml.sax.saxutils import escape as xml
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    Response,
)
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core import blog
from app.db.session import get_db
from app.models.blog_post import STATUS_PUBLISHED, BlogPost

logger = logging.getLogger(__name__)

router = APIRouter(tags=["blog"])

_ORT = Path(__file__).resolve().parent.parent / "blog_site"
_vorlagen = Environment(
    loader=FileSystemLoader(str(_ORT / "templates")),
    autoescape=select_autoescape(default=True, default_for_string=True),
)

# Nur diese Dateien werden ausgeliefert — kein Pfad aus der Anfrage.
_SCHRIFTEN = {name: _ORT / "fonts" / name for name in ("sora.woff2", "ibm-plex-sans.woff2", "ibm-plex-mono.woff2")}

_MONATE = ("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
           "August", "September", "Oktober", "November", "Dezember")

_CACHE = "public, max-age=300"


def _nur_wenn_aktiv() -> None:
    if not blog.blog_aktiv():
        raise HTTPException(status_code=404, detail="Not found")


def _zone() -> ZoneInfo:
    try:
        return ZoneInfo(os.environ.get("TZ") or "Europe/Berlin")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("Europe/Berlin")


def _ort(d: datetime | None) -> datetime | None:
    """Zeitpunkt in der Zeitzone der Anlage — die Datenbank liefert UTC, und ein
    abends veroeffentlichter Beitrag truege sonst das Datum des Vortags."""
    if d is None or d.tzinfo is None:
        return d
    return d.astimezone(_zone())


def _datum(d: datetime | None) -> str:
    d = _ort(d)
    return f"{d.day}. {_MONATE[d.month - 1]} {d.year}" if d else ""


def _tag(d: datetime | None) -> str:
    d = _ort(d)
    return d.date().isoformat() if d else ""


def _marke() -> str:
    return settings.blog_site_name or "AI Employee"


def _analytics() -> tuple[str, str]:
    """Adresse und Kennung der Besucherzaehlung — nur als https-Adresse gueltig."""
    src, kennung = (settings.blog_analytics_src or "").strip(), (settings.blog_analytics_id or "").strip()
    teile = urlsplit(src)
    if teile.scheme != "https" or not teile.netloc or not kennung:
        return "", ""
    return src, kennung


def _csp(zaehlung: bool = True) -> str:
    src, _ = _analytics()
    herkunft = ""
    if src and zaehlung:
        teile = urlsplit(src)
        herkunft = f"{teile.scheme}://{teile.netloc}"
    return (
        "default-src 'none'; "
        "style-src 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        f"script-src {herkunft or chr(39) + 'none' + chr(39)}; "
        f"connect-src {herkunft or chr(39) + 'none' + chr(39)}; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    )


def _ld(daten: dict) -> str:
    """Strukturierte Daten fuer den Seitenkopf — so maskiert, dass kein Wert das Skript-Element beenden kann."""
    return json.dumps(daten, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def _bild(name: str, masse: dict) -> dict | None:
    """Angaben zu einem Titelbild fuer die Vorlagen — ``None``, wenn es das Bild nicht (mehr) gibt."""
    if not name or name not in masse:
        return None
    breite, hoehe = masse[name]
    return {"src": blog.BILD_PFAD + name, "breite": breite, "hoehe": hoehe}


def _karte(post: BlogPost, masse: dict | None = None) -> dict:
    datum = post.published_at or post.created_at
    return {
        "bild": _bild(post.cover, masse or {}),
        "slug": post.slug,
        "titel": post.title,
        "beschreibung": post.description,
        "themen": post.tags or [],
        "datum": _datum(datum),
        "datum_iso": _tag(datum),
        "lesezeit": blog.lesezeit(post.words),
    }


def _seite(vorlage: str, status: int = 200, cache: str = _CACHE, **werte) -> HTMLResponse:
    werte.setdefault("noindex", False)
    werte.setdefault("vorschau", False)
    # Keine Besucherzaehlung auf Vorschau-Seiten: die Adresse enthaelt den
    # Schluessel des Entwurfs, und ein Zaehldienst wuerde sie mitschreiben.
    zaehlung = not werte["vorschau"]
    src, kennung = _analytics() if zaehlung else ("", "")
    werte.setdefault("strukturdaten", [])
    werte.setdefault("og_typ", "website")
    werte.setdefault("og_titel", werte.get("seitentitel", ""))
    werte.setdefault("og_bild", "")
    html = _vorlagen.get_template(vorlage).render(
        marke=_marke(), basis=blog.basis_url(), analytics_src=src, analytics_id=kennung, **werte,
    )
    kopf = {"Content-Security-Policy": _csp(zaehlung), "Cache-Control": cache}
    if werte["vorschau"]:
        kopf["Referrer-Policy"] = "no-referrer"
    if werte["noindex"]:
        kopf["X-Robots-Tag"] = "noindex, nofollow"
    return HTMLResponse(html, status_code=status, headers=kopf)


def _nicht_gefunden() -> HTMLResponse:
    return _seite(
        "nicht_gefunden.html", status=404, cache="no-store", noindex=True,
        seitentitel=f"Nicht gefunden — {_marke()} Blog",
        beschreibung="Diesen Beitrag gibt es nicht.", canonical=blog.absolut("/blog"),
    )


# --- Seiten -------------------------------------------------------------------


@router.api_route("/blog", methods=["GET", "HEAD"], include_in_schema=False)
@router.api_route("/blog/", methods=["GET", "HEAD"], include_in_schema=False)
async def blog_uebersicht(db: AsyncSession = Depends(get_db)):
    _nur_wenn_aktiv()
    posts = await blog.veroeffentlichte(db)
    masse = await blog.bild_masse(db, [p.cover for p in posts])
    marke = _marke()
    beschreibung = (
        f"Antworten auf die Fragen, die sich beim Einsatz von KI-Agenten im Unternehmen stellen: "
        f"Betrieb, Sicherheit, Kosten und Praxis mit {marke}."
    )
    daten = {
        "@context": "https://schema.org",
        "@type": "Blog",
        "name": f"{marke} Blog",
        "url": blog.absolut("/blog"),
        "description": beschreibung,
        "inLanguage": "de",
        "blogPost": [
            {"@type": "BlogPosting", "headline": p.title, "url": blog.absolut(f"/blog/{p.slug}"),
             "datePublished": p.published_at.isoformat() if p.published_at else None}
            for p in posts[:20]
        ],
    }
    return _seite(
        "uebersicht.html",
        seitentitel=f"Blog: KI-Agenten im Unternehmen — {marke}",
        ueberschrift="KI-Agenten im Unternehmen: Fragen und Antworten",
        beschreibung=beschreibung,
        canonical=blog.absolut("/blog"),
        beitraege=[_karte(p, masse) for p in posts],
        strukturdaten=[_ld(daten)],
    )


@router.api_route("/blog/feed.xml", methods=["GET", "HEAD"], include_in_schema=False)
async def blog_feed(db: AsyncSession = Depends(get_db)):
    _nur_wenn_aktiv()
    posts = await blog.veroeffentlichte(db, limit=30)
    marke = _marke()
    eintraege = []
    for p in posts:
        adresse = xml(blog.absolut(f"/blog/{p.slug}"))
        eintraege.append(
            f"<item><title>{xml(p.title)}</title><link>{adresse}</link>"
            f"<guid isPermaLink=\"true\">{adresse}</guid>"
            f"<description>{xml(p.description)}</description>"
            + (f"<pubDate>{format_datetime(p.published_at)}</pubDate>" if p.published_at else "")
            + "</item>"
        )
    rss = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel>'
        f"<title>{xml(marke)} Blog</title><link>{xml(blog.absolut('/blog'))}</link>"
        f"<description>Fragen und Antworten zu KI-Agenten im Unternehmen.</description><language>de</language>"
        + "".join(eintraege) + "</channel></rss>"
    )
    return Response(rss, media_type="application/rss+xml; charset=utf-8", headers={"Cache-Control": _CACHE})


@router.api_route("/blog/latest.json", methods=["GET", "HEAD"], include_in_schema=False)
async def blog_neueste(db: AsyncSession = Depends(get_db)):
    """Die drei neuesten Beitraege — fuer den Abschnitt auf der Startseite."""
    _nur_wenn_aktiv()
    posts = await blog.veroeffentlichte(db, limit=3)
    masse = await blog.bild_masse(db, [p.cover for p in posts])
    return JSONResponse({"beitraege": [_karte(p, masse) for p in posts]}, headers={"Cache-Control": _CACHE})


@router.api_route("/blog/assets/fonts/{name}", methods=["GET", "HEAD"], include_in_schema=False)
async def blog_schrift(name: str):
    _nur_wenn_aktiv()
    datei = _SCHRIFTEN.get(name)
    if not datei or not datei.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(datei, media_type="font/woff2", headers={"Cache-Control": "public, max-age=31536000, immutable"})


@router.api_route("/blog/media/{name}", methods=["GET", "HEAD"], include_in_schema=False)
async def blog_bild(name: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Ein Bild des Blogs. Der Typ steht fest (PNG, JPEG, WebP) und wurde beim Hochladen am Inhalt geprueft.

    Die Bilddaten werden erst geladen, wenn sie wirklich gebraucht werden: Wer
    das Bild schon hat (``If-None-Match``) oder nur den Kopf will, bekommt die
    Antwort aus den Angaben zum Bild.
    """
    _nur_wenn_aktiv()
    bild = await blog.hole_bild(db, name)
    if not bild:
        raise HTTPException(status_code=404, detail="Not found")
    etag = f'"{bild.etag}"'
    kopf = {
        # Eine Stunde, danach Nachfrage mit Pruefsumme: ein ersetztes Bild ist schnell ueberall neu.
        "Cache-Control": "public, max-age=3600",
        "ETag": etag,
        # Selbst wenn ein Browser das Bild als Dokument oeffnet, laeuft darin nichts.
        "Content-Security-Policy": "default-src 'none'; sandbox",
    }
    if bild.etag and etag in (request.headers.get("if-none-match") or ""):
        return Response(status_code=304, headers=kopf)
    if request.method == "HEAD":
        return Response(media_type=bild.content_type, headers={**kopf, "Content-Length": str(bild.size)})
    bild = await blog.hole_bild(db, name, mit_daten=True)
    return Response(bild.data, media_type=bild.content_type, headers=kopf)


@router.api_route("/blog/{slug}", methods=["GET", "HEAD"], include_in_schema=False)
async def blog_beitrag(slug: str, vorschau: str = Query("", max_length=64), db: AsyncSession = Depends(get_db)):
    _nur_wenn_aktiv()
    if not blog.SLUG_RE.match(slug) or len(slug) > blog.MAX_SLUG:
        return _nicht_gefunden()
    post = await blog.hole(db, slug)
    if not post:
        return _nicht_gefunden()
    oeffentlich = post.status == STATUS_PUBLISHED
    if not oeffentlich and not blog.vorschau_gueltig(post, vorschau):
        # Ein Entwurf verraet sich nicht: dieselbe Antwort wie bei einer falschen Adresse.
        return _nicht_gefunden()

    andere = await blog.veroeffentlichte(db)
    im_text = [blog.bild_name_aus(src) for src, _ in blog.bilder_im_text(post.body_md)]
    masse = await blog.bild_masse(db, [post.cover, *im_text, *[p.cover for p in andere]])
    titelbild = _bild(post.cover, masse)
    html, gliederung = blog.render(post.body_md, masse)
    html = html.replace("<table>", '<div class="tabelle"><table>').replace("</table>", "</table></div>")
    marke = _marke()
    autor = post.author or settings.blog_author or ""
    adresse = blog.absolut(f"/blog/{post.slug}")
    geaendert = ""
    if post.published_at and post.updated_at and (_tag(post.updated_at) > _tag(post.published_at)):
        geaendert = _datum(post.updated_at)

    strukturdaten = []
    if oeffentlich:
        artikel = {
            "@context": "https://schema.org",
            "@type": "BlogPosting",
            "headline": post.title,
            "description": post.description,
            "inLanguage": "de",
            "mainEntityOfPage": adresse,
            "url": adresse,
            "datePublished": post.published_at.isoformat() if post.published_at else None,
            "dateModified": (post.updated_at or post.published_at).isoformat() if (post.updated_at or post.published_at) else None,
            "keywords": ", ".join(post.tags or []),
            "wordCount": post.words,
            "publisher": {"@type": "Organization", "name": marke, "url": blog.absolut("/")},
        }
        if autor:
            artikel["author"] = {"@type": "Person", "name": autor}
        if titelbild:
            artikel["image"] = blog.absolut(titelbild["src"])
        strukturdaten.append(_ld(artikel))
        strukturdaten.append(_ld({
            "@context": "https://schema.org",
            "@type": "BreadcrumbList",
            "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": "Start", "item": blog.absolut("/")},
                {"@type": "ListItem", "position": 2, "name": "Blog", "item": blog.absolut("/blog")},
                {"@type": "ListItem", "position": 3, "name": post.title, "item": adresse},
            ],
        }))
        if post.faq:
            strukturdaten.append(_ld({
                "@context": "https://schema.org",
                "@type": "FAQPage",
                "mainEntity": [
                    {"@type": "Question", "name": f["frage"], "acceptedAnswer": {"@type": "Answer", "text": f["antwort"]}}
                    for f in post.faq
                ],
            }))

    return _seite(
        "beitrag.html",
        titelbild=titelbild,
        og_bild=blog.absolut(titelbild["src"]) if titelbild else "",
        cache=_CACHE if oeffentlich else "no-store",
        noindex=not oeffentlich,
        vorschau=not oeffentlich,
        og_typ="article",
        og_titel=post.title,
        seitentitel=f"{post.title} — {marke}",
        beschreibung=post.description,
        canonical=adresse,
        titel=post.title,
        vorspann=post.description,
        datum=_datum(post.published_at),
        datum_iso=_tag(post.published_at),
        geaendert=geaendert,
        lesezeit=blog.lesezeit(post.words),
        autor=autor,
        themen=post.tags or [],
        gliederung=gliederung,
        html=html,
        faq=post.faq or [],
        verwandte=[_karte(p, masse) for p in blog.verwandte(post, andere)],
        strukturdaten=strukturdaten,
    )


# --- Fuer Suchmaschinen -------------------------------------------------------


@router.api_route("/sitemap.xml", methods=["GET", "HEAD"], include_in_schema=False)
async def sitemap(db: AsyncSession = Depends(get_db)):
    _nur_wenn_aktiv()
    posts = await blog.veroeffentlichte(db)
    neuester = max((p.updated_at or p.published_at for p in posts if (p.updated_at or p.published_at)), default=None)

    def eintrag(pfad: str, stand: datetime | None) -> str:
        lastmod = f"<lastmod>{_tag(stand)}</lastmod>" if stand else ""
        return f"<url><loc>{xml(blog.absolut(pfad))}</loc>{lastmod}</url>"

    zeilen = [eintrag("/", None), eintrag("/blog", neuester)]
    zeilen += [eintrag(f"/blog/{p.slug}", p.updated_at or p.published_at) for p in posts]
    inhalt = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + "".join(zeilen) + "</urlset>"
    )
    return Response(inhalt, media_type="application/xml; charset=utf-8", headers={"Cache-Control": _CACHE})


@router.api_route("/robots.txt", methods=["GET", "HEAD"], include_in_schema=False)
async def robots():
    _nur_wenn_aktiv()
    return PlainTextResponse(
        "User-agent: *\nAllow: /\nDisallow: /api/\n\n"
        f"Sitemap: {blog.absolut('/sitemap.xml')}\n",
        headers={"Cache-Control": _CACHE},
    )
