"""Proxy-Stufen für Claude- und Codex-Zugang in der Oberfläche (Admin → Betrieb).

Ein Kunden-Admin stellt die Stufen ohne .env und ohne SSH ein. Geprüft wird:

* Lesen/Schreiben der Stufe (aus / Auswahl / alle) und dass „aus“ einen Neustart
  übersteht (leere Werte übergeht das Laden beim Start — dann gälte wieder die .env).
* Die Endpunkte: nur Admins, unbekannter Zugang/Modus und fremde oder unpassende
  Agenten werden abgelehnt, Änderung wirkt sofort und steht im Prüfprotokoll.
* Der Sicherheitsstatus meldet Agenten mit Klartext-Zugang in beide Richtungen.
"""
import inspect
import unittest
from unittest.mock import AsyncMock, patch

from app.api import settings as api
from app.config import settings
from app.core import egress
from app.core import sicherheitsstatus as ss
from app.dependencies import require_admin
from app.models.agent import Agent
from app.models.platform_settings import PlatformSettings
from app.models.user import User, UserRole
from app.services.settings_service import SettingsService
from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

TOKEN = "p" * 64
#: Vor dem Ersetzen in den Endpunkt-Tests festgehalten — der Protokoll-Test prüft das Original.
_PROTOKOLL_ECHT = api._stufe_protokollieren
ADMIN = User(id="ad", email="admin@example.invalid", name="Admin", role=UserRole.ADMIN)


def _proxy_an():
    return [patch.object(settings, "egress_proxy_enabled", True),
            patch.object(settings, "egress_proxy_token", TOKEN)]


class StufeLesenSchreibenTests(unittest.TestCase):
    def test_rundweg(self):
        for modus, agenten, gespeichert in (("aus", [], "aus"), ("alle", [], "alle"),
                                            ("auswahl", ["bbbbbbbb", "AAAAAAAA", "bbbbbbbb"],
                                             "aaaaaaaa,bbbbbbbb")):
            with self.subTest(modus):
                wert = egress.stufe_schreiben(modus, agenten)
                self.assertEqual(wert, gespeichert)
                self.assertEqual(egress.stufe_lesen(wert)["modus"], modus)

    def test_aus_ist_nicht_leer(self):
        """Leer übergeht load_into_config — nach dem Neustart gälte wieder die .env."""
        self.assertTrue(egress.stufe_schreiben("aus"))

    def test_altwerte_aus_der_env(self):
        self.assertEqual(egress.stufe_lesen("")["modus"], "aus")
        self.assertEqual(egress.stufe_lesen(None)["modus"], "aus")
        self.assertEqual(egress.stufe_lesen(" Alle ")["modus"], "alle")
        self.assertEqual(egress.stufe_lesen("a1b2c3d4, e5f6a7b8"),
                         {"modus": "auswahl", "agenten": ["a1b2c3d4", "e5f6a7b8"]})

    def test_gesenkt(self):
        L = egress.stufe_lesen
        for alt, neu, erwartet in (("alle", "aus", True), ("alle", "aaaaaaaa", True), ("aus", "alle", False),
                                   ("aaaaaaaa", "aus", True), ("aaaaaaaa,bbbbbbbb", "aaaaaaaa", True),
                                   ("aaaaaaaa", "aaaaaaaa,bbbbbbbb", False), ("aaaaaaaa", "alle", False),
                                   ("alle", "alle", False)):
            with self.subTest(alt=alt, neu=neu):
                self.assertEqual(egress.stufe_gesenkt(L(alt), L(neu)), erwartet)

    def test_unfoermige_env_werte_laut(self):
        """„true“ trifft keinen Agenten — nicht still als Auswahl anzeigen."""
        with self.assertLogs("app.core.egress", "WARNING"):
            self.assertEqual(egress.stufe_lesen("true"), {"modus": "auswahl", "agenten": ["true"]})
        with patch.object(settings, "egress_proxy_enabled", True), patch.object(settings, "egress_proxy_token", TOKEN), \
                patch.object(settings, "egress_ki_zugang", "true"), self.assertLogs("app.core.egress", "WARNING"):
            self.assertFalse(egress.ki_ueber_proxy("aaaaaaaa"), "„true“ heißt nicht „alle“")

    def test_ungueltig(self):
        for modus, agenten in (("teilweise", []), ("auswahl", []), ("auswahl", ["../etc"]),
                               ("auswahl", ["a1b2c3d4,alle"]), ("auswahl", ["a1b2c3d"])):
            with self.subTest(modus=modus, agenten=agenten), self.assertRaises(ValueError):
                egress.stufe_schreiben(modus, agenten)

    def test_wirkung_je_stufe(self):
        p = _proxy_an()
        for x in p:
            x.start()
        try:
            for wert, a, b in (("aus", False, False), ("", False, False), ("alle", True, True),
                               ("aaaaaaaa", True, False)):
                with self.subTest(wert), patch.object(settings, "egress_ki_zugang", wert), \
                        patch.object(settings, "egress_codex_zugang", wert):
                    self.assertEqual((egress.ki_ueber_proxy("aaaaaaaa"), egress.ki_ueber_proxy("bbbbbbbb")), (a, b))
                    self.assertEqual((egress.codex_ueber_proxy("aaaaaaaa"),
                                      egress.codex_ueber_proxy("bbbbbbbb")), (a, b))
        finally:
            for x in p:
                x.stop()


