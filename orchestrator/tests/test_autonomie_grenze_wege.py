"""Die Autonomie-Grenze der Rolle gilt auf JEDEM Weg (#910).

Markttest: ein Mitglied legte aus der Vorlage „DevOps" einen Agenten an und
bekam ohne Rueckfrage vollen Root-Zugriff; jede Vorlage pinnte den Agenten auf
handverlesene Sudo-Pakete (``[]`` statt ``None``); Mitglieder konnten L4
einstellen und sich per Freigaberegel freischalten, was L3 bewusst sperrt.

Geprueft wird am echten Ablauf:

    Weg / Fall                                   Mitglied (L3)        Admin (ohne Grenze)
    Anlegen aus DevOps-Vorlage                   Auto, kein Root      Pakete ohne Root
    Anlegen mit Root + Bestaetigung              Auto, kein Root      Root
    Anlegen L4 ausdruecklich                     403, kein Container  L4
    Anlegen ohne Stufe (Betrachter L1)           L1                   L3
    Stufe L4 setzen, bisher L3                   403                  ok
    Stufe L4 halten, bisher L4 (Bestand)         ok                   ok
    Freigaberegel „custom" am eigenen Agenten    403                  ok
    Freigaberegel „shell_exec" (bis L3 frei)     ok                   ok
    Root nachtraeglich ohne Bestaetigung         403                  400
"""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from app.core import autonomie_grenze as ag
from app.core import autonomy_matrix as am
from app.core.agent_manager import AgentManager
from app.models.agent_template import AgentTemplate
from app.models.user import User, UserRole

ADMIN = SimpleNamespace(id="admin1", role=UserRole.ADMIN, custom_role_id=None, name="A", email="a@example.com")
MITGLIED = SimpleNamespace(id="mit1", role=UserRole.MEMBER, custom_role_id=None, name="M", email="m@example.com")
BETRACHTER = SimpleNamespace(id="bet1", role=UserRole.VIEWER, custom_role_id=None, name="B", email="b@example.com")
NUTZER = {u.id: u for u in (ADMIN, MITGLIED, BETRACHTER)}

DEVOPS_PAKETE = ["package-install", "system-config", "full-access"]


def _docker():
    docker = MagicMock()
    docker.create_container.return_value = SimpleNamespace(id="neuer-container")
    return docker


def _db():
    db = MagicMock()

    async def _get(modell, schluessel):
        if modell is User:
            return NUTZER.get(schluessel)
        return None

    db.get = AsyncMock(side_effect=_get)
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.flush = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock())
    db.scalar = AsyncMock(return_value=None)
    return db


def _manager(db, docker):
    m = AgentManager(db, docker, MagicMock())
    for name in ("_publish_event", "_cancel_open_chats", "migrate_knowledge_file"):
        setattr(m, name, AsyncMock())
    m._agent_redis_url = AsyncMock(return_value="redis://redis:6379")
    m._effective_llm_config = AsyncMock(return_value=None)
    m._get_custom_mcp_env = AsyncMock(return_value={})
    m._get_integration_env = AsyncMock(return_value={})
    m._owner_credential_env = AsyncMock(return_value={})
    m._apply_permissions = MagicMock()
    m._update_team_registry = MagicMock()
    return m


_UMGEBUNG = (
    patch("app.core.master_rules.load", AsyncMock(return_value="")),
    patch("app.core.agentenlimit.pruefe_agentenlimit", AsyncMock()),
)


