"""Persoenliche Oberflaechen-Einstellungen am Konto: ``/auth/me/ui-preferences``.

Die Listenansicht der Agentenseite legt ihre Einrichtung (Ansicht, Spalten,
Sortierung, gespeicherte Filter) am Konto ab, damit sie auf jedem Geraet gilt.
Geprueft wird der Vertrag: ``{}`` ohne Gespeichertes, PATCH ersetzt nur den
uebergebenen Schluessel, unbekannter Schluessel und mehr als 32 KB -> 422, ohne
Anmeldung -> 401, und jede Person sieht nur ihre eigenen Einstellungen.

Ueber HTTP gegen ECHTES SQL (in-memory SQLite) — die 422 fuer einen Body, der
kein Objekt ist, entsteht erst in FastAPI, ein Direktaufruf wuerde sie wegtesten.
"""

import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import auth
from app.core import ui_einstellungen as ui
from app.db.session import get_db
from app.models.user import User, UserRole

URL = "/api/v1/auth/me/ui-preferences"

AGENTS_PAGE = {
    "ansicht": "list",
    "liste": {
        "spalten": ["name", "status", "aufgabe", "gruppe", "zuletzt_aktiv", "kosten", "warnungen"],
        "sortierung": {"spalte": "zuletzt_aktiv", "richtung": "desc"},
        "gruppierung": "tag",
    },
    "filter": [{"id": "f1", "name": "Webwerkstatt-Kunden", "suche": "",
                "schlagwort": "Webwerkstatt", "status": []}],
}


class ZusammenfuehrenTests(unittest.TestCase):
    def test_ersetzt_nur_den_uebergebenen_schluessel(self):
        gespeichert = {"agents_page": {"ansicht": "grid"}, "spaeter": {"x": 1}}
        neu = ui.zusammenfuehren(gespeichert, {"agents_page": {"ansicht": "list"}})
        self.assertEqual(neu, {"agents_page": {"ansicht": "list"}, "spaeter": {"x": 1}})
        # Das Gespeicherte selbst bleibt unangetastet (SQLAlchemy braucht ein neues Objekt).
        self.assertEqual(gespeichert["agents_page"], {"ansicht": "grid"})

    def test_ohne_gespeichertes(self):
        self.assertEqual(ui.zusammenfuehren(None, {"agents_page": {}}), {"agents_page": {}})

    def test_unbekannter_schluessel(self):
        with self.assertRaises(ui.UngueltigeEinstellungen):
            ui.zusammenfuehren({}, {"agents_page": {}, "fremd": {}})

    def test_wert_muss_ein_objekt_sein(self):
        for wert in (None, [], "list", 3):
            with self.assertRaises(ui.UngueltigeEinstellungen):
                ui.zusammenfuehren({}, {"agents_page": wert})

    def test_leere_aenderung(self):
        with self.assertRaises(ui.UngueltigeEinstellungen):
            ui.zusammenfuehren({}, {})

    def test_groessengrenze_gilt_fuer_das_ganze_objekt(self):
        # Genau an der Grenze geht, ein Byte darueber nicht — gemessen am Gesamtobjekt.
        rahmen = ui.groesse({"agents_page": {"x": ""}})
        passt = {"agents_page": {"x": "a" * (ui.MAX_BYTES - rahmen)}}
        self.assertEqual(ui.groesse(ui.zusammenfuehren({}, passt)), ui.MAX_BYTES)
        with self.assertRaises(ui.UngueltigeEinstellungen):
            ui.zusammenfuehren({}, {"agents_page": {"x": "a" * (ui.MAX_BYTES - rahmen + 1)}})


class EndpunktTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(User.metadata.create_all, tables=[User.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.Session() as db:
            db.add_all([
                User(id="ua", email="a@example.invalid", name="A", password_hash="x",
                     role=UserRole.MEMBER),
                User(id="ub", email="b@example.invalid", name="B", password_hash="x",
                     role=UserRole.MEMBER),
            ])
            await db.commit()

        app = FastAPI()
        app.include_router(auth.router, prefix="/api/v1")

        async def _db():
            async with self.Session() as db:
                yield db

        app.dependency_overrides[get_db] = _db

        # Angemeldet ist, wessen Kennung im Kopf steht — ohne Kopf: 401 wie im Original.
        async def _nutzer(request: Request, db):
            uid = request.headers.get("x-test-user")
            if not uid:
                raise HTTPException(status_code=401, detail="Not authenticated")
            return await db.scalar(select(User).where(User.id == uid))

        p = patch("app.dependencies.get_current_user", _nutzer)
        p.start()
        self.addCleanup(p.stop)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.engine.dispose()

    async def _get(self, uid="ua"):
        return await self.client.get(URL, headers={"x-test-user": uid})

    async def _patch(self, body, uid="ua"):
        return await self.client.patch(URL, json=body, headers={"x-test-user": uid})

    async def test_ohne_gespeichertes_leeres_objekt(self):
        r = await self._get()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {})

    async def test_speichern_und_wieder_lesen(self):
        r = await self._patch({"agents_page": AGENTS_PAGE})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"agents_page": AGENTS_PAGE})
        self.assertEqual((await self._get()).json(), {"agents_page": AGENTS_PAGE})

    async def test_andere_schluessel_bleiben_unveraendert(self):
        # Ein spaeterer Bereich hat schon etwas abgelegt (direkt in der Datenbank,
        # weil die API ihn noch nicht kennt) — ein PATCH auf agents_page laesst ihn stehen.
        async with self.Session() as db:
            nutzer = await db.get(User, "ua")
            nutzer.ui_preferences = {"agents_page": {"ansicht": "grid"}, "anderes": {"a": 1}}
            await db.commit()
        r = await self._patch({"agents_page": {"ansicht": "list"}})
        self.assertEqual(r.json(), {"agents_page": {"ansicht": "list"}, "anderes": {"a": 1}})

    async def test_ersetzt_den_schluessel_vollstaendig(self):
        await self._patch({"agents_page": AGENTS_PAGE})
        r = await self._patch({"agents_page": {"ansicht": "teams"}})
        self.assertEqual(r.json(), {"agents_page": {"ansicht": "teams"}})

    async def test_unbekannter_schluessel_422(self):
        r = await self._patch({"tasks_page": {}})
        self.assertEqual(r.status_code, 422)
        self.assertEqual((await self._get()).json(), {})

    async def test_kein_objekt_422(self):
        self.assertEqual((await self._patch(["agents_page"])).status_code, 422)
        self.assertEqual((await self._patch({"agents_page": "list"})).status_code, 422)

    async def test_zu_gross_422_und_nichts_gespeichert(self):
        await self._patch({"agents_page": {"ansicht": "list"}})
        r = await self._patch({"agents_page": {"x": "a" * (33 * 1024)}})
        self.assertEqual(r.status_code, 422)
        self.assertEqual((await self._get()).json(), {"agents_page": {"ansicht": "list"}})

    async def test_ohne_anmeldung_401(self):
        self.assertEqual((await self.client.get(URL)).status_code, 401)
        self.assertEqual((await self.client.patch(URL, json={"agents_page": {}})).status_code, 401)

    async def test_jede_person_nur_ihre_eigenen(self):
        await self._patch({"agents_page": {"ansicht": "list"}}, uid="ua")
        self.assertEqual((await self._get("ub")).json(), {})
        await self._patch({"agents_page": {"ansicht": "network"}}, uid="ub")
        self.assertEqual((await self._get("ua")).json(), {"agents_page": {"ansicht": "list"}})


if __name__ == "__main__":
    unittest.main()
