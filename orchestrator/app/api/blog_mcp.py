"""MCP-Dienst des Blogs.

Ueber diesen Dienst werden Blogbeitraege geschrieben, geprueft und
veroeffentlicht — von jedem MCP-Client aus (Claude Code, Cursor, n8n) und,
wenn ein Administrator den Dienst unter Integrationen eintraegt, von den
Agenten der Plattform selbst, in allen Laufzeiten gleich.

Endpunkt: POST /api/v1/mcp/blog
Anmeldung: Authorization: Bearer <BLOG_MCP_TOKEN>

Derselbe Aufbau wie der Dienst je Second Brain (``brain_mcp.py``); Umschlag
und Protokollversion kommen aus ``mcp_agent.py`` — eine MCP-Umsetzung, keine
zweite daneben. Ohne ``BLOG_ENABLED`` oder ohne ausreichend langen Schluessel
antwortet der Endpunkt mit 404: auf Anlagen ohne Blog gibt es ihn nicht.
"""

import hmac
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.mcp_agent import (
    MCP_PROTOCOL_VERSION,
    _mcp_error,
    _mcp_result,
    _tool_result,
)
from app.core import blog
from app.db.session import get_db
from app.models.blog_post import STATUS_PUBLISHED

router = APIRouter(prefix="/mcp", tags=["mcp-blog"])
logger = logging.getLogger(__name__)

ARBEITSWEISE = """So entsteht ein Beitrag, der gefunden wird:

1. THEMENKARTE. Zu einem Thema alle Fragen sammeln, die Menschen dazu stellen,
   und JE FRAGE EINEN Beitrag schreiben. Wer alle Fragen eines Themas
   beantwortet, gilt als Fachquelle. Ein Beitrag beantwortet genau eine Frage;
   die Frage (oder ihr Kern) ist der Hauptbegriff (keyword).

2. ERFAHRUNG ERFRAGEN. Vor dem Schreiben dem Menschen, in dessen Namen der
   Beitrag erscheint, höchstens zehn Fragen stellen: eigene Erfahrung,
   Fachwissen, Fallbeispiele, Zahlen, Meinung. Ohne diese Antworten entsteht
   ein Text, den es schon hundertmal gibt. Nichts erfinden — keine Zahlen,
   keine Kunden, keine Zitate.

3. SCHREIBEN. Die Antwort steht im ersten Absatz. Danach Zwischenüberschriften
   (##), kurze Absätze, Listen und Tabellen, wo sie helfen. Der Hauptbegriff
   steht im Titel, in der Beschreibung, in den ersten 150 Wörtern und in einer
   Zwischenüberschrift. Titel bis 60 Zeichen, Beschreibung 120 bis 160.
   Am Ende drei bis fünf Nebenfragen als faq.

4. BILDER. Jeder Beitrag bekommt ein Titelbild im Querformat (1200x630) und
   mindestens ein Bild im Text: eine Grafik, die den Kern zeigt, oder ein
   echtes Bildschirmfoto. Hochladen mit blog_upload_image, im Text einfügen als
   ![Was zu sehen ist](/blog/media/name.png), Titelbild über das Feld cover.
   Keine fremden Bilder, keine erfundenen Zahlen in Grafiken, keine echten
   Personen- oder Kundendaten auf Bildschirmfotos.

5. VERLINKEN. blog_find_mentions mit dem Hauptbegriff aufrufen: Es zeigt die
   veröffentlichten Beiträge, die den Begriff schon nennen. Etwa drei davon
   öffnen und den Begriff dort auf den neuen Beitrag verlinken (blog_save_post
   mit dem geänderten Text). Im neuen Beitrag selbst mindestens zwei Verweise
   auf eigene Seiten setzen, z. B. [Text](/blog/andere-adresse).

6. PRÜFEN UND VERÖFFENTLICHEN. blog_save_post legt einen Entwurf an und meldet,
   was fehlt. Die Vorschau-Adresse im Browser ansehen. blog_publish stellt den
   Beitrag online; solange Fehler offen sind, lehnt es ab.

Format: Markdown ohne HTML (HTML erscheint als Text). Keine Überschrift erster
Ordnung im Text — sie gehört dem Titel. Die Adresse (slug) ändert sich nach der
Veröffentlichung nicht mehr."""

