"""Korrekturen an Skills überleben Abgleich und Probezeit.

Gemeldet: ein Agent korrigiert seinen Skill, zwei Tage später macht er denselben
Fehler wieder. Ursache: mitgelieferte und importierte Skills wurden bei jedem
Orchestrator-Start (mitgelieferte_skills.synchronisieren) bzw. jedem Crawl
(skill_crawler._sync_to_db) auf den Stand der Quelle gesetzt — auch nach einer
bewussten Korrektur. Und eine Probezeit konnte eine spätere Korrektur
zurückbauen. Hier mit echter (In-Memory-)Datenbank nachgestellt.
"""

import contextlib
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core import mitgelieferte_skills as ms
from app.core.skill_herkunft import FREIGABE_SCHNAPPSCHUSS, lokal_geaendert, nach_freigabe_geaendert
from app.models.skill import Skill, SkillCategory, SkillStatus, SkillTaskUsage, SkillVersion
from app.services import skill_crawler as sc


class _Db(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            for t in (Skill, SkillVersion, SkillTaskUsage):
                await conn.run_sync(lambda c, t=t: t.__table__.create(c))
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

        @contextlib.asynccontextmanager
        async def _sitzung(*a, **k):
            async with self.Session() as db:
                yield db

        self.sitzung = _sitzung

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _skill(self, **felder) -> int:
        async with self.Session() as db:
            s = Skill(category=SkillCategory.ROUTINE, status=SkillStatus.ACTIVE, **felder)
            db.add(s)
            await db.commit()
            return s.id

    async def _korrigieren(self, skill_id: int, inhalt: str, wer: str = "agent:a1"):
        """Wie skill_update: erst Schnappschuss, dann neuer Inhalt."""
        async with self.Session() as db:
            s = await db.get(Skill, skill_id)
            db.add(SkillVersion(skill_id=s.id, version_number=1, content=s.content,
                                description=s.description, created_by=wer, change_reason="Korrektur"))
            s.content = inhalt
            await db.commit()

    async def _inhalt(self, skill_id: int) -> str:
        async with self.Session() as db:
            return (await db.get(Skill, skill_id)).content


class MitgelieferteSkills(_Db):
    def _katalog(self, inhalt):
        return [{"name": "angebot", "description": "d", "category": "ROUTINE",
                 "content": inhalt, "created_by": "system"}]

    async def _sync(self, inhalt):
        with patch.object(ms, "laden", lambda: self._katalog(inhalt)), \
             patch("app.db.session.resilient_session", self.sitzung):
            return await ms.synchronisieren()

    async def test_unveraenderter_skill_bekommt_die_neue_fassung(self):
        sid = await self._skill(name="angebot", content="alt", description="d", source_repo=ms.QUELLE)
        await self._sync("neu")
        self.assertEqual(await self._inhalt(sid), "neu")

    async def test_korrektur_ueberlebt_den_start(self):
        sid = await self._skill(name="angebot", content="alt", description="d", source_repo=ms.QUELLE)
        await self._korrigieren(sid, "korrigiert vom Agenten")
        with self.assertLogs("app.core.mitgelieferte_skills", level="INFO") as log:
            neu, aktualisiert = await self._sync("neue Fassung der Quelle")
        self.assertEqual(await self._inhalt(sid), "korrigiert vom Agenten")
        self.assertEqual(aktualisiert, 0)
        self.assertIn("lokale Fassung behalten", "\n".join(log.output))


class ImportierteSkills(_Db):
    async def _crawl(self, inhalt):
        dienst = sc.SkillCrawlerService(SimpleNamespace(client=None))
        with patch("app.db.session.resilient_session", self.sitzung):
            await dienst._sync_to_db([{"name": "erklaervideo", "description": "d", "category": "ROUTINE",
                                       "content": inhalt, "created_by": "import:source:2"}])

    async def test_unveraenderter_import_wird_nachgezogen(self):
        sid = await self._skill(name="erklaervideo", content="alt", created_by="import:source:2")
        await self._crawl("neu aus der Quelle")
        self.assertEqual(await self._inhalt(sid), "neu aus der Quelle")

    async def test_korrigierter_import_bleibt(self):
        sid = await self._skill(name="erklaervideo", content="alt", created_by="import:source:2")
        await self._korrigieren(sid, "korrigiert vom Admin", wer="user")
        await self._crawl("neu aus der Quelle")
        self.assertEqual(await self._inhalt(sid), "korrigiert vom Admin")


class ProbezeitBautKeineKorrekturZurueck(_Db):
    async def test_aenderung_nach_freigabe_beendet_die_probezeit_ohne_rueckbau(self):
        from app.services import improvement_engine as ie

        start = datetime.now(timezone.utc) - timedelta(days=3)
        sid = await self._skill(name="video", content="verbessert", created_by="agent:a1",
                                improvement_status="probation", probation_started_at=start,
                                pre_improvement_avg_helpfulness=4.5)
        async with self.Session() as db:
            # Schnappschuss der Freigabe (zählt nicht als spätere Änderung) …
            db.add(SkillVersion(skill_id=sid, version_number=1, content="vor der Verbesserung",
                                created_by="user:admin", change_reason=FREIGABE_SCHNAPPSCHUSS,
                                created_at=start))
            # … und fünf schlecht bewertete Nutzungen: ohne Sperre gäbe es den Rückbau.
            for _ in range(5):
                db.add(SkillTaskUsage(skill_id=sid, task_id="t", agent_id="a1", skill_helpfulness=1,
                                      created_at=start + timedelta(hours=1)))
            await db.commit()
        await self._korrigieren(sid, "korrigiert nach der Freigabe")

        async with self.Session() as db:
            await ie._validate_probation_skills(db)
            await db.commit()
        async with self.Session() as db:
            s = await db.get(Skill, sid)
            self.assertEqual(s.content, "korrigiert nach der Freigabe")
            self.assertIsNone(s.improvement_status)

    async def test_ohne_spaetere_aenderung_wird_wie_bisher_zurueckgebaut(self):
        from app.services import improvement_engine as ie

        start = datetime.now(timezone.utc) - timedelta(days=3)
        sid = await self._skill(name="video2", content="verbessert", created_by="agent:a1",
                                improvement_status="probation", probation_started_at=start,
                                pre_improvement_avg_helpfulness=4.5)
        async with self.Session() as db:
            db.add(SkillVersion(skill_id=sid, version_number=1, content="vor der Verbesserung",
                                created_by="user:admin", change_reason=FREIGABE_SCHNAPPSCHUSS,
                                created_at=start))
            for _ in range(5):
                db.add(SkillTaskUsage(skill_id=sid, task_id="t", agent_id="a1", skill_helpfulness=1,
                                      created_at=start + timedelta(hours=1)))
            await db.commit()
        async with self.Session() as db:
            await ie._validate_probation_skills(db)
            await db.commit()
        self.assertEqual(await self._inhalt(sid), "vor der Verbesserung")


class Hilfsfunktionen(_Db):
    async def test_lokal_geaendert(self):
        sid = await self._skill(name="x", content="a")
        async with self.Session() as db:
            self.assertFalse(await lokal_geaendert(db, sid))
        await self._korrigieren(sid, "b")
        async with self.Session() as db:
            self.assertTrue(await lokal_geaendert(db, sid))

    async def test_freigabe_schnappschuss_zaehlt_nicht(self):
        start = datetime.now(timezone.utc) - timedelta(hours=1)
        sid = await self._skill(name="y", content="a")
        async with self.Session() as db:
            db.add(SkillVersion(skill_id=sid, version_number=1, content="a", created_by="user:x",
                                change_reason=FREIGABE_SCHNAPPSCHUSS))
            await db.commit()
            self.assertFalse(await nach_freigabe_geaendert(db, sid, start))
        await self._korrigieren(sid, "b")
        async with self.Session() as db:
            self.assertTrue(await nach_freigabe_geaendert(db, sid, start))


if __name__ == "__main__":
    unittest.main()