class _Basis(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        for p in _UMGEBUNG:
            p.start()
            self.addCleanup(p.stop)

    async def anlegen(self, besitzer, **kw):
        docker = _docker()
        m = _manager(_db(), docker)
        agent = await m.create_agent(name="Neu", user_id=besitzer.id if besitzer else None, **kw)
        sudo = m._apply_permissions.call_args.args[1]
        return agent, sudo, docker


class AnlegenZentral(_Basis):
    async def test_mitglied_aus_devops_vorlage_bekommt_keinen_root(self):
        agent, sudo, _ = await self.anlegen(MITGLIED, permissions=list(DEVOPS_PAKETE))
        self.assertNotIn("full-access", sudo)
        self.assertEqual(agent.access_policy["permissions_mode"], "auto")
        self.assertEqual(sudo, am.effective_permissions({}, "l3"))

    async def test_mitglied_auch_mit_bestaetigung_kein_root(self):
        agent, sudo, _ = await self.anlegen(MITGLIED, permissions=["full-access"], root_bestaetigt=True)
        self.assertNotIn("full-access", sudo)
        self.assertEqual(agent.access_policy["permissions_mode"], "auto")

    async def test_admin_ohne_bestaetigung_kein_root(self):
        agent, sudo, _ = await self.anlegen(ADMIN, permissions=list(DEVOPS_PAKETE))
        self.assertEqual(sorted(sudo), ["package-install", "system-config"])
        self.assertEqual(agent.access_policy["permissions_mode"], "manual")

    async def test_admin_mit_bestaetigung_bekommt_root(self):
        _, sudo, _ = await self.anlegen(ADMIN, permissions=list(DEVOPS_PAKETE), root_bestaetigt=True)
        self.assertIn("full-access", sudo)

    async def test_ohne_paketliste_folgt_der_stufe(self):
        agent, sudo, _ = await self.anlegen(ADMIN, permissions=None)
        self.assertEqual(agent.access_policy["permissions_mode"], "auto")

    async def test_mitglied_l4_ausdruecklich_wird_abgelehnt_bevor_ein_container_entsteht(self):
        docker = _docker()
        m = _manager(_db(), docker)
        with self.assertRaises(HTTPException) as f:
            await m.create_agent(name="Neu", user_id=MITGLIED.id, autonomy_level="l4")
        self.assertEqual(f.exception.status_code, 403)
        docker.create_container.assert_not_called()

    async def test_admin_darf_l4(self):
        agent, _, _ = await self.anlegen(ADMIN, autonomy_level="l4")
        self.assertEqual(agent.autonomy_level, "l4")

    async def test_ohne_stufenangabe_hoechstens_die_grenze(self):
        agent, _, docker = await self.anlegen(BETRACHTER)
        self.assertEqual(agent.autonomy_level, "l1")
        self.assertEqual(docker.create_container.call_args.kwargs["environment"]["AUTONOMY_LEVEL"], "l1")
        agent, _, _ = await self.anlegen(MITGLIED)
        self.assertEqual(agent.autonomy_level, "l3")

    async def test_unbekannter_besitzer_schliesst(self):
        docker = _docker()
        m = _manager(_db(), docker)
        agent = await m.create_agent(name="Neu", user_id="gibtsnicht")
        self.assertEqual(agent.autonomy_level, "l1")


class VorlagenWeg(unittest.IsolatedAsyncioTestCase):
    """Die Vorlage uebergibt ``None`` statt ``[]`` und reicht die Bestaetigung durch."""

    async def _lauf(self, pakete, body=None):
        from app.api import templates as api

        vorlage = SimpleNamespace(id=1, name="v", display_name="V", model="m", role="r",
                                  integrations=[], permissions=pakete, is_published=True)
        with patch.object(api, "vorlage_fuer_nutzer", AsyncMock(return_value=vorlage)), \
             patch.object(api, "vorlage_anwenden", AsyncMock()), \
             patch.object(api, "AgentManager") as manager:
            manager.return_value.create_agent = AsyncMock(return_value=SimpleNamespace(id="x"))
            manager.return_value.get_agent_with_metrics = AsyncMock(return_value={})
            await api.create_agent_from_template(
                1, body or api.CreateFromTemplate(), request=MagicMock(),
                user=SimpleNamespace(id="__anonymous__", role=None),
                db=MagicMock(), docker=MagicMock(), redis=MagicMock(),
            )
            return manager.return_value.create_agent.call_args.kwargs

    async def test_vorlage_ohne_pakete_pinnt_nicht_auf_manuell(self):
        kw = await self._lauf([])
        self.assertIsNone(kw["permissions"])

    async def test_root_nur_mit_bestaetigung_aus_dem_fenster(self):
        from app.api import templates as api

        kw = await self._lauf(list(DEVOPS_PAKETE))
        self.assertFalse(kw["root_bestaetigt"])
        kw = await self._lauf(list(DEVOPS_PAKETE), api.CreateFromTemplate(root_bestaetigt=True))
        self.assertTrue(kw["root_bestaetigt"])


class AendernMitBestandsschutz(unittest.IsolatedAsyncioTestCase):
    async def _stufe(self, user, neu, bisher):
        from app.services import agent_settings

        agent = SimpleNamespace(id="a1", autonomy_level=bisher, access_policy={}, container_id=None)
        db = MagicMock()
        db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=agent)))
        db.commit = AsyncMock()
        db.add = MagicMock()
        with patch("app.dependencies.require_agent_access", AsyncMock()), \
             patch("app.api.approval_rules.apply_autonomy_preset", AsyncMock(return_value=[])):
            await agent_settings.change_autonomy_level(db, user, "a1", neu)
        return agent, db

    async def test_mitglied_kann_nicht_auf_l4_erhoehen(self):
        with self.assertRaises(HTTPException) as f:
            await self._stufe(MITGLIED, "l4", "l3")
        self.assertEqual(f.exception.status_code, 403)

    async def test_bestand_bleibt_und_darf_gehalten_oder_gesenkt_werden(self):
        agent, _ = await self._stufe(MITGLIED, "l4", "l4")
        self.assertEqual(agent.autonomy_level, "l4")
        agent, _ = await self._stufe(MITGLIED, "l2", "l4")
        self.assertEqual(agent.autonomy_level, "l2")

    async def test_matrix_bestand(self):
        l4 = am.matrix_for_level("l4")
        await ag.pruefe_matrix(MITGLIED, _db(), l4, bisher=l4)
        strenger = dict(l4, purchases=am.ASK)
        await ag.pruefe_matrix(MITGLIED, _db(), strenger, bisher=l4)
        with self.assertRaises(HTTPException):
            await ag.pruefe_matrix(MITGLIED, _db(), l4, bisher=am.matrix_for_level("l3"))

    async def test_sudo_bestand(self):
        await ag.pruefe_sudo_pakete(MITGLIED, _db(), ["full-access"], bisher=["full-access"])
        await ag.pruefe_sudo_pakete(MITGLIED, _db(), [], bisher=["full-access"])
        with self.assertRaises(HTTPException):
            await ag.pruefe_sudo_pakete(MITGLIED, _db(), ["package-install"], bisher=[])


