"""Nutzungsmessung (#1047): gezählt wird nur der Bereich, nur für sich selbst, Summen nur für Admins.

MC/DC für ``bereich_aus_pfad``: gültiger erster Pfadteil / angelegte Seite mit und
ohne Kurzname / Unsinn. Zählen: neu, erhöht, je Person getrennt, Tagesdeckel,
alte Tage fallen weg. Schnittstelle: ohne Anmeldung 401, Übersicht für
Mitglieder 403, eine fremde Person lässt sich weder zählen noch abfragen.
"""
import unittest
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import nutzung as api
from app.core import nutzung
from app.dependencies import get_db, require_auth
from app.models.bereich_aufruf import BereichAufruf
from app.models.user import UserRole

HEUTE = date(2026, 10, 27)
ICH = SimpleNamespace(id="m1", role=UserRole.MEMBER)
ADMIN = SimpleNamespace(id="admin", role=UserRole.ADMIN)


class BereichAusPfad(unittest.TestCase):
    def test_erster_pfadteil(self):
        self.assertEqual(nutzung.bereich_aus_pfad("/agents/abc-123?tab=x"), "/agents")
        self.assertEqual(nutzung.bereich_aus_pfad("/Heute"), "/heute")

    def test_angelegte_seite_behaelt_kurzname(self):
        self.assertEqual(nutzung.bereich_aus_pfad("/p/kunden-portal/x"), "/p/kunden-portal")
        self.assertIsNone(nutzung.bereich_aus_pfad("/p"))

    def test_unsinn_wird_verworfen(self):
        for roh in (None, "", "agents", "/", "/<script>", "javascript:alert(1)", "/" + "a" * 400):
            self.assertIsNone(nutzung.bereich_aus_pfad(roh), roh)

    def test_erfundene_bereiche_zaehlen_nicht(self):
        for roh in ("/aaa1", "/login", "/register", "/agentsx"):
            self.assertIsNone(nutzung.bereich_aus_pfad(roh), roh)

    def test_liste_passt_zu_den_seiten_der_oberflaeche(self):
        app = Path(__file__).resolve().parents[2] / "frontend" / "src" / "app"
        if not app.is_dir():
            self.skipTest("Frontend liegt nicht neben dem Orchestrator")
        seiten = {d.name for d in app.iterdir() if d.is_dir() and not d.name.startswith(("_", "("))}
        self.assertEqual(seiten - {"login", "register", "p"}, set(nutzung.BEREICHE),
                         "neue Seite? In nutzung.BEREICHE eintragen")


