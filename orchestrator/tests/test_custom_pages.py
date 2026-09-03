"""Eigene Menuepunkte: wer welche Seite zu sehen bekommt und was hineindarf.

Zwei Dinge werden hier geprueft, und beide sind der Grund, weshalb es die
Funktion ueberhaupt sicher gibt:

1. **Die Sichtbarkeit haengt an ``menu_paths``** — und zwar im Server, nicht erst
   in der Seitenleiste. Wuerde nur das Menue ausblenden, koennte jeder
   Angemeldete die Adresse einer fremden Seite ueber die Liste abfragen. Getestet
   wird die Matrix aus (keine Einschraenkung / Seite freigeschaltet / nur andere
   Pfade freigeschaltet / gar nichts) mal (Liste, Einzelabruf).

2. **Was als Adresse angenommen wird.** Der Wert landet als ``src`` in einem
   Rahmen; ein ``javascript:``-Wert waere damit fremder Code in unserer
   Oberflaeche. Deshalb ausdruecklich nur http/https.

Gegen echtes SQL (SQLite im Speicher), damit die Filter- und Sortierlogik der
Query mitgetestet wird und nicht wegdefiniert.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.custom_pages import (
    CustomPageCreate,
    _validate_slug,
    _validate_url,
    create_page,
    get_page_by_slug,
    list_my_pages,
)
from app.models.audit_log import AuditLog
from app.models.base import Base
from app.models.custom_page import CustomPage

ADMIN = SimpleNamespace(id="user-admin", role=None)


def _page(slug: str, **over) -> CustomPage:
    data = dict(
        slug=slug,
        title=slug.upper(),
        url=f"https://{slug}.example.test",
        icon="Globe",
        group_key="collab",
        open_mode="iframe",
        sort_order=0,
        enabled=True,
        allow_media=False,
    )
    data.update(over)
    return CustomPage(**data)


class CustomPageVisibilityTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            # Nur die beiden benoetigten Tabellen: ein ``create_all`` ueber das
            # ganze Modell scheitert an PostgreSQL-eigenen Spaltentypen (JSONB),
            # die SQLite nicht kennt.
            await conn.run_sync(
                lambda c: Base.metadata.create_all(
                    c, tables=[CustomPage.__table__, AuditLog.__table__]
                )
            )
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        self.db = self.Session()
        self.db.add_all([
            _page("owui", sort_order=1),
            _page("intranet", sort_order=0),
            _page("archiv", enabled=False),
        ])
        await self.db.commit()

    async def asyncTearDown(self):
        await self.db.close()
        await self.engine.dispose()

    def _perms(self, menu_paths):
        """``get_effective_permissions`` durch eine feste Antwort ersetzen — die
        Rechteaufloesung selbst hat eigene Tests, hier geht es um ihre Wirkung."""
        return patch(
            "app.api.custom_pages.get_effective_permissions",
            return_value={"menu_paths": menu_paths},
        )

    async def test_ohne_einschraenkung_alle_aktiven_seiten(self):
        with self._perms(None):
            result = await list_my_pages(user=ADMIN, db=self.db)
        slugs = [p["slug"] for p in result["pages"]]
        # Abgeschaltete Seiten tauchen nie auf, und sortiert wird nach sort_order.
        self.assertEqual(slugs, ["intranet", "owui"])

    async def test_nur_freigeschaltete_seite_sichtbar(self):
        with self._perms(["/dashboard", "/p/owui"]):
            result = await list_my_pages(user=ADMIN, db=self.db)
        self.assertEqual([p["slug"] for p in result["pages"]], ["owui"])

    async def test_leere_freigabe_zeigt_nichts(self):
        with self._perms([]):
            result = await list_my_pages(user=ADMIN, db=self.db)
        self.assertEqual(result["pages"], [])

    async def test_einzelabruf_ohne_freigabe_ist_verboten(self):
        # Der Kern: die Seite ausblenden reicht nicht — die Adresse darf auch
        # ueber den direkten Abruf nicht herausfallen.
        with self._perms(["/dashboard"]):
            with self.assertRaises(HTTPException) as ctx:
                await get_page_by_slug("owui", user=ADMIN, db=self.db)
        self.assertEqual(ctx.exception.status_code, 403)

    async def test_einzelabruf_mit_freigabe_liefert_adresse(self):
        with self._perms(["/p/owui"]):
            page = await get_page_by_slug("owui", user=ADMIN, db=self.db)
        self.assertEqual(page["url"], "https://owui.example.test")

    async def test_abgeschaltete_seite_ist_nicht_abrufbar(self):
        with self._perms(None):
            with self.assertRaises(HTTPException) as ctx:
                await get_page_by_slug("archiv", user=ADMIN, db=self.db)
        self.assertEqual(ctx.exception.status_code, 404)

    async def test_praefix_gibt_keine_fremde_seite_frei(self):
        # "/p/owui" darf NICHT "/p/owui-test" mitfreigeben — sonst oeffnete ein
        # neu angelegter Kurzname mit gleichem Wortanfang stillschweigend mit.
        self.db.add(_page("owui-test", sort_order=2))
        await self.db.commit()
        with self._perms(["/p/owui"]):
            result = await list_my_pages(user=ADMIN, db=self.db)
        self.assertEqual([p["slug"] for p in result["pages"]], ["owui"])

    async def test_doppelter_kurzname_wird_abgelehnt(self):
        body = CustomPageCreate(slug="owui", title="Zweites OWUI", url="https://b.example.test")
        with self.assertRaises(HTTPException) as ctx:
            await create_page(body=body, user=ADMIN, db=self.db)
        self.assertEqual(ctx.exception.status_code, 409)


class CustomPageInputTest(unittest.TestCase):
    def test_nur_http_und_https(self):
        for url in ("https://a.test", "http://a.test/pfad?x=1"):
            self.assertEqual(_validate_url(url), url)

    def test_javascript_und_data_werden_abgelehnt(self):
        # Beides landete sonst als iframe-src — also fremder Code in unserer Seite.
        for url in ("javascript:alert(1)", "data:text/html,<script>x</script>", "file:///etc/passwd", "//a.test"):
            with self.assertRaises(HTTPException, msg=url):
                _validate_url(url)

    def test_leere_adresse_wird_abgelehnt(self):
        with self.assertRaises(HTTPException):
            _validate_url("   ")

    def test_kurzname_regeln(self):
        self.assertEqual(_validate_slug("OpenWebUI-2"), "openwebui-2")
        for bad in ("-start", "mit leerzeichen", "mit/slash", "", "a" * 64, "admin", "../etc"):
            with self.assertRaises(HTTPException, msg=bad):
                _validate_slug(bad)


if __name__ == "__main__":
    unittest.main()