_BEITRAG = {
    "slug": {
        "type": "string",
        "description": "Adresse unter /blog/: Kleinbuchstaben, Ziffern, Bindestriche (z. B. 'ki-agenten-selbst-hosten'). Bei einem neuen Beitrag ohne slug wird er aus dem Titel gebildet.",
    },
    "title": {"type": "string", "description": "Titel und Hauptüberschrift, möglichst 30 bis 60 Zeichen, mit dem Hauptbegriff."},
    "description": {"type": "string", "description": "Beschreibung für Suchtreffer und Vorschaukarten, 120 bis 160 Zeichen, mit dem Hauptbegriff."},
    "keyword": {"type": "string", "description": "Hauptbegriff oder Frage, auf die der Beitrag gefunden werden soll."},
    "body_markdown": {"type": "string", "description": "Der Beitrag in Markdown, ohne HTML und ohne Überschrift erster Ordnung."},
    "tags": {"type": "array", "items": {"type": "string"}, "description": "Bis zu 8 Themen; verbinden den Beitrag mit verwandten."},
    "faq": {
        "type": "array",
        "description": "Nebenfragen mit kurzer Antwort; erscheinen als eigener Abschnitt und als strukturierte Daten.",
        "items": {
            "type": "object",
            "properties": {"frage": {"type": "string"}, "antwort": {"type": "string"}},
            "required": ["frage", "antwort"],
        },
    },
    "author": {"type": "string", "description": "Name unter dem Beitrag. Leer: der Standard der Anlage."},
    "cover": {"type": "string", "description": "Name des Titelbilds (aus blog_upload_image), z. B. 'was-ist-ein-ki-agent.png'. Querformat 1200x630 passt am besten. Leer: kein Titelbild."},
}

_SLUG = {"slug": {"type": "string", "description": "Adresse des Beitrags unter /blog/."}}

BLOG_TOOLS = [
    {
        "name": "blog_writing_guide",
        "description": "Die Arbeitsweise für Blogbeiträge dieser Seite (Themenkarte, Erfahrung erfragen, Aufbau, Verlinkung). VOR dem ersten Beitrag lesen.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "blog_list_posts",
        "description": "Alle Beiträge mit Adresse, Titel, Status, Hauptbegriff und Themen. Zeigt, welche Fragen schon beantwortet sind.",
        "inputSchema": {
            "type": "object",
            "properties": {"status": {"type": "string", "enum": ["draft", "published"], "description": "Nur Entwürfe oder nur veröffentlichte."}},
        },
    },
    {
        "name": "blog_get_post",
        "description": "Einen Beitrag vollständig lesen (Markdown, Angaben, Prüfung, Vorschau-Adresse).",
        "inputSchema": {"type": "object", "properties": _SLUG, "required": ["slug"]},
    },
    {
        "name": "blog_save_post",
        "description": (
            "Beitrag anlegen oder ändern. Ein neuer Beitrag ist ein Entwurf und braucht title und "
            "body_markdown. Beim Ändern bleiben nicht genannte Felder unverändert; ein "
            "veröffentlichter Beitrag bleibt veröffentlicht und zeigt die Änderung sofort. "
            "Antwortet mit der Prüfung (Fehler, Hinweise) und der Vorschau-Adresse."
        ),
        "inputSchema": {"type": "object", "properties": _BEITRAG},
    },
    {
        "name": "blog_seo_check",
        "description": "Prüft einen Beitrag: Titel- und Beschreibungslänge, Hauptbegriff an den wichtigen Stellen, Gliederung, Umfang, Verweise auf eigene Seiten.",
        "inputSchema": {"type": "object", "properties": _SLUG, "required": ["slug"]},
    },
    {
        "name": "blog_find_mentions",
        "description": (
            "Veröffentlichte Beiträge, die einen Begriff nennen — mit Textausschnitt und dem Hinweis, "
            "ob sie schon auf den Zielbeitrag verweisen. Dient der Verlinkung: den Begriff an etwa "
            "drei dieser Stellen auf den Beitrag verlinken, der für ihn gefunden werden soll."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "Begriff, nach dem gesucht wird."},
                "target_slug": {"type": "string", "description": "Beitrag, der für den Begriff gefunden werden soll; er selbst wird ausgelassen."},
            },
            "required": ["keyword"],
        },
    },
    {
        "name": "blog_upload_image",
        "description": (
            "Bild in den Blog laden: PNG, JPEG oder WebP bis 1,5 MB (kein SVG). Antwortet mit der Markdown-Zeile "
            "zum Einfügen in den Text. Dasselbe Bild dient als Titelbild, wenn sein Name bei blog_save_post als "
            "cover angegeben wird. Ein vorhandener Name wird ersetzt."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Dateiname: Kleinbuchstaben, Ziffern, Bindestriche plus .png, .jpg oder .webp."},
                "data_base64": {"type": "string", "description": "Die Bilddatei, Base64-kodiert."},
                "alt": {"type": "string", "description": "Was auf dem Bild zu sehen ist — für Screenreader und Suchmaschinen."},
            },
            "required": ["name", "data_base64"],
        },
    },
    {
        "name": "blog_list_images",
        "description": "Alle Bilder des Blogs mit Name, Abmessungen, Größe und der Markdown-Zeile zum Einfügen.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "blog_delete_image",
        "description": "Bild löschen. Lehnt ab, solange ein Beitrag es als Titelbild oder im Text verwendet.",
        "inputSchema": {"type": "object", "properties": {"name": {"type": "string", "description": "Dateiname des Bildes."}}, "required": ["name"]},
    },
    {
        "name": "blog_publish",
        "description": "Beitrag veröffentlichen: erscheint in Übersicht, Feed und Sitemap. Lehnt ab, solange die Prüfung Fehler meldet.",
        "inputSchema": {"type": "object", "properties": _SLUG, "required": ["slug"]},
    },
    {
        "name": "blog_unpublish",
        "description": "Beitrag zurück in den Entwurf nehmen; er ist danach nicht mehr öffentlich erreichbar.",
        "inputSchema": {"type": "object", "properties": _SLUG, "required": ["slug"]},
    },
    {
        "name": "blog_delete_post",
        "description": "Beitrag endgültig löschen. Bei veröffentlichten Beiträgen nur mit confirm=true.",
        "inputSchema": {
            "type": "object",
            "properties": {**_SLUG, "confirm": {"type": "boolean", "description": "Muss true sein, um einen veröffentlichten Beitrag zu löschen."}},
            "required": ["slug"],
        },
    },
]


