"""Nutzertrennung an den Endpunkten — „Nutzer A kommt nicht an die Daten von B".

Die Trennung der Nutzer haengt an den Pruefungen der API-Schicht. Eine
Durchsicht aller Router am 01.10.2026 fand Stellen, an denen sie fehlte oder
nur fuer den ALTEN Zustand eines Objekts lief. Jeder Fall hier steht fuer eine
davon. Geprueft wird das Verhalten der Funktion, nicht ihr Quelltext.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from app.models.user import UserRole

MITGLIED = SimpleNamespace(id="ua", role=UserRole.MEMBER)
ADMIN = SimpleNamespace(id="adm", role=UserRole.ADMIN)
EIGENE = {"a1", "a2"}          # Agenten von Nutzer A
FREMD = "b1"                    # Agent von Nutzer B


def _sichtbar(ids=EIGENE):
    return patch("app.core.ownership.visible_agent_ids", AsyncMock(return_value=ids))


async def _status(aufruf) -> int:
    try:
        await aufruf
    except HTTPException as e:
        return e.status_code
    return 200


class Auftraege(unittest.IsolatedAsyncioTestCase):
    def test_zielagent(self):
        from app.api.tasks import _pruefe_zielagent

        _pruefe_zielagent("a1", EIGENE)
        _pruefe_zielagent(None, EIGENE)          # ohne Ziel waehlt der Server unter den eigenen
        _pruefe_zielagent(FREMD, None)           # Administrator
        with self.assertRaises(HTTPException) as fehler:
            _pruefe_zielagent(FREMD, EIGENE)
        self.assertEqual(fehler.exception.status_code, 403)

    async def test_liste_mit_fremdem_agenten_wird_abgewiesen(self):
        """``?agent_id=<fremd>`` hebelte den Filter auf die eigenen Agenten aus."""
        from app.api import tasks

        router = MagicMock(list_tasks=AsyncMock(return_value=[]))
        with patch.object(tasks, "_erreichbare_agenten", AsyncMock(return_value=EIGENE)):
            code = await _status(tasks.list_tasks(agent_id=FREMD, user=MITGLIED, db=MagicMock(), router_=router))
        self.assertEqual(code, 403)
        router.list_tasks.assert_not_awaited()

    async def test_loeschen_und_abbrechen_fremder_auftraege(self):
        from app.api import tasks

        router = MagicMock(delete_task=AsyncMock(), cancel_task=AsyncMock())
        absage = AsyncMock(side_effect=HTTPException(status_code=403, detail="Access denied"))
        with patch.object(tasks, "_assert_task_access", absage):
            self.assertEqual(await _status(tasks.delete_task("t-b", user=MITGLIED, db=MagicMock(), router_=router)), 403)
            self.assertEqual(await _status(tasks.cancel_task("t-b", user=MITGLIED, db=MagicMock(), router_=router)), 403)
            self.assertEqual(await _status(tasks.retain_task("t-b", db=MagicMock(), user=MITGLIED)), 403)
            self.assertEqual(await _status(tasks.release_task("t-b", db=MagicMock(), user=MITGLIED)), 403)
        router.delete_task.assert_not_awaited()
        router.cancel_task.assert_not_awaited()

    async def test_auftrag_auf_fremden_agenten_anlegen(self):
        from app.api import tasks
        from app.schemas.task import TaskCreate

        router = MagicMock(create_and_route_task=AsyncMock())
        with patch.object(tasks, "_erreichbare_agenten", AsyncMock(return_value=EIGENE)):
            code = await _status(tasks.create_task(
                TaskCreate(title="t", prompt="p", agent_id=FREMD), user=MITGLIED, router_=router, db=MagicMock()))
        self.assertEqual(code, 403)
        router.create_and_route_task.assert_not_awaited()

    async def test_ohne_zielagent_waehlt_der_server_nur_unter_den_eigenen(self):
        from app.core.load_balancer import LoadBalancer

        lb = LoadBalancer.__new__(LoadBalancer)
        metrik = lambda aid: SimpleNamespace(agent_id=aid, healthy=True, state="idle", category="", role="")  # noqa: E731
        lb._collect_metrics = AsyncMock(return_value=[metrik(FREMD), metrik("a1")])
        lb._score = lambda m: 0 if m.agent_id == FREMD else 1     # der fremde waere der „beste"
        self.assertEqual(await lb.select_agent(kandidaten=EIGENE), "a1")
        self.assertEqual(await lb.select_agent(kandidaten=None), FREMD)
        self.assertIsNone(await lb.select_agent(kandidaten=set()))


class Teams(unittest.IsolatedAsyncioTestCase):
    async def test_fremder_agent_im_team(self):
        """Ein Team oeffnet die Auftraege aller Mitglieder — gemischt nur durch Admins."""
        from app.api import teams

        with patch.object(teams, "_get_user_agent_ids", AsyncMock(return_value=list(EIGENE))):
            await teams._nur_eigene_agenten(["a1", "a2", None], MITGLIED, MagicMock())
            code = await _status(teams._nur_eigene_agenten(["a1", FREMD], MITGLIED, MagicMock()))
        self.assertEqual(code, 403)
        with patch.object(teams, "_get_user_agent_ids", AsyncMock(return_value=None)):
            await teams._nur_eigene_agenten(["a1", FREMD], ADMIN, MagicMock())

    async def test_anlegen_mit_fremdem_agenten_legt_nichts_an(self):
        from app.api import teams

        db = MagicMock(add=MagicMock(), commit=AsyncMock())
        with patch.object(teams, "_get_user_agent_ids", AsyncMock(return_value=list(EIGENE))):
            code = await _status(teams.create_team(
                teams.CreateTeam(name="X", member_agent_ids=["a1", FREMD]), user=MITGLIED, db=db))
        self.assertEqual(code, 403)
        db.add.assert_not_called()

    async def test_aendern_eines_teams_dem_man_nicht_angehoert(self):
        from app.api import teams
        from app.models.team import Team

        fremdes_team = Team(id="t", name="B", member_agent_ids=[FREMD], lead_agent_id=FREMD, is_active=True)
        with patch.object(teams, "_get_team", AsyncMock(return_value=fremdes_team)), \
             patch.object(teams, "_get_user_agent_ids", AsyncMock(return_value=list(EIGENE))):
            db = MagicMock(commit=AsyncMock())
            self.assertEqual(await _status(teams.delete_team("t", user=MITGLIED, db=db)), 403)
            self.assertEqual(await _status(teams.set_lead("t", teams.SetLead(lead_agent_id=FREMD), user=MITGLIED, db=db)), 403)
            self.assertEqual(await _status(teams.change_members("t", teams.MembersChange(add=["a1"]), user=MITGLIED, db=db)), 403)
            self.assertEqual(await _status(teams.get_team("t", user=MITGLIED, db=db)), 403)
        self.assertTrue(fremdes_team.is_active)


class Workflows(unittest.IsolatedAsyncioTestCase):
    DEF = {"start": "s1", "steps": {"s1": {"type": "agent_task", "agent_id": "a1"}}}
    DEF_FREMD = {"start": "s1", "steps": {"s1": {"type": "agent_task", "agent_id": FREMD}}}

    async def test_schritt_mit_fremdem_agenten(self):
        from app.api import workflows

        with _sichtbar():
            await workflows._pruefe_schritt_agenten(self.DEF, MITGLIED, MagicMock())
            code = await _status(workflows._pruefe_schritt_agenten(self.DEF_FREMD, MITGLIED, MagicMock()))
        self.assertEqual(code, 403)
        with _sichtbar(None):
            await workflows._pruefe_schritt_agenten(self.DEF_FREMD, ADMIN, MagicMock())

    async def test_geteilter_workflow_laeuft_mit_den_agenten_des_besitzers(self):
        from app.api import workflows

        besitzer = SimpleNamespace(id="ub", role=UserRole.MEMBER)
        db = MagicMock(get=AsyncMock(return_value=besitzer))

        async def sicht(user, _db):
            return {FREMD} if user is besitzer else EIGENE

        with patch("app.core.ownership.visible_agent_ids", sicht):
            await workflows._pruefe_schritt_agenten(self.DEF_FREMD, MITGLIED, db, besitzer_id="ub")

    async def test_workflow_ohne_besitzer_gehoert_nicht_jedem(self):
        from app.api import workflows

        ohne = SimpleNamespace(user_id=None)
        self.assertFalse(workflows._is_owner(ohne, MITGLIED))
        self.assertTrue(workflows._is_owner(ohne, ADMIN))
        self.assertTrue(workflows._is_owner(SimpleNamespace(user_id="ua"), MITGLIED))


class RegelnUndAusloeser(unittest.IsolatedAsyncioTestCase):
    async def _regel_aendern(self, aenderung, user=MITGLIED):
        from app.api import approval_rules
        from app.models.approval_rule import ApprovalRule

        regel = ApprovalRule(id=1, name="r", category="c", agent_id="a1", created_by="ua")
        db = MagicMock(scalar=AsyncMock(return_value=regel), commit=AsyncMock(), refresh=AsyncMock(), add=MagicMock())
        with _sichtbar(None if user is ADMIN else EIGENE):
            code = await _status(approval_rules.update_rule(1, approval_rules.UpdateRule(**aenderung), user=user, db=db))
        return code, regel

    async def test_eigene_regel_auf_fremden_agenten_umhaengen(self):
        code, regel = await self._regel_aendern({"agent_id": FREMD})
        self.assertEqual(code, 403)
        self.assertEqual(regel.agent_id, "a1")

    async def test_eigene_regel_global_machen(self):
        """``agent_id = null`` macht aus der Regel eine fuer ALLE Agenten."""
        code, regel = await self._regel_aendern({"agent_id": None})
        self.assertEqual(code, 403)
        self.assertEqual(regel.agent_id, "a1")

    async def test_andere_felder_bleiben_aenderbar(self):
        code, regel = await self._regel_aendern({"name": "neu"})
        self.assertEqual(code, 200)
        self.assertEqual(regel.name, "neu")

    async def test_ausloeser_darf_keinen_fremden_workflow_starten(self):
        from app.api import event_triggers

        fremd = SimpleNamespace(user_id="ub")
        db = MagicMock(get=AsyncMock(return_value=fremd))
        self.assertEqual(await _status(event_triggers._pruefe_workflow("wf", "ua", False, db)), 403)
        db.get = AsyncMock(return_value=SimpleNamespace(user_id="ua"))
        await event_triggers._pruefe_workflow("wf", "ua", False, db)
        db.get = AsyncMock(return_value=None)
        self.assertEqual(await _status(event_triggers._pruefe_workflow("wf", "ua", False, db)), 403)
        await event_triggers._pruefe_workflow(None, "ua", False, db)


class Zeitplaene(unittest.IsolatedAsyncioTestCase):
    async def test_zeitplan_auf_fremden_agenten(self):
        from app.api import schedules, tasks

        with patch.object(tasks, "_erreichbare_agenten", AsyncMock(return_value=EIGENE)):
            daten = schedules.ScheduleCreate(name="n", prompt="p", agent_id=FREMD, interval_seconds=3600)
            code = await _status(schedules.create_schedule(daten, user=MITGLIED, db=MagicMock(), redis=MagicMock()))
        self.assertEqual(code, 403)


class GeteilteVerbindungen(unittest.TestCase):
    def test_nur_admin_aendert_was_fuer_alle_gilt(self):
        from app.api.integrations import _geteilte_nur_admin

        with self.assertRaises(HTTPException) as fehler:
            _geteilte_nur_admin("github", MITGLIED)
        self.assertEqual(fehler.exception.status_code, 403)
        _geteilte_nur_admin("github", ADMIN)
        _geteilte_nur_admin("microsoft", MITGLIED)      # je Nutzer, also seine eigene


class Skills(unittest.IsolatedAsyncioTestCase):
    def _db(self, ersteller):
        skill = SimpleNamespace(id=1, created_by=ersteller)
        ergebnis = MagicMock(scalar_one_or_none=MagicMock(return_value=skill))
        return MagicMock(execute=AsyncMock(return_value=ergebnis))

    async def _aendern(self, ersteller, user=MITGLIED):
        from app.api import skill_marketplace

        with _sichtbar():
            return await _status(skill_marketplace._darf_skill_aendern(1, user, self._db(ersteller)))

    async def test_eigener_skill(self):
        self.assertEqual(await self._aendern("user:ua"), 200)

    async def test_skill_des_eigenen_agenten(self):
        self.assertEqual(await self._aendern("agent:a1"), 200)

    async def test_skill_eines_fremden_nutzers_oder_agenten(self):
        self.assertEqual(await self._aendern("user:ub"), 403)
        self.assertEqual(await self._aendern(f"agent:{FREMD}"), 403)

    async def test_alter_skill_ohne_nutzerkennung_gehoert_den_admins(self):
        self.assertEqual(await self._aendern("user"), 403)
        self.assertEqual(await self._aendern("import:github"), 403)
        self.assertEqual(await self._aendern("user", ADMIN), 200)

    async def test_freigeben_ist_administratorsache(self):
        from app.api import skill_marketplace

        self.assertEqual(await _status(skill_marketplace._nur_admin(MITGLIED)), 403)
        self.assertEqual(await _status(skill_marketplace._nur_admin(ADMIN)), 200)


class OhneAnmeldung(unittest.IsolatedAsyncioTestCase):
    """Endpunkte, die ohne jede Anmeldung erreichbar waren."""

    def _verlangt_anmeldung(self, funktion) -> bool:
        import inspect

        from app import dependencies as dep

        erlaubt = {dep.require_auth, dep.require_admin, dep.require_auth_or_agent, dep.verify_agent_token}
        return any(getattr(p.default, "dependency", None) in erlaubt
                   for p in inspect.signature(funktion).parameters.values())

    def test_diese_endpunkte_verlangen_jetzt_eine_anmeldung(self):
        from app.api import notifications, skills_catalog, url_allowlist

        for funktion in (skills_catalog.get_skill_catalog, skills_catalog.refresh_skill_catalog,
                         url_allowlist.list_templates, url_allowlist.check_url,
                         notifications.get_approval_result):
            self.assertTrue(self._verlangt_anmeldung(funktion), funktion.__name__)

    async def test_freigabe_antwort_nur_fuer_den_eigenen_agenten(self):
        from app.api import notifications
        from app.dependencies import AgentPrincipal

        notiz = SimpleNamespace(id=5, type="approval", agent_id=FREMD, meta={"response": "ja"})
        ergebnis = MagicMock(scalar_one_or_none=MagicMock(return_value=notiz))
        db = MagicMock(execute=AsyncMock(return_value=ergebnis))
        with _sichtbar():
            self.assertEqual(await _status(notifications.get_approval_result(5, user=MITGLIED, db=db)), 404)
        fremder_agent = AgentPrincipal(id="a1", username="agent-a1")
        self.assertEqual(await _status(notifications.get_approval_result(5, user=fremder_agent, db=db)), 404)
        eigener_agent = AgentPrincipal(id=FREMD, username="agent-b1")
        self.assertEqual((await notifications.get_approval_result(5, user=eigener_agent, db=db))["choice"], "ja")

    async def test_mcp_zugang_ohne_token_ist_zu(self):
        """Eingeschaltet, aber kein Token hinterlegt, hiess bisher: offen."""
        from app.api import mcp_agent

        agent = SimpleNamespace(id="a1", webhook_enabled=True, webhook_token="")
        ergebnis = MagicMock(scalar_one_or_none=MagicMock(return_value=agent))
        db = MagicMock(execute=AsyncMock(return_value=ergebnis))
        anfrage = MagicMock(headers={})
        self.assertEqual(await _status(mcp_agent._auth_agent("a1", anfrage, db)), 403)
        agent.webhook_token = "geheim"
        self.assertEqual(await _status(mcp_agent._auth_agent("a1", anfrage, db)), 401)
        anfrage.headers = {"Authorization": "Bearer geheim"}
        self.assertEqual(await _status(mcp_agent._auth_agent("a1", anfrage, db)), 200)


class LogStroeme(unittest.IsolatedAsyncioTestCase):
    async def _darf(self, zugriff):
        from app.api import ws

        socket = MagicMock(close=AsyncMock())
        socket.state.user_id = "ua"
        with patch.object(ws, "_ws_nutzer", AsyncMock(return_value=MITGLIED)), \
             patch.object(ws, "async_session_factory", MagicMock()), \
             patch("app.dependencies.require_agent_access", zugriff):
            return await ws._ws_darf_agent(socket, FREMD), socket

    async def test_fremder_agent_schliesst_die_verbindung(self):
        darf, socket = await self._darf(AsyncMock(side_effect=HTTPException(status_code=403, detail="x")))
        self.assertFalse(darf)
        self.assertEqual(socket.close.await_args.kwargs["code"], 4003)

    async def test_eigener_agent(self):
        darf, socket = await self._darf(AsyncMock())
        self.assertTrue(darf)
        socket.close.assert_not_awaited()

    async def test_fehler_in_der_pruefung_heisst_zu(self):
        darf, _socket = await self._darf(AsyncMock(side_effect=RuntimeError("db weg")))
        self.assertFalse(darf)


class VorlagenUndSammlungen(unittest.IsolatedAsyncioTestCase):
    async def test_mitgelieferte_url_vorlage_aendert_nur_der_admin(self):
        from app.api import url_allowlist

        mitgeliefert = SimpleNamespace(created_by=None)
        self.assertEqual(await _status(url_allowlist._assert_template_owned(mitgeliefert, MITGLIED, MagicMock())), 404)
        await url_allowlist._assert_template_owned(mitgeliefert, ADMIN, MagicMock())
        await url_allowlist._assert_template_owned(SimpleNamespace(created_by="ua"), MITGLIED, MagicMock())
        self.assertEqual(
            await _status(url_allowlist._assert_template_owned(SimpleNamespace(created_by="ub"), MITGLIED, MagicMock())), 404)

    async def test_mitgelieferte_testsammlung(self):
        from app.api import evals

        def db(besitzer):
            zeile = SimpleNamespace(id="s", user_id=besitzer)
            return MagicMock(execute=AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=zeile))))

        await evals._owned_set(db(None), "s", MITGLIED)                                    # benutzen: ja
        self.assertEqual(await _status(evals._owned_set(db(None), "s", MITGLIED, aendern=True)), 403)
        await evals._owned_set(db(None), "s", ADMIN, aendern=True)
        self.assertEqual(await _status(evals._owned_set(db("ub"), "s", MITGLIED)), 403)
        await evals._owned_set(db("ua"), "s", MITGLIED, aendern=True)

    async def test_rollen_verraten_nichtadmins_keine_freigaben(self):
        from app.api import roles

        rolle = SimpleNamespace(id=1, name="R", description="d", permissions={"secret_ids": [1, 2]}, is_system=False)
        ergebnis = MagicMock()
        ergebnis.scalars.return_value.all.return_value = [rolle]
        db = MagicMock(execute=AsyncMock(return_value=ergebnis))
        self.assertEqual((await roles.list_roles(user=MITGLIED, db=db))["roles"][0]["permissions"], {})
        self.assertEqual((await roles.list_roles(user=ADMIN, db=db))["roles"][0]["permissions"], {"secret_ids": [1, 2]})


if __name__ == "__main__":
    unittest.main()


class AgentUndSeineKollegen(unittest.IsolatedAsyncioTestCase):
    """Ein Agenten-Token gilt fuer die Agenten SEINES Besitzers und seines Teams."""

    async def asyncSetUp(self):
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.models.agent import Agent
        from app.models.agent_access import AgentAccess
        from app.models.team import Team

        # Die Teams-Tabelle nutzt JSONB; fuer die Testdatenbank genuegt JSON.
        from sqlalchemy.dialects.postgresql import JSONB
        from sqlalchemy.ext.compiler import compiles

        @compiles(JSONB, "sqlite")
        def _jsonb_als_json(_typ, _compiler, **_kw):
            return "JSON"

        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            for model in (Agent, AgentAccess, Team):
                await conn.run_sync(model.metadata.create_all, tables=[model.__table__])
        self.sitzung = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.sitzung() as db:
            db.add_all([
                Agent(id="a1", name="A1", user_id="ua"), Agent(id="a2", name="A2", user_id="ua"),
                Agent(id="b1", name="B1", user_id="ub"), Agent(id="b2", name="B2", user_id="ub"),
                Agent(id="p1", name="Plattform", user_id=None, is_platform_agent=True),
                Team(id="t", name="Gemischt", member_agent_ids=["a1", "b1"], lead_agent_id="a1", is_active=True),
                Team(id="alt", name="Aufgelöst", member_agent_ids=["a1", "b2"], lead_agent_id="a1", is_active=False),
            ])
            await db.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _erreichbar(self, agent_id):
        from app.api.tasks import _erreichbare_agenten
        from app.dependencies import AgentPrincipal

        async with self.sitzung() as db:
            return await _erreichbare_agenten(AgentPrincipal(id=agent_id, username="x"), db)

    async def test_eigene_plattform_und_team(self):
        self.assertEqual(await self._erreichbar("a1"), {"a1", "a2", "p1", "b1"})

    async def test_agent_ohne_team_bleibt_bei_seinem_besitzer(self):
        self.assertEqual(await self._erreichbar("a2"), {"a1", "a2", "p1"})

    async def test_aufgeloestes_team_zaehlt_nicht(self):
        self.assertNotIn("b2", await self._erreichbar("a1"))

    async def test_unbekannter_agent_erreicht_nur_sich_selbst(self):
        self.assertEqual(await self._erreichbar("gibt-es-nicht"), {"gibt-es-nicht"})