class Zaehlen(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(BereichAufruf.metadata.create_all, tables=[BereichAufruf.__table__])
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        nutzung._letzte_bereinigung = None

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _zaehle(self, user, pfad, tag=HEUTE):
        async with self.sessions() as db:
            return await nutzung.zaehlen(db, user, pfad, heute=tag)

    async def test_erhoeht_je_person_getrennt(self):
        await self._zaehle("m1", "/agents")
        await self._zaehle("m1", "/agents/x")
        await self._zaehle("m2", "/agents")
        async with self.sessions() as db:
            self.assertEqual(await nutzung.meine(db, "m1", heute=HEUTE), {"/agents": 2})
            self.assertEqual(await nutzung.meine(db, "m2", heute=HEUTE), {"/agents": 1})

    async def test_verworfener_pfad_legt_nichts_an(self):
        self.assertIsNone(await self._zaehle("m1", "kein-pfad"))
        async with self.sessions() as db:
            self.assertEqual(await nutzung.meine(db, "m1", heute=HEUTE), {})

    async def test_tagesdeckel_gegen_beliebige_bereiche(self):
        with patch.object(nutzung, "MAX_BEREICHE_JE_TAG", 2):
            self.assertEqual(await self._zaehle("m1", "/agents"), "/agents")
            self.assertEqual(await self._zaehle("m1", "/tasks"), "/tasks")
            self.assertIsNone(await self._zaehle("m1", "/apps"), "dritter neuer Bereich am Tag")
            self.assertEqual(await self._zaehle("m1", "/agents"), "/agents", "Vorhandenes zählt weiter")

    async def test_geht_mit_dem_konto(self):
        await self._zaehle("m1", "/agents")
        await self._zaehle("m2", "/agents")
        async with self.sessions() as db:
            await nutzung.von_person_loeschen(db, "m1")
            await db.commit()
            self.assertEqual(await nutzung.meine(db, "m1", heute=HEUTE), {})
            self.assertEqual(await nutzung.meine(db, "m2", heute=HEUTE), {"/agents": 1})

    async def test_zeitraum_und_frist(self):
        alt = HEUTE - timedelta(days=nutzung.AUFBEWAHRUNG_TAGE + 1)
        await self._zaehle("m1", "/apps", tag=alt)
        await self._zaehle("m1", "/apps", tag=HEUTE - timedelta(days=40))
        async with self.sessions() as db:
            self.assertEqual(await nutzung.meine(db, "m1", tage=30, heute=HEUTE), {})
            self.assertEqual(await nutzung.meine(db, "m1", tage=60, heute=HEUTE), {"/apps": 1})
        # Erster Aufruf eines neuen Tages räumt jenseits der Frist auf.
        nutzung._letzte_bereinigung = None
        await self._zaehle("m1", "/heute")
        async with self.sessions() as db:
            tage = set(await db.scalars(select(BereichAufruf.tag)))
        self.assertNotIn(alt, tage)

    async def test_uebersicht_nur_summen(self):
        await self._zaehle("m1", "/agents")
        await self._zaehle("m1", "/agents")
        await self._zaehle("m2", "/agents")
        await self._zaehle("m2", "/workflows")
        async with self.sessions() as db:
            u = await nutzung.uebersicht(db, heute=HEUTE)
        self.assertEqual(u[0], {"bereich": "/agents", "aufrufe": 3, "personen": 2,
                                "zuletzt": HEUTE.isoformat()})
        self.assertEqual(u[1]["bereich"], "/workflows")
        self.assertNotIn("m1", repr(u), "keine Personen in der Übersicht")


class Schnittstelle(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI()
        self.app.include_router(api.router)
        self.app.dependency_overrides[get_db] = lambda: None
        self.client = TestClient(self.app)

    def test_unangemeldet_ist_401(self):
        with patch("app.dependencies._check_users_exist", return_value=True):
            self.assertEqual(self.client.post("/nutzung/aufruf", json={"pfad": "/agents"}).status_code, 401)
            self.assertEqual(self.client.get("/nutzung/meine").status_code, 401)
            self.assertEqual(self.client.get("/nutzung/uebersicht").status_code, 401)

    def test_zaehlt_nur_fuer_sich_selbst(self):
        self.app.dependency_overrides[require_auth] = lambda: ICH
        with patch.object(nutzung, "zaehlen", AsyncMock(return_value="/agents")) as zaehlen:
            r = self.client.post("/nutzung/aufruf?user_id=m2", json={"pfad": "/agents", "user_id": "m2"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"bereich": "/agents"})
        self.assertEqual(zaehlen.await_args.args[1], "m1")

    def test_zu_langer_pfad_ist_422(self):
        self.app.dependency_overrides[require_auth] = lambda: ICH
        self.assertEqual(self.client.post("/nutzung/aufruf", json={"pfad": "/" + "a" * 400}).status_code, 422)

    def test_meine_nur_eigene(self):
        self.app.dependency_overrides[require_auth] = lambda: ICH
        with patch.object(nutzung, "meine", AsyncMock(return_value={})) as meine:
            r = self.client.get("/nutzung/meine?user_id=m2")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(meine.await_args.args[1], "m1")

    def test_uebersicht_fuer_mitglieder_403_fuer_admins_200(self):
        with patch("app.dependencies.get_current_user", AsyncMock(return_value=ICH)):
            self.assertEqual(self.client.get("/nutzung/uebersicht").status_code, 403)
        with patch("app.dependencies.get_current_user", AsyncMock(return_value=ADMIN)), \
                patch.object(nutzung, "uebersicht", AsyncMock(return_value=[])):
            self.assertEqual(self.client.get("/nutzung/uebersicht").status_code, 200)


if __name__ == "__main__":
    unittest.main()