def _auth(request: Request) -> None:
    """Dienst vorhanden und Schluessel richtig — sonst gibt es ihn nicht bzw. 401."""
    erwartet = blog.mcp_token()
    if not blog.blog_aktiv() or not erwartet:
        raise HTTPException(status_code=404, detail="Not found")
    kopf = request.headers.get("Authorization", "")
    gegeben = kopf.removeprefix("Bearer ").strip() if kopf.startswith("Bearer ") else ""
    if not gegeben or not hmac.compare_digest(gegeben.encode(), erwartet.encode()):
        raise HTTPException(status_code=401, detail="Invalid or missing Bearer token")


@router.post("/blog")
async def mcp_blog_endpoint(request: Request, db: AsyncSession = Depends(get_db)):
    """MCP Streamable HTTP — nimmt alle JSON-RPC-Anfragen der Clients entgegen."""
    _auth(request)

    # Erst die Groesse, dann der Inhalt: ohne Grenze koennte eine einzige
    # Anfrage den Arbeitsspeicher des Dienstes fuellen.
    try:
        angekuendigt = int(request.headers.get("content-length") or 0)
    except ValueError:
        angekuendigt = 0
    if angekuendigt > blog.MAX_MCP_ANFRAGE:
        raise HTTPException(status_code=413, detail="Request too large")
    roh = bytearray()
    async for stueck in request.stream():
        roh.extend(stueck)
        if len(roh) > blog.MAX_MCP_ANFRAGE:
            raise HTTPException(status_code=413, detail="Request too large")
    try:
        body = json.loads(bytes(roh))
    except ValueError:
        return JSONResponse(_mcp_error(None, -32700, "Parse error: invalid JSON"), status_code=200)

    if isinstance(body, list):
        antworten = []
        for req in body[:50]:
            antwort = await _handle_rpc(req, db) if isinstance(req, dict) else _mcp_error(None, -32600, "Invalid request")
            if antwort is not None:
                antworten.append(antwort)
        return JSONResponse(antworten)

    if not isinstance(body, dict):
        return JSONResponse(_mcp_error(None, -32600, "Invalid request"), status_code=200)
    antwort = await _handle_rpc(body, db)
    if antwort is None:
        return JSONResponse(None, status_code=202)  # Benachrichtigung — keine Antwort
    return JSONResponse(antwort)


async def _handle_rpc(req: dict, db: AsyncSession):
    rpc_id = req.get("id")
    method = req.get("method", "")
    params = req.get("params") or {}
    if not isinstance(params, dict):
        return _mcp_error(rpc_id, -32602, "Invalid params")

    if rpc_id is None:
        return None

    if method == "initialize":
        return _mcp_result(rpc_id, {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "ai-employee-blog", "version": "1.0.0"},
            "instructions": ARBEITSWEISE,
        })

    if method == "ping":
        return _mcp_result(rpc_id, {})

    if method == "tools/list":
        return _mcp_result(rpc_id, {"tools": BLOG_TOOLS})

    if method == "tools/call":
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            return _mcp_result(rpc_id, _tool_result("Fehler: arguments muss ein Objekt sein.", is_error=True))
        try:
            ergebnis = await _call_tool(str(params.get("name") or ""), args, db)
        except blog.BlogFehler as e:
            await db.rollback()
            ergebnis = _tool_result(f"Fehler: {e}", is_error=True)
        except Exception:  # noqa: BLE001
            # Ein unerwarteter Fehler in einem Werkzeug darf weder die uebrigen
            # Anfragen eines Stapels abbrechen noch Einzelheiten nach aussen geben.
            await db.rollback()
            logger.exception("blog-mcp: Werkzeug %s fehlgeschlagen", str(params.get("name"))[:60])
            ergebnis = _tool_result("Fehler: Das Werkzeug konnte nicht ausgeführt werden.", is_error=True)
        return _mcp_result(rpc_id, ergebnis)

    return _mcp_error(rpc_id, -32601, f"Method not found: {method}")