class ZugriffsRichtlinie(unittest.IsolatedAsyncioTestCase):
    """PUT /agents/{id}/access-policy und PATCH /agents/{id}/permissions."""

    def _agent(self, policy=None):
        return SimpleNamespace(id="a1", autonomy_level="l3", access_policy=policy or {}, container_id=None)

    async def _policy(self, user, agent, **body):
        from app.api import agents as api

        db = MagicMock()
        db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=agent)))
        db.commit = AsyncMock()
        with patch.object(api, "_check_owner", AsyncMock()), \
             patch.object(api, "_sync_container_sudo", AsyncMock(return_value=[])), \
             patch("app.api.agents.flag_modified", MagicMock(), create=True), \
             patch("sqlalchemy.orm.attributes.flag_modified", MagicMock()):
            return await api.update_access_policy("a1", api.AccessPolicyUpdate(**body), user=user,
                                                  db=db, manager=MagicMock()), db

    async def test_mitglied_kein_root_ueber_die_richtlinie(self):
        with self.assertRaises(HTTPException) as f:
            await self._policy(MITGLIED, self._agent(), permissions=["full-access"])
        self.assertEqual(f.exception.status_code, 403)

    async def test_admin_root_nur_mit_bestaetigung(self):
        with self.assertRaises(HTTPException) as f:
            await self._policy(ADMIN, self._agent(), permissions=["full-access"])
        self.assertEqual(f.exception.status_code, 400)
        agent = self._agent()
        await self._policy(ADMIN, agent, permissions=["full-access"], root_bestaetigt=True)
        self.assertEqual(agent.access_policy["permissions"], ["full-access"])

    async def test_mitglied_matrix_ueber_der_grenze(self):
        with self.assertRaises(HTTPException) as f:
            await self._policy(MITGLIED, self._agent(), matrix=am.matrix_for_level("l4"))
        self.assertEqual(f.exception.status_code, 403)

    async def test_mitglied_darf_bestehenden_root_behalten(self):
        agent = self._agent({"permissions": ["full-access"], "permissions_mode": "manual"})
        await self._policy(MITGLIED, agent, permissions=["full-access"])

    # Computer-Use-Standard (Sicherheitspruefung v1.362.0, F3): Shell auf dem
    # Rechner des Menschen, Tastatur-/Mikrofon-Mitschnitt und Browser in einer
    # angemeldeten Sitzung sind mindestens so viel wie Shell im Container. Ein
    # begrenzter Nutzer schaltet sie nur frei, wenn die Matrix seines Agenten
    # ``shell_exec`` ohne Rueckfrage erlaubt.
    #
    #   Fall                                      Grenze   Matrix shell_exec   Ergebnis
    #   Mitglied, Agent L2, neu „shell"           L3       ask                 403
    #   Mitglied, Agent L3, neu „shell"           L3       allow               ok
    #   Mitglied, Agent L2, nur harmlose Gruppen  L3       ask                 ok
    #   Mitglied, Agent L2, „shell" schon da      L3       ask                 ok (Bestand)
    #   Admin, Agent L1, neu „ego_browser"        -        ask                 ok
    #   Mitglied, gleichzeitig Matrix gesenkt     L3       ask (neu)           403
    async def test_computer_use_sensible_gruppe_ohne_shell_freigabe(self):
        for gruppe in ("shell", "input_capture", "voice_capture", "browser", "ego_browser"):
            agent = SimpleNamespace(id="a1", autonomy_level="l2", access_policy={}, container_id=None)
            with self.assertRaises(HTTPException) as f:
                await self._policy(MITGLIED, agent, computer_use_default_capabilities=["screenshots", gruppe])
            self.assertEqual(f.exception.status_code, 403, gruppe)
            self.assertNotIn("computer_use_default_capabilities", agent.access_policy)

    async def test_computer_use_shell_wenn_matrix_shell_erlaubt(self):
        agent = self._agent()  # L3: shell_exec = allow
        await self._policy(MITGLIED, agent, computer_use_default_capabilities=["shell", "browser"])
        self.assertEqual(agent.access_policy["computer_use_default_capabilities"], ["shell", "browser"])

    async def test_computer_use_harmlose_gruppen_immer(self):
        agent = SimpleNamespace(id="a1", autonomy_level="l1", access_policy={}, container_id=None)
        await self._policy(BETRACHTER, agent, computer_use_default_capabilities=["screenshots", "mouse"])
        self.assertEqual(agent.access_policy["computer_use_default_capabilities"], ["screenshots", "mouse"])

    async def test_computer_use_bestand_bleibt(self):
        agent = SimpleNamespace(id="a1", autonomy_level="l2", container_id=None,
                                access_policy={"computer_use_default_capabilities": ["shell", "mouse"]})
        await self._policy(MITGLIED, agent, computer_use_default_capabilities=["shell"])
        self.assertEqual(agent.access_policy["computer_use_default_capabilities"], ["shell"])

    async def test_computer_use_admin_unbegrenzt(self):
        agent = SimpleNamespace(id="a1", autonomy_level="l1", access_policy={}, container_id=None)
        await self._policy(ADMIN, agent, computer_use_default_capabilities=["ego_browser"])
        self.assertEqual(agent.access_policy["computer_use_default_capabilities"], ["ego_browser"])

    async def test_computer_use_gegen_die_neue_matrix_geprueft(self):
        agent = self._agent()  # L3
        with self.assertRaises(HTTPException) as f:
            await self._policy(MITGLIED, agent, matrix=am.matrix_for_level("l2"),
                               computer_use_default_capabilities=["shell"])
        self.assertEqual(f.exception.status_code, 403)

    async def test_patch_permissions_root_braucht_bestaetigung(self):
        from app.api import agents as api

        agent = self._agent()
        manager = MagicMock()
        manager._get_agent = AsyncMock(return_value=agent)
        db = MagicMock(commit=AsyncMock())
        with patch.object(api, "_check_owner", AsyncMock()), \
             patch("sqlalchemy.orm.attributes.flag_modified", MagicMock()):
            with self.assertRaises(HTTPException) as f:
                await api.update_agent_permissions(
                    "a1", api.PermissionsUpdate(permissions=["full-access"]), user=ADMIN, db=db, manager=manager)
            self.assertEqual(f.exception.status_code, 400)
            await api.update_agent_permissions(
                "a1", api.PermissionsUpdate(permissions=["full-access"], root_bestaetigt=True),
                user=ADMIN, db=db, manager=manager)
        self.assertEqual(agent.access_policy["permissions"], ["full-access"])