class DbTestFall(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Agent.metadata.create_all,
                                tables=[User.__table__, Agent.__table__, PlatformSettings.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        self.db = self.Session()
        self.db.add_all([
            User(id="u1", email="u@example.invalid", name="U", role=UserRole.MEMBER),
            Agent(id="aaaaaaaa", name="Claude-A", user_id="u1", mode="claude_code",
                  config={"egress": True, "egress_ki_stufe": False}),
            Agent(id="bbbbbbbb", name="Claude-B", user_id="u1", mode="claude_code",
                  config={"egress": True, "egress_ki_stufe": False}),
            Agent(id="cccccccc", name="Codex-C", user_id="u1", mode="codex_cli",
                  config={"egress": True, "egress_codex_stufe": False}),
        ])
        await self.db.commit()
        self.patches = _proxy_an() + [
            patch.object(settings, "egress_ki_zugang", ""),
            patch.object(settings, "egress_codex_zugang", ""),
            patch.object(api, "_stufe_protokollieren", AsyncMock()),
        ]
        for p in self.patches:
            p.start()

    async def asyncTearDown(self):
        for p in self.patches:
            p.stop()
        await self.db.close()
        await self.engine.dispose()

    async def setzen(self, art, modus, agenten=()):
        return await api.set_egress_stufe(api.EgressStufe(art=art, modus=modus, agenten=list(agenten)),
                                          user=ADMIN, db=self.db)


class EndpunktTests(DbTestFall):
    def test_nur_fuer_admins(self):
        for fn in (api.get_egress_stufen, api.set_egress_stufe):
            with self.subTest(fn.__name__):
                self.assertIs(inspect.signature(fn).parameters["user"].default.dependency, require_admin)

    async def test_stand(self):
        stand = await api.get_egress_stufen(user=ADMIN, db=self.db)
        self.assertTrue(stand["proxy_aktiv"])
        self.assertEqual(stand["ki"]["modus"], "aus")
        self.assertEqual({a["id"]: a["laufzeit"] for a in stand["agenten"]},
                         {"aaaaaaaa": "claude_code", "bbbbbbbb": "claude_code", "cccccccc": "codex_cli"})
        self.assertFalse(any(a["veraltet"] for a in stand["agenten"]), "Stufe aus = Behälter passen")

    async def test_auswahl_wirkt_sofort_ist_gespeichert_und_protokolliert(self):
        stand = await self.setzen("ki", "auswahl", ["AAAAAAAA"])
        self.assertEqual(settings.egress_ki_zugang, "aaaaaaaa")
        self.assertTrue(egress.ki_ueber_proxy("aaaaaaaa"))
        self.assertFalse(egress.ki_ueber_proxy("bbbbbbbb"))
        self.assertEqual({a["id"] for a in stand["agenten"] if a["veraltet"]}, {"aaaaaaaa"},
                         "nur der umgestellte Agent zeigt „Aktualisieren“")
        async with self.Session() as frisch:
            self.assertEqual((await SettingsService(frisch).get_all())["egress_ki_zugang"], "aaaaaaaa")
        api._stufe_protokollieren.assert_awaited_once()
        _, _, art, von, nach, gesenkt = api._stufe_protokollieren.await_args.args
        self.assertEqual((art, von["modus"], nach["agenten"], gesenkt), ("ki", "aus", ["aaaaaaaa"], False))

    async def test_absenken_wird_markiert(self):
        await self.setzen("ki", "alle")
        await self.setzen("ki", "auswahl", ["aaaaaaaa"])
        self.assertTrue(api._stufe_protokollieren.await_args.args[5], "alle → Auswahl = gesenkt")
        await self.setzen("ki", "auswahl", ["aaaaaaaa", "bbbbbbbb"])
        self.assertFalse(api._stufe_protokollieren.await_args.args[5], "Auswahl erweitert")

    async def test_protokolleintrag(self):
        """Der echte Eintrag nennt vorher/nachher und markiert das Absenken."""
        from app.models.audit_log import AuditLog
        async with self.engine.begin() as conn:
            await conn.run_sync(AuditLog.metadata.create_all, tables=[AuditLog.__table__])
        await _PROTOKOLL_ECHT(self.db, ADMIN, "codex", egress.stufe_lesen("alle"),
                                        egress.stufe_lesen("aus"), True)
        await self.db.commit()
        eintrag = (await self.db.execute(select(AuditLog))).scalars().one()
        self.assertIn("Codex-Zugang: alle → aus", eintrag.command)
        self.assertIn("gesenkt", eintrag.command)
        self.assertEqual(eintrag.meta["aktion"], "egress_stufe_gesenkt")

    async def test_aus_uebersteht_den_neustart(self):
        await self.setzen("codex", "alle")
        await self.setzen("codex", "aus")
        # Neustart: .env sagt „alle“, gespeichert ist „aus“ — gespeichert gewinnt.
        settings.egress_codex_zugang = "alle"
        async with self.Session() as frisch:
            await SettingsService(frisch).load_into_config()
        self.assertEqual(egress.stufe_lesen(settings.egress_codex_zugang)["modus"], "aus")
        self.assertFalse(egress.codex_ueber_proxy("cccccccc"))

    async def test_abgelehnt(self):
        faelle = (
            ("anthropic", "alle", []),            # unbekannter Zugang
            ("ki", "teilweise", []),              # unbekannter Modus
            ("ki", "auswahl", []),                # leere Auswahl
            ("ki", "auswahl", ["dddddddd"]),      # Agent gibt es nicht
            ("ki", "auswahl", ["cccccccc"]),      # Codex-Agent in der Claude-Stufe
            ("codex", "auswahl", ["aaaaaaaa"]),   # Claude-Agent in der Codex-Stufe
            ("ki", "auswahl", ["aaaaaaaa'; --"]),  # unförmige Kennung
        )
        for art, modus, agenten in faelle:
            with self.subTest(art=art, modus=modus, agenten=agenten):
                with self.assertRaises(HTTPException) as fehler:
                    await self.setzen(art, modus, agenten)
                self.assertEqual(fehler.exception.status_code, 400)
        self.assertEqual(settings.egress_ki_zugang, "", "nichts übernommen")
        api._stufe_protokollieren.assert_not_awaited()


class SicherheitsstatusTests(DbTestFall):
    async def punkt(self, art):
        return next(p for p in await ss._zugaenge(self.db) if p["schluessel"] == f"zugang_{art}")

    async def test_klartext_wird_gemeldet(self):
        p = await self.punkt("ki")
        self.assertEqual(p["stand"], "warnung")
        self.assertIn("2 von 2", p["text"])
        self.assertIn("Zugänge hinter dem Proxy", p["beheben"])

    async def test_stufe_gilt_aber_behaelter_alt(self):
        await self.setzen("ki", "alle")
        p = await self.punkt("ki")
        self.assertEqual(p["stand"], "warnung")
        self.assertIn("entstanden vorher", p["text"])

    async def test_alles_hinter_dem_proxy(self):
        await self.setzen("codex", "alle")
        agent = await self.db.get(Agent, "cccccccc")
        agent.config = {"egress": True, "egress_codex_stufe": True}
        await self.db.commit()
        self.assertEqual((await self.punkt("codex"))["stand"], "ok")

    async def test_ohne_codex_agenten_nur_info(self):
        await self.db.execute(delete(Agent).where(Agent.id == "cccccccc"))
        await self.db.commit()
        self.assertEqual((await self.punkt("codex"))["stand"], "info")

    async def test_ohne_proxy_keine_punkte(self):
        with patch.object(settings, "egress_proxy_enabled", False):
            self.assertEqual(await ss._zugaenge(self.db), [])


if __name__ == "__main__":
    unittest.main()
