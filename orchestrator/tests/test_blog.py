"""Blog der Landingpage: wer schreiben darf, was oeffentlich wird, was im HTML landet.

Der Blog hat zwei Seiten, und beide sind heikel:

1. **Schreiben** geht ueber einen MCP-Dienst mit EINEM Schluessel. Geprueft wird
   die Matrix aus (Blog an / aus) x (Schluessel gesetzt / zu kurz) x (Kopfzeile
   fehlt / falsch / richtig): nur die eine Kombination laesst durch, ein
   abgeschalteter Blog verraet sich nicht (404 statt 401).

2. **Lesen** geht ohne Anmeldung. Geprueft wird die Matrix aus (Entwurf /
   veroeffentlicht) x (ohne / falscher / richtiger Vorschau-Schluessel) — und
   dass ein Entwurf in Uebersicht, Feed, Sitemap und Startseiten-Abschnitt fehlt.

Dazu: Der Text eines Beitrags geht ungefiltert an jeden Besucher. Rohes HTML,
``javascript:``-Verweise und Skript-Enden in Titel oder Fragen duerfen nicht
als Code im Dokument ankommen.

Gegen echtes SQL (SQLite im Speicher) und ueber die echten Endpunkt-Funktionen.
"""

import base64
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import blog_admin, blog_mcp, blog_public
from app.config import settings
from app.core import blog
from app.models.audit_log import AuditLog
from app.models.base import Base
from app.models.blog_image import BlogImage
from app.models.blog_post import STATUS_DRAFT, STATUS_PUBLISHED, BlogPost

TOKEN = "t" * 40

# Ein echtes 1x1-PNG und ein kleinstes JPEG (mit Abmessungen 3x2 im Kopf).
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")
JPG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xc0\x00\x11\x08\x00\x02\x00\x03\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01\xff\xd9"

LANGER_TEXT = (
    "KI-Agenten selbst hosten heißt, die Plattform auf eigenen Servern zu betreiben.\n\n"
    "## Was KI-Agenten selbst hosten bedeutet\n\n"
    + ("Ein Absatz mit genug Wörtern, damit der Beitrag eine Frage wirklich beantwortet. " * 45)
    + "\n\nMehr dazu im [Überblick](/blog/ueberblick) und auf der [Startseite](/).\n\n"
    "## Was es kostet\n\n"
    + ("Noch ein Absatz, der die Kosten erklärt und Beispiele nennt. " * 20)
)


def _anfrage(body, token: str | None = TOKEN, kopf: str | None = None):
    kopfzeilen = {}
    if kopf is not None:
        kopfzeilen["Authorization"] = kopf
    elif token is not None:
        kopfzeilen["Authorization"] = f"Bearer {token}"

    roh = body if isinstance(body, bytes) else json.dumps(body).encode()

    async def _stream():
        # In Stuecken, wie ein echter Anfragekoerper ankommt.
        mitte = len(roh) // 2
        yield roh[:mitte]
        yield roh[mitte:]

    return SimpleNamespace(headers=kopfzeilen, stream=_stream)


def _aufruf(werkzeug: str, **args) -> dict:
    return {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": werkzeug, "arguments": args}}


class _MitDatenbank(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=[BlogPost.__table__, BlogImage.__table__, AuditLog.__table__]))
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        self._patches = [
            patch.object(settings, "blog_enabled", True),
            patch.object(settings, "blog_mcp_token", TOKEN),
            patch.object(settings, "blog_base_url", "https://example.com"),
            patch.object(settings, "blog_analytics_src", ""),
            patch.object(settings, "blog_analytics_id", ""),
        ]
        for p in self._patches:
            p.start()

    async def asyncTearDown(self):
        for p in self._patches:
            p.stop()
        await self.engine.dispose()

    async def _mcp(self, werkzeug: str, **args):
        """Ein Werkzeug ueber den echten Endpunkt aufrufen; liefert (Text, ist_fehler)."""
        async with self.Session() as db:
            antwort = await blog_mcp.mcp_blog_endpoint(_anfrage(_aufruf(werkzeug, **args)), db)
        ergebnis = json.loads(antwort.body)["result"]
        return ergebnis["content"][0]["text"], ergebnis["isError"]

    async def _beitrag(self, slug="selbst-hosten", veroeffentlicht=False, **mehr):
        angaben = dict(
            slug=slug,
            title="KI-Agenten selbst hosten: So geht es",
            description="KI-Agenten selbst hosten: was du brauchst, was es kostet und worauf du beim Betrieb auf eigenen Servern achten musst.",
            keyword="KI-Agenten selbst hosten",
            body_markdown=LANGER_TEXT,
            tags=["Betrieb", "Self-Hosting"],
            faq=[{"frage": "Brauche ich eine GPU?", "antwort": "Nein, die Modelle laufen über eine Schnittstelle."}],
        )
        angaben.update(mehr)
        text, fehler = await self._mcp("blog_save_post", **angaben)
        self.assertFalse(fehler, text)
        if veroeffentlicht:
            text, fehler = await self._mcp("blog_publish", slug=slug)
            self.assertFalse(fehler, text)
        return json.loads(text)


class McpZugangTest(_MitDatenbank):
    """Nur Blog an + gueltiger Schluessel + richtige Kopfzeile laesst durch."""

    async def _status(self, **anfrage) -> int:
        async with self.Session() as db:
            try:
                antwort = await blog_mcp.mcp_blog_endpoint(
                    _anfrage({"jsonrpc": "2.0", "id": 1, "method": "ping"}, **anfrage), db)
            except HTTPException as e:
                return e.status_code
        return antwort.status_code

    async def test_richtiger_schluessel_kommt_durch(self):
        self.assertEqual(await self._status(), 200)

    async def test_ohne_kopfzeile_401(self):
        self.assertEqual(await self._status(token=None), 401)

    async def test_falscher_schluessel_401(self):
        self.assertEqual(await self._status(token="x" * 40), 401)

    async def test_schluessel_ohne_bearer_401(self):
        self.assertEqual(await self._status(kopf=TOKEN), 401)

    async def test_abgeschalteter_blog_verraet_sich_nicht(self):
        with patch.object(settings, "blog_enabled", False):
            self.assertEqual(await self._status(), 404)

    async def test_zu_kurzer_schluessel_schaltet_den_dienst_nicht_frei(self):
        # Auch wer den kurzen Wert kennt, kommt nicht hinein.
        with patch.object(settings, "blog_mcp_token", "kurz"):
            self.assertEqual(await self._status(token="kurz"), 404)

    async def test_leerer_schluessel_laesst_leere_kopfzeile_nicht_durch(self):
        with patch.object(settings, "blog_mcp_token", ""):
            self.assertEqual(await self._status(kopf="Bearer "), 404)