class Freigaberegeln(unittest.IsolatedAsyncioTestCase):
    async def _regel(self, user, kategorie):
        from app.api import approval_rules as api

        db = MagicMock()
        db.add = MagicMock()
        db.commit = AsyncMock()
        db.refresh = AsyncMock()
        db.get = AsyncMock(return_value=None)
        with patch("app.core.ownership.visible_agent_ids", AsyncMock(return_value={"a1"})):
            await api.create_rule(
                api.CreateRule(name="r", description="d", category=kategorie, agent_id="a1"),
                user=user, db=db,
            )
        return db

    async def test_mitglied_kann_nichts_ueber_der_grenze_freischalten(self):
        for kategorie in ("custom", "purchase"):
            with self.assertRaises(HTTPException) as f:
                await self._regel(MITGLIED, kategorie)
            self.assertEqual(f.exception.status_code, 403, kategorie)

    async def test_mitglied_innerhalb_der_grenze(self):
        db = await self._regel(MITGLIED, "shell_exec")
        db.commit.assert_awaited()

    async def test_admin_darf_alles(self):
        await self._regel(ADMIN, "custom")

    async def test_aendern_auf_gesperrte_kategorie(self):
        from app.api import approval_rules as api

        regel = SimpleNamespace(id=1, name="r", description="d", category="shell_exec", threshold=None,
                                is_active=True, agent_id="a1", created_by=MITGLIED.id, created_at=None)
        db = MagicMock(add=MagicMock(), commit=AsyncMock(), refresh=AsyncMock())
        db.scalar = AsyncMock(return_value=regel)
        db.get = AsyncMock(return_value=None)
        with self.assertRaises(HTTPException) as f:
            await api.update_rule(1, api.UpdateRule(category="custom"), user=MITGLIED, db=db)
        self.assertEqual(f.exception.status_code, 403)
        self.assertEqual(regel.category, "shell_exec")


