"""Agenten aus einer Vorlage — auch auf dem allgemeinen Anlege-Weg.

Das Anlegen-Fenster nimmt ``/templates/{id}/create-agent`` nur fuer Claude Code
ohne KI-Konto. Mit KI-Konto (auf Kundenanlagen der Normalfall), Codex oder
Custom-LLM geht es ueber ``POST /agents`` — und dort ging die Vorlage bisher
verloren: kein ``template_id``, kein Wissen, keine Daueraufgaben, keine Skills
und seit den Build-Werkzeugen auch keine .exe-Werkzeuge.

Weil die Vorlage mitbestimmt, womit der Container gebaut wird, gilt auf beiden
Wegen DIESELBE Zugriffspruefung (``vorlage_fuer_nutzer``). MCDC-Matrix unten.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from app.api import templates as templates_api
from app.models.agent_template import AgentTemplate
from app.models.user import UserRole


def _nutzer(rolle=UserRole.MEMBER, uid="u1"):
    return SimpleNamespace(id=uid, role=rolle)


def _db(vorlage):
    db = MagicMock()
    db.scalar = AsyncMock(return_value=vorlage)
    return db


def _vorlage(veroeffentlicht=True):
    return AgentTemplate(id=7, name="fullstack-developer", display_name="Fullstack",
                         is_published=veroeffentlicht, build_tools=True)


class VorlageFuerNutzer(unittest.IsolatedAsyncioTestCase):
    """MCDC: gibt es sie? | veroeffentlicht? | Admin/Manager? | fuer die Rolle freigegeben?"""

    async def _pruefe(self, vorlage, nutzer, erlaubte_ids=None):
        with patch("app.core.permissions.get_effective_permissions",
                   AsyncMock(return_value={"template_ids": erlaubte_ids})):
            return await templates_api.vorlage_fuer_nutzer(7, nutzer, _db(vorlage))

    async def test_gibt_es_nicht(self):
        with self.assertRaises(HTTPException) as fehler:
            await self._pruefe(None, _nutzer())
        self.assertEqual(fehler.exception.status_code, 404)

    async def test_mitglied_unveroeffentlicht(self):
        with self.assertRaises(HTTPException) as fehler:
            await self._pruefe(_vorlage(veroeffentlicht=False), _nutzer())
        self.assertEqual(fehler.exception.status_code, 403)

    async def test_admin_darf_unveroeffentlichte(self):
        vorlage = await self._pruefe(_vorlage(veroeffentlicht=False), _nutzer(UserRole.ADMIN))
        self.assertEqual(vorlage.id, 7)

    async def test_rolle_ohne_freigabe(self):
        with self.assertRaises(HTTPException) as fehler:
            await self._pruefe(_vorlage(), _nutzer(), erlaubte_ids=[1, 2])
        self.assertEqual(fehler.exception.status_code, 403)

    async def test_rolle_mit_freigabe(self):
        vorlage = await self._pruefe(_vorlage(), _nutzer(), erlaubte_ids=[7])
        self.assertEqual(vorlage.id, 7)

    async def test_ohne_einschraenkung(self):
        vorlage = await self._pruefe(_vorlage(), _nutzer(), erlaubte_ids=None)
        self.assertEqual(vorlage.id, 7)


class AllgemeinerAnlegeWeg(unittest.IsolatedAsyncioTestCase):
    """``POST /agents`` mit ``template_id``: pruefen, weiterreichen, anwenden."""

    async def _anlegen(self, template_id, pruefung):
        from app.api import agents as agents_api
        from app.schemas.agent import AgentCreate

        manager = MagicMock()
        manager.create_agent = AsyncMock(return_value=SimpleNamespace(id="abc12345"))
        manager.get_agent_with_metrics = AsyncMock(return_value={
            "id": "abc12345", "name": "Ben", "container_id": "c", "state": "running", "model": "m",
            "created_at": "2026-09-29T10:00:00", "updated_at": "2026-09-29T10:00:00",
        })
        anwenden = AsyncMock()
        with patch.object(templates_api, "vorlage_fuer_nutzer", pruefung), \
             patch.object(templates_api, "vorlage_anwenden", anwenden):
            await agents_api.create_agent(
                AgentCreate(name="Ben", template_id=template_id),
                user=SimpleNamespace(id="__anonymous__", role=UserRole.ADMIN),
                manager=manager, db=MagicMock(),
            )
        return manager, anwenden

    async def test_vorlage_wird_mitgegeben_und_angewendet(self):
        manager, anwenden = await self._anlegen(7, AsyncMock(return_value=_vorlage()))
        self.assertEqual(manager.create_agent.call_args.kwargs["template_id"], 7)
        anwenden.assert_awaited_once()

    async def test_nicht_freigegebene_vorlage_wird_abgewiesen(self):
        verboten = AsyncMock(side_effect=HTTPException(status_code=403, detail="nein"))
        with self.assertRaises(HTTPException) as fehler:
            await self._anlegen(7, verboten)
        self.assertEqual(fehler.exception.status_code, 403)

    async def test_ohne_vorlage_unveraendert(self):
        pruefung = AsyncMock()
        manager, anwenden = await self._anlegen(None, pruefung)
        pruefung.assert_not_awaited()
        anwenden.assert_not_awaited()
        self.assertIsNone(manager.create_agent.call_args.kwargs["template_id"])


if __name__ == "__main__":
    unittest.main()
