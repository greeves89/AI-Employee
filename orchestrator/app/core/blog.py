"""Blog der Landingpage: Aufbereitung, Pruefung, Verweise.

Hier liegt alles, was sowohl der oeffentliche Blog (``api/blog_public.py``) als
auch der MCP-Dienst (``api/blog_mcp.py``) brauchen — damit es EINE Stelle gibt,
an der aus Markdown HTML wird und an der entschieden wird, ob ein Beitrag
veroeffentlicht werden darf.

Sicherheit: Der Text eines Beitrags kommt ueber einen Dienst mit Zugangsschluessel
herein und geht ohne Anmeldung an jeden Besucher hinaus. Deshalb
- wird rohes HTML im Markdown NICHT durchgereicht (``html=False``: es erscheint
  als Text),
- lehnt der Renderer gefaehrliche Verweisziele ab (``javascript:`` u. a.),
- laufen alle uebrigen Felder durch die automatische Maskierung der Vorlagen.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import re
import secrets
import struct
import unicodedata
from datetime import datetime, timezone

from markdown_it import MarkdownIt
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer, undefer

from app.config import settings
from app.models.audit_log import AuditEventType, AuditLog
from app.models.blog_image import BlogImage
from app.models.blog_post import BLOG_STATUS, STATUS_DRAFT, STATUS_PUBLISHED, BlogPost

# Kleinbuchstaben, Ziffern, Bindestriche; kein Bindestrich am Rand oder doppelt.
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*\Z")
MAX_SLUG = 80
MAX_BODY = 60_000
MAX_TAGS = 8
MAX_FAQ = 12
# Der Schluessel des MCP-Dienstes muss lang genug sein, um nicht erraten zu
# werden; ein kuerzerer Wert schaltet den Dienst nicht frei.
MIN_TOKEN = 32

# Adressen unter /blog/, die keine Beitraege sind.
RESERVED_SLUGS = frozenset({"feed", "assets", "latest", "vorschau", "thema", "media"})

# Bilder: Name wie eine Adresse plus Endung; Typ wird an den ersten Bytes
# erkannt, nicht an der Endung geglaubt. Kein SVG — es kann Skripte tragen.
BILD_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*\.(png|jpg|webp)\Z")
BILD_TYPEN = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}
MAX_BILD = 1_500_000
MAX_BILDER = 500
# Abmessungen: gross genug fuer ein Titelbild in doppelter Aufloesung, klein
# genug, dass kein Bild den Browser eines Besuchers beim Entpacken ueberlaedt.
MAX_BILD_SEITE = 6000
MAX_BILD_PIXEL = 24_000_000
# Anfrage an den MCP-Dienst: ein Bild in Base64 plus Umschlag, nicht mehr.
MAX_MCP_ANFRAGE = MAX_BILD * 4 // 3 + 200_000
BILD_PFAD = "/blog/media/"

WORDS_PER_MINUTE = 200

# Steuerzeichen haben in keinem Feld etwas zu suchen: sie machen Feed und
# Sitemap ungueltig, und ein Nullbyte lehnt die Datenbank ab. Zeilenumbruch
# und Tabulator bleiben.
_STEUERZEICHEN = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ufffe\uffff]")

_md = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False}).enable("table")


class BlogFehler(ValueError):
    """Eine Angabe zu einem Beitrag ist unzulaessig. Die Meldung geht an den Aufrufer."""


# --- Schalter -----------------------------------------------------------------


def blog_zustand() -> str:
    """``an``, ``aus`` oder ``ohne_adresse``.

    Ohne oeffentliche Adresse gibt es keinen Blog: kanonische Adresse, Sitemap
    und Feed braeuchten sie, und relative Angaben dort sind fuer Suchmaschinen
    ungueltig. Lieber kein Blog als einer mit falschen Verweisen.
    """
    if not settings.blog_enabled:
        return "aus"
    return "an" if basis_url() else "ohne_adresse"


def blog_aktiv() -> bool:
    return blog_zustand() == "an"


def mcp_token() -> str:
    """Der gueltige Schluessel des MCP-Dienstes — oder leer, wenn keiner gilt."""
    token = (settings.blog_mcp_token or "").strip()
    return token if len(token) >= MIN_TOKEN else ""


def basis_url() -> str:
    """Oeffentliche Adresse der Seite ohne Schraegstrich am Ende."""
    return (settings.blog_base_url or settings.public_app_url or "").strip().rstrip("/")


def absolut(pfad: str) -> str:
    return f"{basis_url()}{pfad}"


# --- Angaben pruefen ----------------------------------------------------------


def _umlaute(text: str) -> str:
    return (
        text.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
        .replace("Ä", "ae").replace("Ö", "oe").replace("Ü", "ue").replace("ß", "ss")
    )


def slug_aus(text: str) -> str:
    """Adresse aus einem Titel ableiten: Umlaute ausschreiben, Rest vereinfachen."""
    text = unicodedata.normalize("NFKD", _umlaute(text)).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return text[:MAX_SLUG].rstrip("-")


def pruefe_slug(slug) -> str:
    slug = str(slug or "").strip()
    if not slug or len(slug) > MAX_SLUG or not SLUG_RE.match(slug):
        raise BlogFehler(
            "slug: nur Kleinbuchstaben, Ziffern und einzelne Bindestriche, höchstens "
            f"{MAX_SLUG} Zeichen (Beispiel: ki-agenten-selbst-hosten)."
        )
    if slug in RESERVED_SLUGS:
        raise BlogFehler(f"slug: „{slug}“ ist eine feste Adresse des Blogs — bitte einen anderen wählen.")
    return slug


def pruefe_slug_oder_none(slug) -> str | None:
    """Die Adresse, wenn sie zulaessig ist — sonst ``None`` (fuer Lesewege, die mit 404 antworten)."""
    try:
        return pruefe_slug(slug)
    except BlogFehler:
        return None


def _zeile(wert, feld: str, hoechstens: int, pflicht: bool = False) -> str:
    text = re.sub(r"\s+", " ", _STEUERZEICHEN.sub("", str(wert or ""))).strip()
    if pflicht and not text:
        raise BlogFehler(f"{feld}: darf nicht leer sein.")
    if len(text) > hoechstens:
        raise BlogFehler(f"{feld}: höchstens {hoechstens} Zeichen (sind {len(text)}).")
    return text


def pruefe_tags(tags) -> list[str]:
    if tags in (None, ""):
        return []
    if isinstance(tags, str):
        tags = [t for t in tags.split(",")]
    if not isinstance(tags, list):
        raise BlogFehler("tags: eine Liste von Begriffen.")
    sauber: list[str] = []
    for t in tags:
        t = _zeile(t, "tags", 40)
        if t and t not in sauber:
            sauber.append(t)
    if len(sauber) > MAX_TAGS:
        raise BlogFehler(f"tags: höchstens {MAX_TAGS}.")
    return sauber


def pruefe_faq(faq) -> list[dict]:
    if faq in (None, ""):
        return []
    if not isinstance(faq, list):
        raise BlogFehler("faq: eine Liste aus {frage, antwort}.")
    sauber: list[dict] = []
    for eintrag in faq:
        if not isinstance(eintrag, dict):
            raise BlogFehler("faq: jeder Eintrag braucht frage und antwort.")
        frage = _zeile(eintrag.get("frage") or eintrag.get("question"), "faq.frage", 200, pflicht=True)
        antwort = _zeile(eintrag.get("antwort") or eintrag.get("answer"), "faq.antwort", 1200, pflicht=True)
        sauber.append({"frage": frage, "antwort": antwort})
    if len(sauber) > MAX_FAQ:
        raise BlogFehler(f"faq: höchstens {MAX_FAQ} Fragen.")
    return sauber


def pruefe_text(body_md) -> str:
    text = _STEUERZEICHEN.sub("", str(body_md or "").replace("\r\n", "\n").replace("\r", "\n")).strip()
    if not text:
        raise BlogFehler("body_markdown: darf nicht leer sein.")
    if len(text) > MAX_BODY:
        raise BlogFehler(f"body_markdown: höchstens {MAX_BODY} Zeichen (sind {len(text)}).")
    return text


# --- Aufbereitung -------------------------------------------------------------


def _anker(text: str, vergeben: set[str]) -> str:
    anker = slug_aus(text) or "abschnitt"
    basis, n = anker, 2
    while anker in vergeben:
        anker = f"{basis}-{n}"
        n += 1
    vergeben.add(anker)
    return anker


def _ist_extern(href: str) -> bool:
    if not re.match(r"^[a-z][a-z0-9+.-]*:", href, re.I) and not href.startswith("//"):
        return False  # relativ oder Anker
    basis = basis_url()
    return not (basis and (href == basis or href.startswith(basis + "/")))


def bild_name_aus(src: str) -> str | None:
    """Name eines Blog-Bildes aus einer Adresse im Text — oder ``None``, wenn es keines ist."""
    src = (src or "").strip()
    basis = basis_url()
    if basis and src.startswith(basis + BILD_PFAD):
        src = src[len(basis):]
    if not src.startswith(BILD_PFAD):
        return None
    name = src[len(BILD_PFAD):]
    return name if BILD_NAME_RE.match(name) else None


def bilder_im_text(body_md: str) -> list[tuple[str, str]]:
    """Alle Bilder im Text als (Adresse, alt-Text)."""
    funde: list[tuple[str, str]] = []
    for tok in _md.parse(body_md or ""):
        for kind in (tok.children or []) if tok.type == "inline" else []:
            if kind.type == "image":
                funde.append((str(kind.attrGet("src") or ""), kind.content or ""))
    return funde


def render(body_md: str, masse: dict[str, tuple[int, int]] | None = None) -> tuple[str, list[dict]]:
    """Markdown zu HTML. Liefert das HTML und die Zwischenueberschriften (h2).

    Ueberschriften bekommen einen Anker fuer das Inhaltsverzeichnis. Eine
    Ueberschrift erster Ordnung im Text wird zur zweiten herabgestuft — die
    erste gehoert dem Titel der Seite. Verweise nach draussen oeffnen ohne
    Zugriff auf das eigene Fenster.

    Bilder gibt es nur aus dem eigenen Bestand (``/blog/media/<name>``); jedes
    andere Bild wird zu seinem alt-Text. ``masse`` (Name → Breite, Hoehe) setzt
    die Abmessungen, damit die Seite beim Laden nicht springt.
    """
    tokens = _md.parse(body_md or "")
    vergeben: set[str] = set()
    gliederung: list[dict] = []
    for i, tok in enumerate(tokens):
        if tok.type in ("heading_open", "heading_close") and tok.tag == "h1":
            tok.tag = "h2"
        if tok.type == "heading_open":
            inhalt = tokens[i + 1]
            text = "".join(k.content for k in (inhalt.children or []) if k.type in ("text", "code_inline"))
            anker = _anker(text, vergeben)
            tok.attrSet("id", anker)
            if tok.tag == "h2":
                gliederung.append({"anker": anker, "text": text})
        if tok.type == "inline":
            for kind in tok.children or []:
                if kind.type == "link_open" and _ist_extern(str(kind.attrGet("href") or "")):
                    kind.attrSet("rel", "noopener noreferrer")
                if kind.type == "image":
                    name = bild_name_aus(str(kind.attrGet("src") or ""))
                    if not name:
                        # Fremdes Bild: nur sein Text bleibt.
                        kind.type, kind.tag, kind.attrs, kind.children = "text", "", {}, None
                        continue
                    kind.attrSet("src", BILD_PFAD + name)
                    kind.attrSet("loading", "lazy")
                    kind.attrSet("decoding", "async")
                    breite, hoehe = (masse or {}).get(name, (0, 0))
                    if breite and hoehe:
                        kind.attrSet("width", str(breite))
                        kind.attrSet("height", str(hoehe))
    return _md.renderer.render(tokens, _md.options, {}), gliederung


def reiner_text(body_md: str) -> str:
    """Der Text ohne Auszeichnung — fuer Wortzahl, Suche und Pruefung."""
    teile: list[str] = []
    for tok in _md.parse(body_md or ""):
        if tok.type == "inline":
            teile.append("".join(k.content for k in (tok.children or []) if k.type in ("text", "code_inline", "softbreak")) or tok.content)
        elif tok.type in ("fence", "code_block"):
            teile.append(tok.content)
    return re.sub(r"\s+", " ", " ".join(teile)).strip()


def wortzahl(body_md: str) -> int:
    return len(re.findall(r"\w+", reiner_text(body_md)))


def lesezeit(worte: int) -> int:
    """Lesezeit in Minuten aus der Wortzahl."""
    return max(1, round((worte or 0) / WORDS_PER_MINUTE))


def verweise(body_md: str) -> list[str]:
    ziele: list[str] = []
    for tok in _md.parse(body_md or ""):
        for kind in (tok.children or []) if tok.type == "inline" else []:
            if kind.type == "link_open":
                ziele.append(str(kind.attrGet("href") or ""))
    return ziele


# --- Pruefung vor der Veroeffentlichung ---------------------------------------


def _enthaelt(text: str, begriff: str) -> bool:
    return bool(begriff) and begriff.casefold() in (text or "").casefold()


def seo_pruefung(post: BlogPost) -> dict:
    """Was einem Beitrag noch fehlt, um gut gefunden zu werden.

    ``fehler`` verhindern die Veroeffentlichung, ``hinweise`` nicht. Die
    Schwellen sind die ueblichen Faustregeln (Titel bis 60 Zeichen, Beschreibung
    120 bis 160), keine Garantie fuer eine Platzierung.
    """
    fehler: list[str] = []
    hinweise: list[str] = []
    titel, beschr, begriff = post.title or "", post.description or "", (post.keyword or "").strip()
    tokens = _md.parse(post.body_md or "")
    h2 = [tokens[i + 1].content for i, t in enumerate(tokens) if t.type == "heading_open" and t.tag == "h2"]
    h1 = [t for t in tokens if t.type == "heading_open" and t.tag == "h1"]
    text = reiner_text(post.body_md)
    worte = len(re.findall(r"\w+", text))
    intern = [z for z in verweise(post.body_md) if not _ist_extern(z) and not z.startswith("#")]

    if not titel:
        fehler.append("Titel fehlt.")
    elif len(titel) > 60:
        hinweise.append(f"Titel hat {len(titel)} Zeichen — über 60 wird er im Suchtreffer abgeschnitten.")
    elif len(titel) < 25:
        hinweise.append(f"Titel hat nur {len(titel)} Zeichen — 30 bis 60 nutzen den Platz im Suchtreffer.")

    if not beschr:
        fehler.append("Beschreibung fehlt — sie ist der Text unter dem Suchtreffer.")
    elif not 110 <= len(beschr) <= 165:
        hinweise.append(f"Beschreibung hat {len(beschr)} Zeichen — 120 bis 160 sind üblich.")

    if not begriff:
        fehler.append("Hauptbegriff (keyword) fehlt — ohne ihn lässt sich nichts prüfen und nichts verlinken.")
    else:
        if not _enthaelt(titel, begriff):
            hinweise.append(f"Der Hauptbegriff „{begriff}“ steht nicht im Titel.")
        if not _enthaelt(beschr, begriff):
            hinweise.append(f"Der Hauptbegriff „{begriff}“ steht nicht in der Beschreibung.")
        if not _enthaelt(" ".join(text.split()[:150]), begriff):
            hinweise.append(f"Der Hauptbegriff „{begriff}“ fehlt in den ersten 150 Wörtern.")
        if not any(_enthaelt(u, begriff) for u in h2):
            hinweise.append(f"Keine Zwischenüberschrift nennt den Hauptbegriff „{begriff}“.")

    if h1:
        hinweise.append("Der Text enthält eine Überschrift erster Ordnung (#). Sie wird herabgestuft — die erste gehört dem Titel.")
    if len(h2) < 2:
        fehler.append("Weniger als zwei Zwischenüberschriften (##) — der Text braucht eine Gliederung.")
    if worte < 300:
        fehler.append(f"Nur {worte} Wörter — das beantwortet keine Frage gründlich.")
    elif worte < 700:
        hinweise.append(f"{worte} Wörter — für eine gründliche Antwort sind 700 und mehr üblich.")
    if len(intern) < 2:
        hinweise.append(
            f"{len(intern)} Verweis(e) auf eigene Seiten — mindestens zwei setzen "
            "(blog_find_mentions zeigt passende Beiträge)."
        )
    if not post.faq:
        hinweise.append("Keine Fragen und Antworten (faq) — sie beantworten Nebenfragen und erscheinen als eigener Abschnitt.")
    if not post.tags:
        hinweise.append("Keine Themen (tags) — sie verbinden den Beitrag mit verwandten.")
    im_text = bilder_im_text(post.body_md)
    if not post.cover:
        hinweise.append("Kein Titelbild (cover) — es erscheint oben im Beitrag, auf der Karte und beim Teilen.")
    if not im_text:
        hinweise.append("Kein Bild im Text — eine Grafik oder ein Bildschirmfoto macht den Beitrag anschaulicher.")
    ohne_alt = [src for src, alt in im_text if not alt.strip()]
    if ohne_alt:
        hinweise.append(f"{len(ohne_alt)} Bild(er) ohne Beschreibung — so schreiben: ![Was zu sehen ist](/blog/media/name.png).")
    fremd = [src for src, _ in im_text if not bild_name_aus(src)]
    if fremd:
        hinweise.append(f"{len(fremd)} Bild(er) liegen nicht im Blog und werden nicht angezeigt — erst hochladen (blog_upload_image).")

    return {
        "fehler": fehler,
        "hinweise": hinweise,
        "worte": worte,
        "lesezeit_minuten": lesezeit(worte),
        "zwischenueberschriften": len(h2),
        "interne_verweise": len(intern),
        "bilder": len(im_text),
        "titel_zeichen": len(titel),
        "beschreibung_zeichen": len(beschr),
    }


# --- Speichern und Lesen ------------------------------------------------------

# Herkunft einer Aenderung im Protokoll.
UEBER_MCP = "mcp"
UEBER_OBERFLAECHE = "oberflaeche"


def _protokoll(ereignis: AuditEventType, slug: str, wer: str | None, ueber: str, **meta) -> AuditLog:
    """Ein Protokolleintrag je Aenderung am Blog — im selben Commit wie die Aenderung.

    ``agent_id`` ist im Modell Pflicht: ``admin`` fuer die Oberflaeche (wie die
    uebrigen Verwaltungs-Endpunkte), ``blog-mcp`` fuer den MCP-Dienst, der
    keinen Nutzer kennt.
    """
    return AuditLog(
        agent_id="admin" if ueber == UEBER_OBERFLAECHE else "blog-mcp",
        user_id=wer,
        event_type=ereignis,
        command=f"/blog/{slug}",
        outcome="success",
        meta={"ueber": ueber, **meta},
    )


async def hole(db: AsyncSession, slug: str) -> BlogPost | None:
    return (await db.execute(select(BlogPost).where(BlogPost.slug == slug))).scalar_one_or_none()


async def hole_oder_none(db: AsyncSession, slug) -> BlogPost | None:
    """Der Beitrag zu einer Adresse — ``None`` auch dann, wenn die Adresse gar nicht zulaessig ist."""
    sauber = pruefe_slug_oder_none(slug)
    return await hole(db, sauber) if sauber else None


async def veroeffentlichte(db: AsyncSession, limit: int | None = None, mit_text: bool = False) -> list[BlogPost]:
    """Veroeffentlichte Beitraege, neueste zuerst.

    Ohne ``mit_text`` bleibt der Text in der Datenbank: Uebersicht, Feed,
    Sitemap und Karten brauchen ihn nicht, und sie sind ohne Anmeldung abrufbar.
    """
    abfrage = (
        select(BlogPost)
        .where(BlogPost.status == STATUS_PUBLISHED)
        .order_by(BlogPost.published_at.desc(), BlogPost.id.desc())
    )
    if not mit_text:
        abfrage = abfrage.options(defer(BlogPost.body_md))
    if limit:
        abfrage = abfrage.limit(limit)
    return list((await db.execute(abfrage)).scalars().all())


async def alle(db: AsyncSession, status: str | None = None) -> list[BlogPost]:
    abfrage = select(BlogPost).options(defer(BlogPost.body_md)).order_by(BlogPost.updated_at.desc(), BlogPost.id.desc())
    if status:
        status = str(status)
        if status not in BLOG_STATUS:
            raise BlogFehler(f"status: {' oder '.join(BLOG_STATUS)}.")
        abfrage = abfrage.where(BlogPost.status == status)
    return list((await db.execute(abfrage)).scalars().all())


async def speichere(db: AsyncSession, angaben: dict, wer: str | None = None, ueber: str = UEBER_MCP) -> tuple[BlogPost, bool]:
    """Beitrag anlegen oder aendern. Liefert den Beitrag und ob er neu ist.

    Ein neuer Beitrag ist immer ein Entwurf. Beim Aendern bleiben nicht
    genannte Felder, wie sie sind — auch der Status.
    """
    genannt = str(angaben.get("slug") or "").strip()
    slug = pruefe_slug(genannt or slug_aus(str(angaben.get("title") or "")))
    post = await hole(db, slug)
    neu = post is None
    if not neu and not genannt:
        # Ohne genannte Adresse ist ein NEUER Beitrag gemeint. Stillschweigend
        # einen vorhandenen mit gleichem Titel zu ueberschreiben, waere bei
        # einem veroeffentlichten sofort oeffentlich.
        raise BlogFehler(
            f"Unter „{slug}“ gibt es schon einen Beitrag. Zum Ändern slug=\"{slug}\" angeben, "
            "für einen neuen Beitrag einen anderen slug wählen."
        )
    if neu:
        post = BlogPost(slug=slug, status=STATUS_DRAFT, tags=[], faq=[], preview_key=secrets.token_urlsafe(24))
        for pflicht in ("title", "body_markdown"):
            if not angaben.get(pflicht):
                raise BlogFehler(f"{pflicht}: wird für einen neuen Beitrag gebraucht.")

    if "title" in angaben:
        post.title = _zeile(angaben["title"], "title", 200, pflicht=True)
    if "description" in angaben:
        post.description = _zeile(angaben["description"], "description", 320)
    if "keyword" in angaben:
        post.keyword = _zeile(angaben["keyword"], "keyword", 120)
    if "body_markdown" in angaben:
        post.body_md = pruefe_text(angaben["body_markdown"])
        post.words = wortzahl(post.body_md)
    if "tags" in angaben:
        post.tags = pruefe_tags(angaben["tags"])
    if "faq" in angaben:
        post.faq = pruefe_faq(angaben["faq"])
    if "author" in angaben:
        post.author = _zeile(angaben["author"], "author", 120)
    if "cover" in angaben:
        cover = _zeile(angaben["cover"], "cover", 120)
        if cover and not await hole_bild(db, cover):
            raise BlogFehler(f"cover: Das Bild „{cover}“ gibt es nicht — erst hochladen (blog_upload_image).")
        post.cover = cover

    if post.status == STATUS_PUBLISHED:
        # Ein veroeffentlichter Beitrag zeigt jede Aenderung sofort. Was die
        # Veroeffentlichung verhindert haette, darf deshalb auch nicht
        # nachtraeglich hineingespeichert werden.
        fehler = seo_pruefung(post)["fehler"]
        if fehler:
            await db.rollback()
            raise BlogFehler(
                "Nicht gespeichert — der Beitrag ist veröffentlicht, und mit dieser Änderung gilt: "
                + " ".join(fehler) + " Erst zurückziehen (blog_unpublish) oder die Änderung anpassen."
            )

    if neu:
        db.add(post)
    db.add(_protokoll(AuditEventType.BLOG_POST_SAVED, slug, wer, ueber, neu=neu, status=post.status,
                      felder=sorted(k for k in angaben if k != "slug")))
    try:
        await db.commit()
    except IntegrityError as e:
        # Zwei gleichzeitige Anlagen derselben Adresse: der eindeutige Index
        # entscheidet, und der Verlierer bekommt eine verstaendliche Absage.
        await db.rollback()
        raise BlogFehler(f"Unter „{slug}“ gibt es schon einen Beitrag.") from e
    await db.refresh(post)
    return post, neu


async def veroeffentliche(db: AsyncSession, slug: str, wer: str | None = None, ueber: str = UEBER_MCP) -> tuple[BlogPost, dict]:
    post = await hole(db, pruefe_slug(slug))
    if not post:
        raise BlogFehler(f"Kein Beitrag mit der Adresse „{slug}“.")
    pruefung = seo_pruefung(post)
    if pruefung["fehler"]:
        raise BlogFehler("Noch nicht veröffentlicht: " + " ".join(pruefung["fehler"]))
    post.status = STATUS_PUBLISHED
    # Das erste Datum bleibt: ein spaeter ueberarbeiteter Beitrag wird nicht "neu".
    if not post.published_at:
        post.published_at = datetime.now(timezone.utc)
    db.add(_protokoll(AuditEventType.BLOG_POST_PUBLISHED, post.slug, wer, ueber, titel=post.title))
    await db.commit()
    await db.refresh(post)
    return post, pruefung


async def ziehe_zurueck(db: AsyncSession, slug: str, wer: str | None = None, ueber: str = UEBER_MCP) -> BlogPost:
    post = await hole(db, pruefe_slug(slug))
    if not post:
        raise BlogFehler(f"Kein Beitrag mit der Adresse „{slug}“.")
    post.status = STATUS_DRAFT
    # Neue Vorschau-Adresse: die alte kann inzwischen in fremden Haenden sein.
    post.preview_key = secrets.token_urlsafe(24)
    db.add(_protokoll(AuditEventType.BLOG_POST_UNPUBLISHED, post.slug, wer, ueber))
    await db.commit()
    await db.refresh(post)
    return post


async def loesche(db: AsyncSession, slug: str, wer: str | None = None, ueber: str = UEBER_MCP) -> None:
    post = await hole(db, pruefe_slug(slug))
    if not post:
        raise BlogFehler(f"Kein Beitrag mit der Adresse „{slug}“.")
    db.add(_protokoll(AuditEventType.BLOG_POST_DELETED, post.slug, wer, ueber, titel=post.title, status=post.status))
    await db.delete(post)
    await db.commit()


# --- Bilder -------------------------------------------------------------------


def _bild_masse(daten: bytes, endung: str) -> tuple[int, int]:
    """Breite und Hoehe aus den Bilddaten lesen. (0, 0), wenn sie sich nicht bestimmen lassen."""
    try:
        if endung == "png":
            return struct.unpack(">II", daten[16:24])
        if endung == "jpg":
            i = 2
            while i + 9 < len(daten):
                if daten[i] != 0xFF:
                    i += 1
                    continue
                marke = daten[i + 1]
                if marke in (0xC0, 0xC1, 0xC2):
                    hoehe, breite = struct.unpack(">HH", daten[i + 5:i + 9])
                    return breite, hoehe
                if marke in (0xD8, 0x01) or 0xD0 <= marke <= 0xD7:
                    i += 2
                    continue
                i += 2 + struct.unpack(">H", daten[i + 2:i + 4])[0]
        if endung == "webp":
            art = daten[12:16]
            if art == b"VP8X":
                breite = int.from_bytes(daten[24:27], "little") + 1
                hoehe = int.from_bytes(daten[27:30], "little") + 1
                return breite, hoehe
            if art == b"VP8 ":
                breite, hoehe = struct.unpack("<HH", daten[26:30])
                return breite & 0x3FFF, hoehe & 0x3FFF
            if art == b"VP8L":
                bits = int.from_bytes(daten[21:25], "little")
                return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    except (struct.error, IndexError):
        pass
    return 0, 0


def _bild_typ(daten: bytes) -> str | None:
    """Die Endung, die zu den ersten Bytes passt — der Inhalt entscheidet, nicht der Name."""
    if daten.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if daten.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if daten[:4] == b"RIFF" and daten[8:12] == b"WEBP":
        return "webp"
    return None


def pruefe_bild_name(name) -> str:
    name = str(name or "").strip().lower()
    if len(name) > 120 or not BILD_NAME_RE.match(name):
        raise BlogFehler(
            "name: Kleinbuchstaben, Ziffern und Bindestriche mit der Endung .png, .jpg oder .webp "
            "(Beispiel: kostenbloecke-ki-agent.png)."
        )
    return name


def bild_aus_base64(text) -> bytes:
    roh = str(text or "").strip()
    if roh.startswith("data:"):
        roh = roh.split(",", 1)[-1]
    # Vor dem Dekodieren pruefen — sonst liegt ein zu grosses Bild erst einmal ganz im Speicher.
    if len(roh) > MAX_BILD * 4 // 3 + 8:
        raise BlogFehler(f"Das Bild ist größer als {MAX_BILD // 1000} kB.")
    try:
        return base64.b64decode(roh, validate=True)
    except (binascii.Error, ValueError) as e:
        raise BlogFehler("data_base64: kein gültiges Base64.") from e


async def hole_bild(db: AsyncSession, name, mit_daten: bool = False) -> BlogImage | None:
    """Ein Bild nach Namen. Die Bilddaten kommen nur mit, wenn sie gebraucht werden (Auslieferung)."""
    name = str(name or "")
    if not BILD_NAME_RE.match(name):
        return None
    abfrage = select(BlogImage).where(BlogImage.name == name)
    if mit_daten:
        abfrage = abfrage.options(undefer(BlogImage.data))
    return (await db.execute(abfrage)).scalar_one_or_none()


async def bilder(db: AsyncSession) -> list[BlogImage]:
    return list((await db.execute(select(BlogImage).order_by(BlogImage.created_at.desc(), BlogImage.id.desc()))).scalars().all())


async def bild_masse(db: AsyncSession, namen) -> dict[str, tuple[int, int]]:
    namen = [n for n in set(namen) if n]
    if not namen:
        return {}
    zeilen = await db.execute(select(BlogImage.name, BlogImage.width, BlogImage.height).where(BlogImage.name.in_(namen)))
    return {name: (breite, hoehe) for name, breite, hoehe in zeilen.all()}


async def speichere_bild(db: AsyncSession, name, daten: bytes, alt="", wer: str | None = None,
                         ueber: str = UEBER_MCP) -> tuple[BlogImage, bool]:
    """Bild anlegen oder ersetzen. Liefert das Bild und ob es neu ist."""
    name = pruefe_bild_name(name)
    if not daten:
        raise BlogFehler("Das Bild ist leer.")
    if len(daten) > MAX_BILD:
        raise BlogFehler(f"Das Bild hat {len(daten) // 1000} kB — höchstens {MAX_BILD // 1000} kB. Kleiner rechnen oder als JPEG/WebP speichern.")
    typ = _bild_typ(daten)
    if not typ:
        raise BlogFehler("Kein PNG, JPEG oder WebP — andere Formate (auch SVG) nimmt der Blog nicht an.")
    if typ != name.rsplit(".", 1)[1]:
        raise BlogFehler(f"Der Inhalt ist ein .{typ}-Bild, der Name endet anders. Bitte „{name.rsplit('.', 1)[0]}.{typ}“ verwenden.")
    alt = _zeile(alt, "alt", 300)
    breite, hoehe = _bild_masse(daten, typ)
    if breite <= 0 or hoehe <= 0:
        raise BlogFehler("Die Abmessungen des Bildes lassen sich nicht lesen — die Datei ist beschädigt oder kein übliches Bild.")
    if breite > MAX_BILD_SEITE or hoehe > MAX_BILD_SEITE or breite * hoehe > MAX_BILD_PIXEL:
        raise BlogFehler(f"Das Bild hat {breite} × {hoehe} Bildpunkte — höchstens {MAX_BILD_SEITE} je Seite.")

    bild = await hole_bild(db, name)
    neu = bild is None
    if neu:
        anzahl = len((await db.execute(select(BlogImage.id))).all())
        if anzahl >= MAX_BILDER:
            raise BlogFehler(f"Der Blog hält schon {anzahl} Bilder — erst nicht mehr gebrauchte löschen.")
        bild = BlogImage(name=name)
        db.add(bild)
    bild.content_type = BILD_TYPEN[typ]
    bild.data = daten
    bild.size = len(daten)
    bild.etag = hashlib.sha256(daten).hexdigest()[:32]
    bild.width, bild.height = breite, hoehe
    if alt or neu:
        bild.alt = alt
    db.add(_protokoll(AuditEventType.BLOG_IMAGE_SAVED, f"media/{name}", wer, ueber, neu=neu, bytes=len(daten)))
    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        raise BlogFehler(f"Ein Bild mit dem Namen „{name}“ gibt es schon.") from e
    return bild, neu


async def bild_verwendung(db: AsyncSession, name: str) -> list[str]:
    """Adressen der Beitraege, die ein Bild als Titelbild oder im Text verwenden."""
    posts = (await db.execute(select(BlogPost))).scalars().all()
    return [p.slug for p in posts
            if p.cover == name or any(bild_name_aus(src) == name for src, _ in bilder_im_text(p.body_md))]


async def loesche_bild(db: AsyncSession, name, wer: str | None = None, ueber: str = UEBER_MCP) -> None:
    bild = await hole_bild(db, pruefe_bild_name(name))
    if not bild:
        raise BlogFehler(f"Kein Bild mit dem Namen „{name}“.")
    genutzt = await bild_verwendung(db, bild.name)
    if genutzt:
        raise BlogFehler("Das Bild wird noch verwendet in: " + ", ".join(genutzt) + ". Erst dort entfernen.")
    db.add(_protokoll(AuditEventType.BLOG_IMAGE_DELETED, f"media/{bild.name}", wer, ueber))
    await db.delete(bild)
    await db.commit()


def bild_kurz(bild: BlogImage) -> dict:
    return {
        "name": bild.name,
        "adresse": BILD_PFAD + bild.name,
        "markdown": f"![{bild.alt or 'Beschreibung'}]({BILD_PFAD}{bild.name})",
        "alt": bild.alt,
        "breite": bild.width,
        "hoehe": bild.height,
        "kb": round(bild.size / 1000),
        "typ": bild.content_type,
    }


# --- Darstellung fuer MCP-Dienst und Oberflaeche ------------------------------


def vorschau_adresse(post: BlogPost) -> str:
    """Adresse, unter der der Beitrag im Browser anzusehen ist."""
    if post.status == STATUS_PUBLISHED:
        return absolut(f"/blog/{post.slug}")
    return absolut(f"/blog/{post.slug}?vorschau={post.preview_key}")


def kurz(post: BlogPost) -> dict:
    return {
        "slug": post.slug,
        "titel": post.title,
        "status": post.status,
        "hauptbegriff": post.keyword,
        "themen": post.tags or [],
        "titelbild": post.cover or "",
        "worte": post.words,
        "adresse": absolut(f"/blog/{post.slug}"),
        "veroeffentlicht_am": post.published_at.isoformat() if post.published_at else None,
        "geaendert_am": post.updated_at.isoformat() if post.updated_at else None,
    }


def voll(post: BlogPost) -> dict:
    return {
        **kurz(post),
        "beschreibung": post.description,
        "autor": post.author,
        "faq": post.faq or [],
        "vorschau": vorschau_adresse(post),
        "pruefung": seo_pruefung(post),
        "body_markdown": post.body_md,
    }


# --- Verweise zwischen Beitraegen ---------------------------------------------


def _ausschnitt(text: str, begriff: str, breite: int = 90) -> str:
    stelle = text.casefold().find(begriff.casefold())
    if stelle < 0:
        return ""
    anfang, ende = max(0, stelle - breite), min(len(text), stelle + len(begriff) + breite)
    return ("… " if anfang else "") + text[anfang:ende].strip() + (" …" if ende < len(text) else "")


async def erwaehnungen(db: AsyncSession, begriff: str, ohne_slug: str = "") -> list[dict]:
    """Veroeffentlichte Beitraege, die einen Begriff nennen.

    Das ist die Suche ``site:… "begriff"`` fuer den eigenen Blog: Wer einen
    Beitrag auf einen Begriff ausrichten will, verlinkt ihn von den Stellen,
    an denen der Begriff schon steht.
    """
    begriff = _zeile(begriff, "keyword", 120, pflicht=True)
    treffer: list[dict] = []
    for post in await veroeffentlichte(db, mit_text=True):
        if post.slug == ohne_slug:
            continue
        text = reiner_text(post.body_md)
        if not (_enthaelt(text, begriff) or _enthaelt(post.title, begriff)):
            continue
        schon = any(z.rstrip("/").endswith(f"/blog/{ohne_slug}") for z in verweise(post.body_md)) if ohne_slug else False
        treffer.append({
            "slug": post.slug,
            "titel": post.title,
            "adresse": f"/blog/{post.slug}",
            "ausschnitt": _ausschnitt(text, begriff) or post.title,
            "verlinkt_schon": schon,
        })
    return treffer


def verwandte(post: BlogPost, andere: list[BlogPost], limit: int = 3) -> list[BlogPost]:
    """Beitraege mit gemeinsamen Themen, die meisten Ueberschneidungen zuerst."""
    eigene = {t.casefold() for t in (post.tags or [])}
    bewertet = []
    for a in andere:
        if a.slug == post.slug:
            continue
        gemeinsam = len(eigene & {t.casefold() for t in (a.tags or [])})
        bewertet.append((gemeinsam, a.published_at or a.created_at, a))
    bewertet.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [a for _, _, a in bewertet[:limit]]


# --- Vorschau fuer Entwuerfe --------------------------------------------------


def vorschau_gueltig(post: BlogPost, schluessel: str) -> bool:
    """Ob ein Schluessel die Vorschau dieses Entwurfs oeffnet.

    Jeder Beitrag hat einen eigenen zufaelligen Schluessel. Er ist NICHT vom
    Schluessel des MCP-Dienstes abgeleitet (der darf nie in einer Adresse
    stehen und soll sich aus einer Vorschau-Adresse auch nicht erraten lassen)
    und wird beim Zurueckziehen eines Beitrags neu vergeben.
    """
    erwartet = (post.preview_key or "").strip()
    gegeben = (schluessel or "").strip()
    return len(erwartet) >= 24 and hmac.compare_digest(erwartet.encode(), gegeben.encode())
