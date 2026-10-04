"""Die Vorlagen-Liste nennt die Skills jeder Vorlage beim Namen.

Bisher lieferte die API nur ``skill_ids`` — die Oberfläche zeigte sie gar nicht an,
niemand sah, was eine Vorlage mitbringt. Jetzt kommen Name + Beschreibung mit,
in einer einzigen Abfrage für alle Vorlagen.
"""

import unittest
from types import SimpleNamespace

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.templates import get_template, list_templates
from app.models.agent_template import AgentTemplate
from app.models.skill import Skill, SkillCategory, SkillStatus
from app.models.user import UserRole


class VorlageZeigtSkillsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(lambda c: Skill.__table__.create(c))
            await conn.run_sync(lambda c: AgentTemplate.__table__.create(c))
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.Session() as db:
            for i, (name, beschr) in enumerate([("docx", "Word-Dateien"), ("buchhaltung-vorkontieren", "Belege vorkontieren")], 1):
                db.add(Skill(id=i, name=name, description=beschr, content="x", category=SkillCategory.TEMPLATE,
                             status=SkillStatus.ACTIVE, created_by="test"))
            db.add(AgentTemplate(name="bookkeeper", display_name="Buchhaltung", description="", icon="", category="finance",
                                 model="", role="", permissions=[], integrations=[], mcp_server_ids=[],
                                 skill_ids=[2, 1, 99], knowledge_template="", is_builtin=True, is_published=True))
            await db.commit()
        self.user = SimpleNamespace(id="u1", role=UserRole.ADMIN)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_liste_nennt_skills_in_reihenfolge_ohne_verwaiste_ids(self):
        async with self.Session() as db:
            out = await list_templates(user=self.user, db=db)
        skills = out["templates"][0]["skills"]
        self.assertEqual([s["name"] for s in skills], ["buchhaltung-vorkontieren", "docx"])
        self.assertEqual(skills[0]["description"], "Belege vorkontieren")

    async def test_einzelne_vorlage_ebenso(self):
        async with self.Session() as db:
            tid = (await list_templates(user=self.user, db=db))["templates"][0]["id"]
            out = await get_template(template_id=tid, user=self.user, db=db)
        self.assertEqual(len(out["skills"]), 2)


if __name__ == "__main__":
    unittest.main()