def _json(daten) -> dict:
    return _tool_result(json.dumps(daten, ensure_ascii=False, indent=2))


async def _call_tool(name: str, args: dict, db: AsyncSession) -> dict:
    if name == "blog_writing_guide":
        return _tool_result(ARBEITSWEISE)

    if name == "blog_list_posts":
        posts = await blog.alle(db, str(args.get("status") or "").strip() or None)
        return _json({"anzahl": len(posts), "beitraege": [blog.kurz(p) for p in posts]})

    if name == "blog_save_post":
        erlaubt = {k: v for k, v in args.items() if k in _BEITRAG}
        post, neu = await blog.speichere(db, erlaubt)
        logger.info("blog_save_post slug=%s neu=%s status=%s", post.slug, neu, post.status)
        return _json({
            "ergebnis": "angelegt (Entwurf)" if neu else f"geändert ({post.status})",
            **blog.kurz(post),
            "vorschau": blog.vorschau_adresse(post),
            "pruefung": blog.seo_pruefung(post),
        })

    if name == "blog_upload_image":
        bild, neu = await blog.speichere_bild(
            db, args.get("name"), blog.bild_aus_base64(args.get("data_base64")), args.get("alt") or "")
        logger.info("blog_upload_image name=%s neu=%s bytes=%s", bild.name, neu, bild.size)
        return _json({"ergebnis": "hochgeladen" if neu else "ersetzt", **blog.bild_kurz(bild)})

    if name == "blog_list_images":
        liste = await blog.bilder(db)
        return _json({"anzahl": len(liste), "bilder": [blog.bild_kurz(b) for b in liste]})

    if name == "blog_delete_image":
        await blog.loesche_bild(db, args.get("name"))
        logger.info("blog_delete_image name=%s", str(args.get("name"))[:120])
        return _tool_result(f"Gelöscht: {args.get('name')}")

    slug = str(args.get("slug") or args.get("target_slug") or "").strip()

    if name == "blog_find_mentions":
        treffer = await blog.erwaehnungen(db, str(args.get("keyword") or ""), ohne_slug=slug)
        return _json({"anzahl": len(treffer), "treffer": treffer})

    if name in ("blog_get_post", "blog_seo_check"):
        post = await blog.hole(db, blog.pruefe_slug(slug))
        if not post:
            return _tool_result(f"Kein Beitrag mit der Adresse „{slug}“.", is_error=True)
        if name == "blog_seo_check":
            return _json({"slug": post.slug, "status": post.status, "pruefung": blog.seo_pruefung(post)})
        return _json(blog.voll(post))

    if name == "blog_publish":
        post, pruefung = await blog.veroeffentliche(db, slug)
        logger.info("blog_publish slug=%s", post.slug)
        return _json({"ergebnis": "veröffentlicht", **blog.kurz(post), "pruefung": pruefung})

    if name == "blog_unpublish":
        post = await blog.ziehe_zurueck(db, slug)
        logger.info("blog_unpublish slug=%s", post.slug)
        return _json({"ergebnis": "zurück im Entwurf", **blog.kurz(post), "vorschau": blog.vorschau_adresse(post)})

    if name == "blog_delete_post":
        post = await blog.hole(db, blog.pruefe_slug(slug))
        if not post:
            return _tool_result(f"Kein Beitrag mit der Adresse „{slug}“.", is_error=True)
        if post.status == STATUS_PUBLISHED and args.get("confirm") is not True:
            return _tool_result(
                "Der Beitrag ist veröffentlicht. Zum Löschen confirm=true mitgeben — oder blog_unpublish, wenn er nur offline gehen soll.",
                is_error=True,
            )
        await blog.loesche(db, slug)
        logger.info("blog_delete_post slug=%s", slug)
        return _tool_result(f"Gelöscht: {slug}")

    return _tool_result(f"Unknown tool: {name}", is_error=True)