class UeberDerGrenze(unittest.TestCase):
    def test_bestand_wird_gemeldet_nicht_geaendert(self):
        self.assertEqual(ag.ueberschreitung("l4", {}, "l3"), ["Stufe L4"])
        self.assertEqual(ag.ueberschreitung("l3", {}, "l3"), [])
        self.assertEqual(ag.ueberschreitung("l4", {}, None), [])
        gruende = ag.ueberschreitung("l3", {"permissions": ["full-access"], "permissions_mode": "manual"}, "l3")
        self.assertEqual(gruende, ["Sudo: full-access"])
        custom = ag.ueberschreitung("custom", {"autonomy_matrix": dict(am.matrix_for_level("l3"), purchases="allow")}, "l3")
        self.assertEqual(custom, ["Matrix freizügiger: purchases"])

    def test_computer_use_ueber_der_grenze_wird_gemeldet(self):
        policy = {"computer_use_default_capabilities": ["mouse", "shell"]}
        self.assertEqual(ag.ueberschreitung("l2", policy, "l3"), ["Computer-Use: shell"])
        self.assertEqual(ag.ueberschreitung("l3", policy, "l3"), [])
        self.assertEqual(ag.ueberschreitung("l2", policy, None), [])


class RollenPflege(unittest.IsolatedAsyncioTestCase):
    def _db(self, rolle=None):
        db = MagicMock(add=MagicMock(), commit=AsyncMock(), refresh=AsyncMock())
        db.get = AsyncMock(return_value=rolle)
        db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
        return db

    async def test_ungueltige_grenze_wird_abgelehnt(self):
        from app.api import roles as api

        with self.assertRaises(HTTPException) as f:
            await api.create_role({"name": "X", "permissions": {"max_autonomy_level": "l9"}},
                                  user=ADMIN, db=self._db())
        self.assertEqual(f.exception.status_code, 422)

    async def test_aenderung_der_grenze_steht_im_pruefprotokoll(self):
        from app.api import roles as api

        from app.models.custom_role import CustomRole

        rolle = CustomRole(id=3, name="Team", description=None, is_system=False,
                           permissions={"max_agents": 2})
        with patch("app.core.audit.protokolliere", AsyncMock()) as prot:
            await api.update_role(3, {"permissions": {"max_agents": 2, "max_autonomy_level": "l2"}},
                                  user=ADMIN, db=self._db(rolle), manager=MagicMock())
        befehle = [c.kwargs.get("command") or "" for c in prot.await_args_list]
        eintrag = [b for b in befehle if "Autonomie-Grenze" in b]
        self.assertEqual(len(eintrag), 1, befehle)
        self.assertIn("L2", eintrag[0])

    async def test_eigene_grenze_wird_normalisiert_ausgeliefert(self):
        from app.api import roles as api

        rolle = SimpleNamespace(permissions={"max_autonomy_level": "kaputt"})
        nutzer = SimpleNamespace(id="s", role=UserRole.MEMBER, custom_role_id=5)
        antwort = await api.my_permissions(user=nutzer, db=self._db(rolle))
        self.assertEqual(antwort["permissions"]["max_autonomy_level"], "l1")


class VerwaltungsListe(unittest.IsolatedAsyncioTestCase):
    async def test_nur_agenten_ueber_der_grenze_ihres_besitzers(self):
        zeilen = [
            ("a1", "Mitglied-L4", MITGLIED.id, "l4", {}),
            ("a2", "Mitglied-L3", MITGLIED.id, "l3", {}),
            ("a3", "Admin-L4", ADMIN.id, "l4", {"permissions": ["full-access"], "permissions_mode": "manual"}),
            ("a4", "Mitglied-Root", MITGLIED.id, "l3", {"permissions": ["full-access"], "permissions_mode": "manual"}),
        ]
        db = _db()
        db.execute = AsyncMock(return_value=MagicMock(all=MagicMock(return_value=zeilen)))
        liste = await ag.agenten_ueber_der_grenze(db)
        self.assertEqual([e["agent_id"] for e in liste], ["a1", "a4"])
        self.assertEqual(liste[0]["grenze"], "l3")
        self.assertEqual(liste[1]["gruende"], ["Sudo: full-access"])

    async def test_nur_fuer_administratoren(self):
        from app.api import admin as api

        with self.assertRaises(HTTPException) as f:
            await api._require_admin(user=MITGLIED)
        self.assertEqual(f.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