class McpWerkzeugeTest(_MitDatenbank):
    async def test_neuer_beitrag_ist_entwurf_mit_vorschau(self):
        daten = await self._beitrag()
        self.assertEqual(daten["status"], STATUS_DRAFT)
        self.assertIn("/blog/selbst-hosten?vorschau=", daten["vorschau"])
        self.assertNotIn(TOKEN, daten["vorschau"])
        self.assertGreaterEqual(len(daten["vorschau"].split("vorschau=")[1]), 24)
        self.assertEqual(daten["pruefung"]["fehler"], [])

    async def test_aendern_laesst_nicht_genannte_felder_stehen(self):
        await self._beitrag(veroeffentlicht=True)
        text, fehler = await self._mcp("blog_save_post", slug="selbst-hosten", title="Neuer Titel für den Beitrag hier")
        self.assertFalse(fehler)
        async with self.Session() as db:
            post = await blog.hole(db, "selbst-hosten")
        self.assertEqual(post.title, "Neuer Titel für den Beitrag hier")
        self.assertEqual(post.status, STATUS_PUBLISHED)
        self.assertEqual(post.keyword, "KI-Agenten selbst hosten")
        self.assertEqual(post.tags, ["Betrieb", "Self-Hosting"])

    async def test_status_laesst_sich_nicht_ueber_speichern_setzen(self):
        await self._beitrag(status="published", published_at="2020-01-01")
        async with self.Session() as db:
            post = await blog.hole(db, "selbst-hosten")
        self.assertEqual(post.status, STATUS_DRAFT)
        self.assertIsNone(post.published_at)

    async def test_duenner_beitrag_wird_nicht_veroeffentlicht(self):
        await self._beitrag(slug="duenn", body_markdown="Zu wenig Text.", description="", keyword="")
        text, fehler = await self._mcp("blog_publish", slug="duenn")
        self.assertTrue(fehler)
        self.assertIn("Noch nicht veröffentlicht", text)
        async with self.Session() as db:
            self.assertEqual((await blog.hole(db, "duenn")).status, STATUS_DRAFT)

    async def test_unzulaessige_adressen_werden_abgelehnt(self):
        for slug in ("Mit Leerzeichen", "../etc/passwd", "feed", "a--b", "-rand", "x" * 81, "ümlaut"):
            text, fehler = await self._mcp("blog_save_post", slug=slug, title="Ein Titel", body_markdown="Text")
            self.assertTrue(fehler, slug)

    async def test_veroeffentlichter_beitrag_laesst_sich_nicht_kaputtspeichern(self):
        # Eine Aenderung an einem veroeffentlichten Beitrag ist sofort oeffentlich —
        # sie darf ihn nicht unter die Schwelle druecken, die fuer die Veroeffentlichung galt.
        await self._beitrag(veroeffentlicht=True)
        text, fehler = await self._mcp("blog_save_post", slug="selbst-hosten", body_markdown="Nur noch ein Satz.")
        self.assertTrue(fehler)
        self.assertIn("Nicht gespeichert", text)
        async with self.Session() as db:
            post = await blog.hole(db, "selbst-hosten")
        self.assertEqual(post.body_md, LANGER_TEXT.strip())
        self.assertGreater(post.words, 700)
        # Als Entwurf geht dieselbe Aenderung durch.
        await self._mcp("blog_unpublish", slug="selbst-hosten")
        text, fehler = await self._mcp("blog_save_post", slug="selbst-hosten", body_markdown="Nur noch ein Satz.")
        self.assertFalse(fehler, text)

    async def test_gleichzeitiges_anlegen_endet_mit_absage_statt_absturz(self):
        from sqlalchemy.exc import IntegrityError

        async with self.Session() as db:
            with patch.object(db, "commit", side_effect=IntegrityError("x", {}, Exception("doppelt"))):
                with self.assertRaises(blog.BlogFehler) as ctx:
                    await blog.speichere(db, {"slug": "doppelt", "title": "T", "body_markdown": "B"})
        self.assertIn("gibt es schon", str(ctx.exception))

    async def test_gleicher_titel_ohne_adresse_ueberschreibt_nichts(self):
        await self._beitrag(slug="ein-titel-fuer-den-beitrag", title="Ein Titel für den Beitrag", veroeffentlicht=True)
        text, fehler = await self._mcp("blog_save_post", title="Ein Titel für den Beitrag", body_markdown="## X\n\nfremd")
        self.assertTrue(fehler)
        async with self.Session() as db:
            self.assertNotIn("fremd", (await blog.hole(db, "ein-titel-fuer-den-beitrag")).body_md)

    async def test_steuerzeichen_werden_entfernt_und_der_feed_bleibt_gueltig(self):
        from xml.dom import minidom
        await self._beitrag(veroeffentlicht=True, title="KI-Agenten selbst\x01 hosten\x00: So geht es",
                            body_markdown=LANGER_TEXT + "\x00\x07")
        async with self.Session() as db:
            post = await blog.hole(db, "selbst-hosten")
            feed = (await blog_public.blog_feed(db=db)).body.decode()
        self.assertEqual(post.title, "KI-Agenten selbst hosten: So geht es")
        self.assertNotIn("\x00", post.body_md)
        minidom.parseString(feed)

    async def test_falsche_typen_sind_fehler_keine_abstuerze(self):
        for werkzeug, args in (("blog_get_post", {"slug": 5}), ("blog_list_posts", {"status": 5}),
                               ("blog_save_post", {"slug": ["a"], "title": {"x": 1}, "body_markdown": 3})):
            text, fehler = await self._mcp(werkzeug, **args)
            self.assertTrue(fehler, werkzeug)
        async with self.Session() as db:
            antwort = await blog_mcp.mcp_blog_endpoint(_anfrage(
                {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": ["liste"]}), db)
        self.assertIn("error", json.loads(antwort.body))

    async def test_wortzahl_wird_beim_speichern_gezaehlt(self):
        await self._beitrag()
        async with self.Session() as db:
            post = await blog.hole(db, "selbst-hosten")
        self.assertEqual(post.words, blog.wortzahl(post.body_md))
        self.assertGreater(post.words, 700)

    async def test_adresse_entsteht_aus_dem_titel(self):
        text, fehler = await self._mcp("blog_save_post", title="Was kostet ein KI-Mitarbeiter für Büros?", body_markdown="Text")
        self.assertFalse(fehler, text)
        self.assertEqual(json.loads(text)["slug"], "was-kostet-ein-ki-mitarbeiter-fuer-bueros")

    async def test_veroeffentlichtes_loeschen_braucht_bestaetigung(self):
        await self._beitrag(veroeffentlicht=True)
        text, fehler = await self._mcp("blog_delete_post", slug="selbst-hosten")
        self.assertTrue(fehler)
        text, fehler = await self._mcp("blog_delete_post", slug="selbst-hosten", confirm=True)
        self.assertFalse(fehler)
        async with self.Session() as db:
            self.assertIsNone(await blog.hole(db, "selbst-hosten"))

    async def test_erwaehnungen_finden_nur_veroeffentlichte_und_melden_vorhandene_verweise(self):
        await self._beitrag(slug="ueberblick", veroeffentlicht=True,
                            body_markdown=LANGER_TEXT + "\n\nSiehe [Betrieb](/blog/ziel) für mehr.")
        await self._beitrag(slug="zweiter", veroeffentlicht=True)
        await self._beitrag(slug="entwurf")
        await self._beitrag(slug="ziel", veroeffentlicht=True)
        text, fehler = await self._mcp("blog_find_mentions", keyword="ki-agenten SELBST hosten", target_slug="ziel")
        self.assertFalse(fehler, text)
        treffer = {t["slug"]: t for t in json.loads(text)["treffer"]}
        self.assertEqual(set(treffer), {"ueberblick", "zweiter"})
        self.assertTrue(treffer["ueberblick"]["verlinkt_schon"])
        self.assertFalse(treffer["zweiter"]["verlinkt_schon"])
        self.assertIn("selbst hosten", treffer["zweiter"]["ausschnitt"])

    async def test_unbekanntes_werkzeug_und_falsche_argumente_sind_fehler_keine_abstuerze(self):
        text, fehler = await self._mcp("gibt_es_nicht")
        self.assertTrue(fehler)
        async with self.Session() as db:
            antwort = await blog_mcp.mcp_blog_endpoint(_anfrage({
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "blog_save_post", "arguments": "kein objekt"}}), db)
        self.assertTrue(json.loads(antwort.body)["result"]["isError"])
        async with self.Session() as db:
            antwort = await blog_mcp.mcp_blog_endpoint(_anfrage("nur text"), db)
        self.assertIn("error", json.loads(antwort.body))

    async def test_werkzeugliste_und_arbeitsweise(self):
        async with self.Session() as db:
            antwort = await blog_mcp.mcp_blog_endpoint(_anfrage({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}), db)
        namen = {t["name"] for t in json.loads(antwort.body)["result"]["tools"]}
        self.assertEqual(namen, {
            "blog_writing_guide", "blog_list_posts", "blog_get_post", "blog_save_post", "blog_seo_check",
            "blog_find_mentions", "blog_publish", "blog_unpublish", "blog_delete_post",
            "blog_upload_image", "blog_list_images", "blog_delete_image"})
        text, _ = await self._mcp("blog_writing_guide")
        self.assertIn("höchstens zehn Fragen", text)


class OeffentlicheSeitenTest(_MitDatenbank):
    async def _seite(self, slug, vorschau=""):
        async with self.Session() as db:
            return await blog_public.blog_beitrag(slug, vorschau=vorschau, db=db)

    async def test_entwurf_ist_ohne_schluessel_nicht_erreichbar(self):
        await self._beitrag()
        self.assertEqual((await self._seite("selbst-hosten")).status_code, 404)
        self.assertEqual((await self._seite("selbst-hosten", vorschau="0" * 32)).status_code, 404)

    async def test_entwurf_mit_vorschau_schluessel_ist_sichtbar_aber_nicht_fuer_suchmaschinen(self):
        daten = await self._beitrag()
        schluessel = daten["vorschau"].split("vorschau=")[1]
        antwort = await self._seite("selbst-hosten", vorschau=schluessel)
        self.assertEqual(antwort.status_code, 200)
        html = antwort.body.decode()
        self.assertIn('content="noindex, nofollow"', html)
        self.assertNotIn("application/ld+json", html)
        self.assertEqual(antwort.headers["x-robots-tag"], "noindex, nofollow")
        self.assertEqual(antwort.headers["cache-control"], "no-store")

    async def test_vorschau_schluessel_gilt_nur_fuer_seinen_beitrag(self):
        a = await self._beitrag(slug="eins")
        await self._beitrag(slug="zwei")
        schluessel = a["vorschau"].split("vorschau=")[1]
        self.assertEqual((await self._seite("zwei", vorschau=schluessel)).status_code, 404)

    async def test_beitrag_ohne_schluessel_hat_keine_vorschau(self):
        # Ein leerer gespeicherter Schluessel darf von einer leeren Angabe nicht "getroffen" werden.
        await self._beitrag()
        async with self.Session() as db:
            post = await blog.hole(db, "selbst-hosten")
            post.preview_key = ""
            await db.commit()
        self.assertEqual((await self._seite("selbst-hosten", vorschau="")).status_code, 404)
        self.assertFalse(blog.vorschau_gueltig(post, ""))

    async def test_zurueckziehen_entwertet_die_alte_vorschau_adresse(self):
        daten = await self._beitrag()
        alt = daten["vorschau"].split("vorschau=")[1]
        await self._mcp("blog_publish", slug="selbst-hosten")
        text, _ = await self._mcp("blog_unpublish", slug="selbst-hosten")
        neu = json.loads(text)["vorschau"].split("vorschau=")[1]
        self.assertNotEqual(alt, neu)
        self.assertEqual((await self._seite("selbst-hosten", vorschau=alt)).status_code, 404)
        self.assertEqual((await self._seite("selbst-hosten", vorschau=neu)).status_code, 200)

    async def test_vorschau_laedt_keine_besucherzaehlung(self):
        # Die Adresse der Vorschau enthaelt den Schluessel — ein Zaehldienst wuerde sie mitschreiben.
        daten = await self._beitrag()
        schluessel = daten["vorschau"].split("vorschau=")[1]
        with patch.object(settings, "blog_analytics_src", "https://stats.example.com/script.js"), \
                patch.object(settings, "blog_analytics_id", "abc"):
            antwort = await self._seite("selbst-hosten", vorschau=schluessel)
        self.assertNotIn("stats.example.com", antwort.body.decode())
        self.assertIn("script-src 'none'", antwort.headers["content-security-policy"])
        self.assertEqual(antwort.headers["referrer-policy"], "no-referrer")

    async def test_veroeffentlichter_beitrag_traegt_alles_fuer_suchmaschinen(self):
        await self._beitrag(veroeffentlicht=True)
        antwort = await self._seite("selbst-hosten")
        self.assertEqual(antwort.status_code, 200)
        html = antwort.body.decode()
        self.assertIn("<title>KI-Agenten selbst hosten: So geht es — AI Employee</title>", html)
        self.assertIn('<link rel="canonical" href="https://example.com/blog/selbst-hosten">', html)
        self.assertEqual(html.count("<h1"), 1)
        self.assertIn('"@type": "BlogPosting"', html)
        self.assertIn('"@type": "FAQPage"', html)
        self.assertIn('"@type": "BreadcrumbList"', html)
        self.assertIn('<h2 id="was-ki-agenten-selbst-hosten-bedeutet">', html)
        self.assertNotIn("noindex", html)

    async def test_abgeschalteter_blog_ist_ueberall_404(self):
        await self._beitrag(veroeffentlicht=True)
        with patch.object(settings, "blog_enabled", False):
            async with self.Session() as db:
                for aufruf in (
                    blog_public.blog_uebersicht(db=db), blog_public.blog_feed(db=db),
                    blog_public.blog_neueste(db=db), blog_public.sitemap(db=db),
                    blog_public.blog_beitrag("selbst-hosten", vorschau="", db=db),
                    blog_public.robots(), blog_public.blog_schrift("sora.woff2"),
                ):
                    with self.assertRaises(HTTPException) as ctx:
                        await aufruf
                    self.assertEqual(ctx.exception.status_code, 404)

    async def test_entwuerfe_fehlen_in_uebersicht_feed_sitemap_und_startseite(self):
        await self._beitrag(slug="oeffentlich", veroeffentlicht=True)
        await self._beitrag(slug="geheim", title="Geheimer Entwurf mit eigenem Titel")
        async with self.Session() as db:
            antworten = [
                await blog_public.blog_uebersicht(db=db), await blog_public.blog_feed(db=db),
                await blog_public.sitemap(db=db), await blog_public.blog_neueste(db=db),
            ]
        for antwort in antworten:
            text = antwort.body.decode()
            self.assertIn("oeffentlich", text)
            self.assertNotIn("geheim", text)
            self.assertNotIn("Geheimer Entwurf", text)

    async def test_schriften_nur_aus_der_festen_liste(self):
        antwort = await blog_public.blog_schrift("sora.woff2")
        self.assertEqual(antwort.status_code, 200)
        for name in ("../templates/base.html", "..%2Fbase.html", "base.html", ""):
            with self.assertRaises(HTTPException):
                await blog_public.blog_schrift(name)

    async def test_oeffentliche_adressen_antworten_auch_auf_head(self):
        # Linkpruefer, Verfuegbarkeitsproben und manche Suchmaschinen fragen mit HEAD.
        pfade = {r.path: r.methods for r in blog_public.router.routes}
        for pfad in ("/blog", "/blog/{slug}", "/blog/feed.xml", "/sitemap.xml", "/robots.txt"):
            self.assertEqual(pfade[pfad], {"GET", "HEAD"}, pfad)

    async def test_datum_gilt_in_der_zeitzone_der_anlage(self):
        from datetime import datetime, timezone

        spaet = datetime(2026, 10, 1, 22, 30, tzinfo=timezone.utc)  # in Berlin schon der 2. Oktober
        with patch.dict("os.environ", {"TZ": "Europe/Berlin"}):
            self.assertEqual(blog_public._datum(spaet), "2. Oktober 2026")
            self.assertEqual(blog_public._tag(spaet), "2026-10-02")
        with patch.dict("os.environ", {"TZ": "Gibt/Es-Nicht"}):
            self.assertEqual(blog_public._tag(spaet), "2026-10-02")

    async def test_robots_nennt_die_sitemap(self):
        self.assertIn("Sitemap: https://example.com/sitemap.xml", (await blog_public.robots()).body.decode())


class KeinFremderCodeTest(_MitDatenbank):
    """Was im Beitrag steht, kommt als Text an — nie als Code."""

    async def test_html_und_gefaehrliche_verweise_im_text(self):
        boese = (
            LANGER_TEXT
            + "\n\n<script>alert('x')</script>\n\n<img src=x onerror=alert(1)>\n\n"
            "[klick](javascript:alert(1)) [daten](data:text/html,<script>1</script>) "
            "![bild](javascript:alert(2))\n"
        )
        await self._beitrag(veroeffentlicht=True, body_markdown=boese)
        async with self.Session() as db:
            html = (await blog_public.blog_beitrag("selbst-hosten", vorschau="", db=db)).body.decode()
        koerper = html.split('<div class="text">')[1].split("</article>")[0]
        self.assertNotIn("<script", koerper)
        self.assertNotIn("<img", koerper)
        self.assertNotIn('href="javascript:', koerper)
        self.assertNotIn('src="javascript:', koerper)
        self.assertNotIn('href="data:', koerper)
        self.assertIn("&lt;script&gt;", koerper)

    async def test_titel_beschreibung_und_fragen_brechen_nicht_aus(self):
        gift = '</title></script><script>alert(1)</script>"><img src=x onerror=alert(1)>'
        await self._beitrag(
            veroeffentlicht=True, title=gift, description=gift, author=gift, tags=[gift[:40]],
            faq=[{"frage": gift, "antwort": gift}],
        )
        async with self.Session() as db:
            seiten = [
                await blog_public.blog_beitrag("selbst-hosten", vorschau="", db=db),
                await blog_public.blog_uebersicht(db=db),
            ]
        for seite in seiten:
            html = seite.body.decode()
            self.assertNotIn("<script>alert(1)</script>", html)
            self.assertNotIn("<img src=x", html)
            # Die strukturierten Daten duerfen ihr Skript-Element nicht verlassen.
            for block in html.split('<script type="application/ld+json">')[1:]:
                daten = block.split("</script>")[0]
                self.assertNotIn("<", daten)
                json.loads(daten)
        async with self.Session() as db:
            for antwort in (await blog_public.blog_feed(db=db), await blog_public.sitemap(db=db)):
                self.assertNotIn("<script>", antwort.body.decode())

    async def test_seite_setzt_eine_enge_richtlinie_ohne_skripte(self):
        await self._beitrag(veroeffentlicht=True)
        async with self.Session() as db:
            antwort = await blog_public.blog_beitrag("selbst-hosten", vorschau="", db=db)
        csp = antwort.headers["content-security-policy"]
        self.assertIn("default-src 'none'", csp)
        self.assertIn("script-src 'none'", csp)
        self.assertIn("frame-ancestors 'none'", csp)

    async def test_besucherzaehlung_nur_als_https_adresse_mit_kennung(self):
        await self._beitrag(veroeffentlicht=True)
        faelle = [
            ("https://stats.example.com/script.js", "abc", True),
            ("http://stats.example.com/script.js", "abc", False),
            ("javascript:alert(1)", "abc", False),
            ("https://stats.example.com/script.js", "", False),
        ]
        for src, kennung, erwartet in faelle:
            with patch.object(settings, "blog_analytics_src", src), patch.object(settings, "blog_analytics_id", kennung):
                async with self.Session() as db:
                    antwort = await blog_public.blog_beitrag("selbst-hosten", vorschau="", db=db)
            html, csp = antwort.body.decode(), antwort.headers["content-security-policy"]
            self.assertEqual("data-website-id" in html, erwartet, src)
            self.assertEqual("script-src https://stats.example.com" in csp, erwartet, src)


class VerwaltungTest(_MitDatenbank):
    """Die Verwaltung in der Oberflaeche: nur Administratoren, dieselben Regeln wie der MCP-Dienst."""

    ADMIN = SimpleNamespace(id="user-admin")

    def test_jeder_endpunkt_verlangt_einen_administrator(self):
        from app.dependencies import require_admin

        routen = [r for r in blog_admin.router.routes if hasattr(r, "dependant")]
        self.assertGreaterEqual(len(routen), 8)
        for route in routen:
            abhaengigkeiten = [d.call for d in route.dependant.dependencies]
            self.assertIn(require_admin, abhaengigkeiten, f"{route.methods} {route.path}")

    async def test_abgeschaltet_gibt_nur_der_status_auskunft(self):
        with patch.object(settings, "blog_enabled", False):
            status = await blog_admin.blog_status(user=self.ADMIN)
            self.assertEqual(status, {"enabled": False, "state": "aus", "base_url": "https://example.com",
                                      "blog_url": None, "mcp_ready": False, "mcp_url": None})
            async with self.Session() as db:
                for aufruf in (
                    blog_admin.blog_posts(user=self.ADMIN, db=db),
                    blog_admin.blog_post_lesen("x", user=self.ADMIN, db=db),
                    blog_admin.blog_post_anlegen(blog_admin.BeitragEingabe(title="T", body_markdown="B"), user=self.ADMIN, db=db),
                    blog_admin.blog_post_veroeffentlichen("x", user=self.ADMIN, db=db),
                    blog_admin.blog_post_loeschen("x", user=self.ADMIN, db=db),
                ):
                    with self.assertRaises(HTTPException) as ctx:
                        await aufruf
                    self.assertEqual(ctx.exception.status_code, 404)

    async def test_ohne_oeffentliche_adresse_bleibt_der_blog_aus(self):
        # Relative Adressen in Sitemap, Feed und Seitenkopf waeren fuer Suchmaschinen ungueltig.
        await self._beitrag(veroeffentlicht=True)
        with patch.object(settings, "blog_base_url", ""), patch.object(settings, "public_app_url", ""):
            status = await blog_admin.blog_status(user=self.ADMIN)
            self.assertEqual((status["enabled"], status["state"]), (False, "ohne_adresse"))
            async with self.Session() as db:
                with self.assertRaises(HTTPException) as ctx:
                    await blog_public.sitemap(db=db)
            self.assertEqual(ctx.exception.status_code, 404)
        with patch.object(settings, "blog_base_url", ""), patch.object(settings, "public_app_url", "https://app.example.com/"):
            self.assertEqual(blog.absolut("/blog"), "https://app.example.com/blog")
            self.assertTrue(blog.blog_aktiv())

    async def test_unbekannte_adresse_ist_ueberall_404(self):
        async with self.Session() as db:
            for aufruf in (blog_admin.blog_post_veroeffentlichen, blog_admin.blog_post_zurueckziehen,
                           blog_admin.blog_post_loeschen, blog_admin.blog_post_lesen):
                for slug in ("gibt-es-nicht", "../x"):
                    with self.assertRaises(HTTPException) as ctx:
                        await aufruf(slug, user=self.ADMIN, db=db)
                    self.assertEqual(ctx.exception.status_code, 404, (aufruf.__name__, slug))

    async def test_status_nennt_den_schluessel_nie(self):
        status = await blog_admin.blog_status(user=self.ADMIN)
        self.assertTrue(status["mcp_ready"])
        self.assertNotIn(TOKEN, json.dumps(status))

    async def test_anlegen_pruefen_veroeffentlichen_zurueckziehen_loeschen(self):
        eingabe = blog_admin.BeitragEingabe(
            title="KI-Agenten selbst hosten: So geht es",
            description="KI-Agenten selbst hosten: was du brauchst, was es kostet und worauf du beim Betrieb auf eigenen Servern achten musst.",
            keyword="KI-Agenten selbst hosten", body_markdown=LANGER_TEXT, tags=["Betrieb"],
            faq=[blog_admin.FaqEintrag(frage="Brauche ich eine GPU?", antwort="Nein.")],
        )
        async with self.Session() as db:
            neu = await blog_admin.blog_post_anlegen(eingabe, user=self.ADMIN, db=db)
            slug = neu["slug"]
            self.assertEqual(slug, "ki-agenten-selbst-hosten-so-geht-es")
            self.assertEqual(neu["status"], STATUS_DRAFT)
            self.assertEqual(neu["faq"], [{"frage": "Brauche ich eine GPU?", "antwort": "Nein."}])
            self.assertEqual(neu["pruefung"]["fehler"], [])

            with self.assertRaises(HTTPException) as ctx:
                await blog_admin.blog_post_anlegen(eingabe, user=self.ADMIN, db=db)
            self.assertEqual(ctx.exception.status_code, 409)

            online = await blog_admin.blog_post_veroeffentlichen(slug, user=self.ADMIN, db=db)
            self.assertEqual(online["status"], STATUS_PUBLISHED)
            self.assertEqual((await blog_public.blog_beitrag(slug, vorschau="", db=db)).status_code, 200)

            zurueck = await blog_admin.blog_post_zurueckziehen(slug, user=self.ADMIN, db=db)
            self.assertEqual(zurueck["status"], STATUS_DRAFT)
            self.assertEqual((await blog_public.blog_beitrag(slug, vorschau="", db=db)).status_code, 404)

            self.assertEqual(await blog_admin.blog_post_loeschen(slug, user=self.ADMIN, db=db),
                             {"deleted": slug, "was_published": False})
            with self.assertRaises(HTTPException) as ctx:
                await blog_admin.blog_post_lesen(slug, user=self.ADMIN, db=db)
            self.assertEqual(ctx.exception.status_code, 404)

    async def test_aendern_haelt_die_adresse_fest_und_kennt_nur_vorhandene(self):
        await self._beitrag()
        async with self.Session() as db:
            geaendert = await blog_admin.blog_post_aendern(
                "selbst-hosten", blog_admin.BeitragEingabe(slug="andere-adresse", title="Ein neuer Titel für den Beitrag"),
                user=self.ADMIN, db=db)
            self.assertEqual(geaendert["slug"], "selbst-hosten")
            self.assertEqual(geaendert["titel"], "Ein neuer Titel für den Beitrag")
            self.assertIsNone(await blog.hole(db, "andere-adresse"))
            for slug in ("gibt-es-nicht", "../x", "UNZULAESSIG"):
                with self.assertRaises(HTTPException) as ctx:
                    await blog_admin.blog_post_aendern(slug, blog_admin.BeitragEingabe(title="T"), user=self.ADMIN, db=db)
                self.assertEqual(ctx.exception.status_code, 404)

    async def test_duenner_beitrag_wird_auch_hier_nicht_veroeffentlicht(self):
        await self._beitrag(slug="duenn", body_markdown="Zu wenig Text.", description="", keyword="")
        async with self.Session() as db:
            with self.assertRaises(HTTPException) as ctx:
                await blog_admin.blog_post_veroeffentlichen("duenn", user=self.ADMIN, db=db)
        self.assertEqual(ctx.exception.status_code, 422)
        self.assertIn("Noch nicht veröffentlicht", ctx.exception.detail)

    async def test_jede_aenderung_steht_im_protokoll_mit_herkunft(self):
        from sqlalchemy import select

        await self._beitrag()  # ueber den MCP-Dienst
        async with self.Session() as db:
            await blog_admin.blog_post_veroeffentlichen("selbst-hosten", user=self.ADMIN, db=db)
            await blog_admin.blog_post_loeschen("selbst-hosten", user=self.ADMIN, db=db)
            zeilen = (await db.execute(select(AuditLog).order_by(AuditLog.id))).scalars().all()
        self.assertEqual([z.event_type for z in zeilen],
                         ["blog_post_saved", "blog_post_published", "blog_post_deleted"])
        self.assertEqual([(z.agent_id, z.user_id, z.meta["ueber"]) for z in zeilen], [
            ("blog-mcp", None, "mcp"), ("admin", "user-admin", "oberflaeche"), ("admin", "user-admin", "oberflaeche")])
        self.assertTrue(all(z.command == "/blog/selbst-hosten" for z in zeilen))


class BilderTest(_MitDatenbank):
    """Bilder: nur echte Rasterbilder, nur aus dem eigenen Bestand, nie als Einfallstor."""

    ADMIN = SimpleNamespace(id="user-admin")

    @staticmethod
    def _abruf(methode="GET", **kopf):
        return SimpleNamespace(method=methode, headers=kopf)

    async def _hoch(self, name="grafik.png", daten=PNG, alt="Eine Grafik"):
        return await self._mcp("blog_upload_image", name=name, data_base64=base64.b64encode(daten).decode(), alt=alt)

    async def test_hochladen_liefert_markdown_und_abmessungen(self):
        text, fehler = await self._hoch()
        self.assertFalse(fehler, text)
        d = json.loads(text)
        self.assertEqual((d["name"], d["breite"], d["hoehe"], d["typ"]), ("grafik.png", 1, 1, "image/png"))
        self.assertEqual(d["markdown"], "![Eine Grafik](/blog/media/grafik.png)")
        text, fehler = await self._hoch(name="foto.jpg", daten=JPG)
        self.assertFalse(fehler, text)
        self.assertEqual((json.loads(text)["breite"], json.loads(text)["hoehe"]), (3, 2))

    async def test_der_inhalt_entscheidet_nicht_die_endung(self):
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        html = b"<html><script>alert(1)</script></html>"
        for name, daten in (("boese.png", svg), ("boese.jpg", html), ("boese.webp", b"RIFF"), ("leer.png", b""),
                            ("falsch.jpg", PNG), ("bild.svg", svg), ("../x.png", PNG), ("GROSS.PNG.exe", PNG),
                            ("a b.png", PNG), ("riesig.png", PNG + b"0" * blog.MAX_BILD)):
            text, fehler = await self._hoch(name=name, daten=daten)
            self.assertTrue(fehler, name)
        text, fehler = await self._mcp("blog_upload_image", name="x.png", data_base64="kein base64 !!")
        self.assertTrue(fehler)
        async with self.Session() as db:
            self.assertEqual(await blog.bilder(db), [])

    async def test_auslieferung_mit_festem_typ_und_nur_fuer_vorhandene_namen(self):
        await self._hoch()
        async with self.Session() as db:
            antwort = await blog_public.blog_bild("grafik.png", self._abruf(), db=db)
            self.assertEqual(antwort.media_type, "image/png")
            self.assertEqual(antwort.body, PNG)
            self.assertEqual(antwort.headers["content-security-policy"], "default-src 'none'; sandbox")
            for name in ("gibt-es-nicht.png", "../grafik.png", "grafik.png/../x", "GRAFIK.PNG", "grafik.png\n", ""):
                with self.assertRaises(HTTPException) as ctx:
                    await blog_public.blog_bild(name, self._abruf(), db=db)
                self.assertEqual(ctx.exception.status_code, 404)
        with patch.object(settings, "blog_enabled", False):
            async with self.Session() as db:
                with self.assertRaises(HTTPException):
                    await blog_public.blog_bild("grafik.png", self._abruf(), db=db)

    async def test_wer_das_bild_schon_hat_bekommt_304_ohne_dass_die_daten_geladen_werden(self):
        await self._hoch()
        async with self.Session() as db:
            erst = await blog_public.blog_bild("grafik.png", self._abruf(), db=db)
            etag = erst.headers["etag"]
        with patch.object(blog, "hole_bild", wraps=blog.hole_bild) as hole:
            async with self.Session() as db:
                wieder = await blog_public.blog_bild("grafik.png", self._abruf(**{"if-none-match": etag}), db=db)
                kopf = await blog_public.blog_bild("grafik.png", self._abruf(methode="HEAD"), db=db)
            self.assertTrue(all(not a.kwargs.get("mit_daten") for a in hole.call_args_list))
        self.assertEqual((wieder.status_code, wieder.body), (304, b""))
        self.assertEqual((kopf.status_code, kopf.body, kopf.headers["content-length"]), (200, b"", str(len(PNG))))
        # Ein ersetztes Bild bekommt eine neue Pruefsumme.
        await self._hoch(daten=PNG + b"\x00")
        async with self.Session() as db:
            neu = await blog_public.blog_bild("grafik.png", self._abruf(**{"if-none-match": etag}), db=db)
        self.assertEqual(neu.status_code, 200)
        self.assertNotEqual(neu.headers["etag"], etag)

    async def test_uebergrosse_oder_unlesbare_abmessungen_werden_abgelehnt(self):
        import struct

        def png(breite, hoehe):
            return PNG[:16] + struct.pack(">II", breite, hoehe) + PNG[24:]

        for breite, hoehe in ((4294967295, 4294967295), (30000, 30000), (6001, 10), (0, 0), (5000, 5000)):
            text, fehler = await self._hoch(name="bombe.png", daten=png(breite, hoehe))
            self.assertTrue(fehler, (breite, hoehe))
        text, fehler = await self._hoch(name="titelbild.png", daten=png(2400, 1260))
        self.assertFalse(fehler, text)

    async def test_zu_grosse_anfrage_wird_abgewiesen_bevor_sie_gelesen_ist(self):
        gross = b"x" * (blog.MAX_MCP_ANFRAGE + 1)
        async with self.Session() as db:
            for anfrage in (_anfrage(gross), SimpleNamespace(
                    headers={"Authorization": f"Bearer {TOKEN}", "content-length": str(10**9)}, stream=None)):
                with self.assertRaises(HTTPException) as ctx:
                    await blog_mcp.mcp_blog_endpoint(anfrage, db)
                self.assertEqual(ctx.exception.status_code, 413)
        text, fehler = await self._mcp("blog_upload_image", name="x.png", data_base64="A" * (blog.MAX_BILD * 4 // 3 + 100))
        self.assertTrue(fehler)
        self.assertIn("größer als", text)

    async def test_fremde_bilder_werden_zu_text_eigene_bekommen_abmessungen(self):
        html, _ = blog.render(
            "![Eigen](/blog/media/grafik.png) ![Fremd](https://anderswo.example/x.png) "
            "![Daten](data:image/png;base64,AAAA) ![Pfad](/api/v1/secrets) ![Trick](/blog/media/../../api/x.png)",
            {"grafik.png": (1200, 630)})
        self.assertEqual(html.count("<img"), 1)
        self.assertIn('src="/blog/media/grafik.png"', html)
        self.assertIn('width="1200" height="630"', html)
        self.assertIn("Fremd", html)
        self.assertNotIn("anderswo.example", html)
        self.assertNotIn("/api/", html)

    async def test_titelbild_muss_es_geben_und_erscheint_auf_seite_und_karte(self):
        text, fehler = await self._mcp("blog_save_post", slug="x", title="T", body_markdown="B", cover="fehlt.png")
        self.assertTrue(fehler)
        self.assertIn("gibt es nicht", text)
        await self._hoch(name="titel.png")
        await self._beitrag(veroeffentlicht=True, cover="titel.png",
                            body_markdown=LANGER_TEXT + "\n\n![Eine Grafik](/blog/media/titel.png)")
        async with self.Session() as db:
            seite = (await blog_public.blog_beitrag("selbst-hosten", vorschau="", db=db)).body.decode()
            liste = (await blog_public.blog_uebersicht(db=db)).body.decode()
            neueste = json.loads((await blog_public.blog_neueste(db=db)).body)
        self.assertIn('<meta property="og:image" content="https://example.com/blog/media/titel.png">', seite)
        self.assertIn('"image": "https://example.com/blog/media/titel.png"', seite)
        self.assertIn('summary_large_image', seite)
        self.assertIn('class="titelbild"', seite)
        self.assertIn('src="/blog/media/titel.png"', liste)
        self.assertEqual(neueste["beitraege"][0]["bild"]["src"], "/blog/media/titel.png")

    async def test_verwendetes_bild_laesst_sich_nicht_loeschen(self):
        await self._hoch(name="titel.png")
        await self._hoch(name="frei.png")
        await self._beitrag(cover="titel.png")
        text, fehler = await self._mcp("blog_delete_image", name="titel.png")
        self.assertTrue(fehler)
        self.assertIn("selbst-hosten", text)
        text, fehler = await self._mcp("blog_delete_image", name="frei.png")
        self.assertFalse(fehler, text)
        text, _ = await self._mcp("blog_list_images")
        self.assertEqual([b["name"] for b in json.loads(text)["bilder"]], ["titel.png"])

    async def test_verwaltung_hochladen_begrenzt_und_nur_fuer_administratoren(self):
        from io import BytesIO

        from fastapi import UploadFile

        from app.dependencies import require_admin

        for route in blog_admin.router.routes:
            self.assertIn(require_admin, [d.call for d in route.dependant.dependencies], route.path)
        async with self.Session() as db:
            bild = await blog_admin.blog_bild_hochladen(
                file=UploadFile(BytesIO(PNG), filename="Mein Schönes Bild.PNG"), name="", alt="Alt", user=self.ADMIN, db=db)
            self.assertEqual(bild["name"], "mein-schoenes-bild.png")
            with self.assertRaises(HTTPException) as ctx:
                await blog_admin.blog_bild_hochladen(
                    file=UploadFile(BytesIO(PNG + b"0" * blog.MAX_BILD), filename="gross.png"), name="", alt="",
                    user=self.ADMIN, db=db)
            self.assertEqual(ctx.exception.status_code, 413)
            with self.assertRaises(HTTPException) as ctx:
                await blog_admin.blog_bild_hochladen(
                    file=UploadFile(BytesIO(b"<svg/>"), filename="x.png"), name="", alt="", user=self.ADMIN, db=db)
            self.assertEqual(ctx.exception.status_code, 422)
            self.assertEqual(len((await blog_admin.blog_bilder(user=self.ADMIN, db=db))["images"]), 1)
            self.assertEqual(await blog_admin.blog_bild_loeschen("mein-schoenes-bild.png", user=self.ADMIN, db=db),
                             {"deleted": "mein-schoenes-bild.png"})
            with self.assertRaises(HTTPException) as ctx:
                await blog_admin.blog_bild_loeschen("mein-schoenes-bild.png", user=self.ADMIN, db=db)
            self.assertEqual(ctx.exception.status_code, 404)


class SeoPruefungTest(unittest.TestCase):
    def _post(self, **mehr) -> BlogPost:
        werte = dict(
            slug="x", title="KI-Agenten selbst hosten: So geht es",
            description="KI-Agenten selbst hosten: was du brauchst, was es kostet und worauf du beim Betrieb auf eigenen Servern achten musst.",
            keyword="KI-Agenten selbst hosten", body_md=LANGER_TEXT, tags=["Betrieb"],
            faq=[{"frage": "a", "antwort": "b"}], status=STATUS_DRAFT,
        )
        werte.update(mehr)
        return BlogPost(**werte)

    def test_guter_beitrag_hat_weder_fehler_noch_hinweise(self):
        text = LANGER_TEXT + "\n\n![Die drei Kostenblöcke als Grafik](/blog/media/kosten.png)"
        with patch.object(settings, "blog_base_url", "https://example.com"):
            p = blog.seo_pruefung(self._post(body_md=text, cover="titel.png"))
        self.assertEqual(p["fehler"], [])
        self.assertEqual(p["hinweise"], [])
        self.assertEqual(p["interne_verweise"], 2)

    def test_bilder_fehlend_ohne_beschreibung_oder_fremd_sind_hinweise(self):
        p = blog.seo_pruefung(self._post())
        self.assertTrue(any("Kein Titelbild" in h for h in p["hinweise"]))
        self.assertTrue(any("Kein Bild im Text" in h for h in p["hinweise"]))
        text = LANGER_TEXT + "\n\n![](/blog/media/a.png)\n\n![Fremd](https://anderswo.example/b.png)"
        p = blog.seo_pruefung(self._post(body_md=text, cover="t.png"))
        self.assertEqual(p["bilder"], 2)
        self.assertTrue(any("ohne Beschreibung" in h for h in p["hinweise"]))
        self.assertTrue(any("liegen nicht im Blog" in h for h in p["hinweise"]))
        self.assertEqual(p["fehler"], [])

    def test_jede_luecke_wird_einzeln_gemeldet(self):
        faelle = {
            "Titel fehlt": dict(title=""),
            "Beschreibung fehlt": dict(description=""),
            "Hauptbegriff (keyword) fehlt": dict(keyword=""),
            "Weniger als zwei Zwischenüberschriften": dict(body_md="Nur Text. " * 400),
            "Nur ": dict(body_md="## Eins\n\nkurz\n\n## Zwei\n\nkurz"),
        }
        for erwartet, aenderung in faelle.items():
            p = blog.seo_pruefung(self._post(**aenderung))
            self.assertTrue(any(erwartet in f for f in p["fehler"]), (erwartet, p["fehler"]))

    def test_hinweise_verhindern_nichts_aber_nennen_die_stelle(self):
        p = blog.seo_pruefung(self._post(title="Ein Titel ohne den Begriff, der außerdem deutlich länger als sechzig Zeichen ist"))
        self.assertEqual(p["fehler"], [])
        self.assertTrue(any("steht nicht im Titel" in h for h in p["hinweise"]))
        self.assertTrue(any("über 60" in h for h in p["hinweise"]))

    def test_fremde_verweise_zaehlen_nicht_als_eigene(self):
        text = LANGER_TEXT.replace("(/blog/ueberblick)", "(https://anderswo.example/blog/x)").replace("(/)", "(#anker)")
        with patch.object(settings, "blog_base_url", "https://example.com"):
            self.assertEqual(blog.seo_pruefung(self._post(body_md=text))["interne_verweise"], 0)
            eigen = LANGER_TEXT.replace("(/blog/ueberblick)", "(https://example.com/blog/x)")
            self.assertEqual(blog.seo_pruefung(self._post(body_md=eigen))["interne_verweise"], 2)


class SicherheitskopfzeilenTest(unittest.IsolatedAsyncioTestCase):
    """Die allgemeine Richtlinie darf die engere des Blogs nicht ueberschreiben."""

    async def _kopfzeilen(self, eigene: list, pfad: str = "/blog/x") -> dict:
        from app.main import SecurityHeadersMiddleware

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": eigene})
            await send({"type": "http.response.body", "body": b""})

        gesendet = []

        async def send(message):
            gesendet.append(message)

        await SecurityHeadersMiddleware(app)({"type": "http", "path": pfad}, None, send)
        return {k.decode().lower(): v.decode() for k, v in gesendet[0]["headers"]}

    async def test_eigene_richtlinie_bleibt(self):
        kopf = await self._kopfzeilen([(b"content-security-policy", b"default-src 'none'")])
        self.assertEqual(kopf["content-security-policy"], "default-src 'none'")
        self.assertEqual(kopf["x-frame-options"], "DENY")

    async def test_ohne_eigene_richtlinie_gilt_die_allgemeine(self):
        kopf = await self._kopfzeilen([])
        self.assertIn("default-src 'self'", kopf["content-security-policy"])

    async def test_ausserhalb_des_blogs_gilt_immer_die_allgemeine(self):
        # Die Ausnahme darf keinem anderen Endpunkt erlauben, die Richtlinie zu lockern.
        for pfad in ("/api/v1/agents", "/", "/apps/x/blog"):
            kopf = await self._kopfzeilen([(b"content-security-policy", b"default-src *"),
                                           (b"referrer-policy", b"unsafe-url")], pfad=pfad)
            self.assertIn("default-src 'self'", kopf["content-security-policy"], pfad)
            self.assertEqual(kopf["referrer-policy"], "strict-origin-when-cross-origin", pfad)


if __name__ == "__main__":
    unittest.main()
