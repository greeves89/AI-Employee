"""Die Autonomiestufe bestimmt auch den sudo-Zugriff im Container.

Bis hierher waren es zwei Systeme: die Matrix sagte dem Agenten, was er darf, und
``config["permissions"]`` sagte dem Container, was er technisch kann. Ein L1-Agent
("nur lesen") bekam trotzdem das Standardpaket ``package-install`` — der Prompt sagte
nein, die Kiste sagte ja.

Die Tests pruefen beides: die Ableitung selbst UND dass keine der vier Stellen, die
frueher ``config.get("permissions")`` selbst gelesen haben, daran vorbeigeht.
"""

import re
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.core import autonomy_matrix as am

REPO = Path(__file__).resolve().parents[2]
ORCH = REPO / "orchestrator"

# Das Docker-SDK liegt nur im Container-Image, nicht im lokalen Lauf — stubben,
# damit sich die API-Schicht importieren laesst (Muster aus test_voice_save_memory.py).
_docker_stub = types.ModuleType("docker")
_docker_stub.from_env = lambda: None
_docker_errors_stub = types.ModuleType("docker.errors")
_docker_errors_stub.NotFound = type("NotFound", (Exception,), {})
_docker_errors_stub.APIError = type("APIError", (Exception,), {})
_docker_stub.errors = _docker_errors_stub
_docker_models_stub = types.ModuleType("docker.models")
_docker_containers_stub = types.ModuleType("docker.models.containers")
_docker_containers_stub.Container = type("Container", (), {})
_docker_models_stub.containers = _docker_containers_stub
_docker_stub.models = _docker_models_stub
sys.modules.setdefault("docker", _docker_stub)
sys.modules.setdefault("docker.errors", _docker_errors_stub)
sys.modules.setdefault("docker.models", _docker_models_stub)
sys.modules.setdefault("docker.models.containers", _docker_containers_stub)

from app.services import realtime_voice_session as rvs  # noqa: E402


class _FakeManager:
    def __init__(self, db, docker, redis):
        self.docker = docker


class _FakeDB:
    async def __aenter__(self):
        db = types.SimpleNamespace()

        async def get(modell, kennung):
            return types.SimpleNamespace(id=kennung, role="admin")

        db.get = get
        return db

    async def __aexit__(self, *a):
        return False


def _voice(agent_id="a1", user_id="u1"):
    s = rvs.RealtimeVoiceSession.__new__(rvs.RealtimeVoiceSession)
    s.agent_id = agent_id
    s.user_id = user_id
    s.redis = None
    return s


class DeriveTests(unittest.TestCase):
    def test_read_only_levels_get_no_sudo(self):
        for level in ("l1", "l2"):
            with self.subTest(level=level):
                self.assertEqual(am.derive_permissions(am.matrix_for_level(level)), [])

    def test_executing_levels_get_package_and_config(self):
        for level in ("l3", "l4"):
            with self.subTest(level=level):
                got = am.derive_permissions(am.matrix_for_level(level))
                self.assertEqual(sorted(got), [am.PKG_PACKAGE_INSTALL, am.PKG_SYSTEM_CONFIG])

    def test_full_access_is_never_derived(self):
        """Uneingeschraenktes root bleibt eine bewusste Handentscheidung — auch bei L4."""
        for level in ("l1", "l2", "l3", "l4"):
            self.assertNotIn(am.PKG_FULL_ACCESS, am.derive_permissions(am.matrix_for_level(level)))

    def test_ask_does_not_grant_sudo(self):
        """`ask` heisst: erst `request_approval`. Ein stehendes sudo-Recht waere
        genau der Weg um diese Sperre herum."""
        matrix = dict(am.matrix_for_level("l3"))
        matrix["system_config"] = am.ASK
        self.assertEqual(am.derive_permissions(matrix), [])

    def test_deny_does_not_grant_sudo(self):
        matrix = dict(am.matrix_for_level("l4"))
        matrix["system_config"] = am.DENY
        self.assertEqual(am.derive_permissions(matrix), [])

    def test_unknown_matrix_fails_closed(self):
        self.assertEqual(am.derive_permissions({}), [])
        self.assertEqual(am.derive_permissions({"system_config": "vielleicht"}), [])


class EffectivePermissionTests(unittest.TestCase):
    def test_auto_follows_the_level(self):
        self.assertEqual(am.effective_permissions({}, "l1"), [])
        self.assertEqual(len(am.effective_permissions({}, "l3")), 2)

    def test_auto_ignores_a_stale_stored_list(self):
        """Der gespeicherte Wert ist genau der, der frueher auseinanderlief."""
        cfg = {"permissions": [am.PKG_PACKAGE_INSTALL]}
        self.assertEqual(am.effective_permissions(cfg, "l1"), [])

    def test_manual_mode_wins(self):
        cfg = {"permissions_mode": "manual", "permissions": [am.PKG_PACKAGE_INSTALL]}
        self.assertEqual(am.effective_permissions(cfg, "l1"), [am.PKG_PACKAGE_INSTALL])

    def test_manual_mode_can_hold_nothing(self):
        cfg = {"permissions_mode": "manual", "permissions": []}
        self.assertEqual(am.effective_permissions(cfg, "l4"), [])

    def test_existing_full_access_survives(self):
        """Wer frueher vollen root-Zugriff vergeben hat, verliert ihn nicht still
        beim naechsten Recreate."""
        cfg = {"permissions": [am.PKG_FULL_ACCESS]}
        self.assertEqual(am.effective_permissions(cfg, "l1"), [am.PKG_FULL_ACCESS])

    def test_custom_matrix_beats_the_level_label(self):
        """Stufe 'custom' + eigene Matrix: die Matrix entscheidet, nicht das Etikett."""
        matrix = dict(am.matrix_for_level("l1"))
        matrix["system_config"] = am.ALLOW
        cfg = {"autonomy_matrix": matrix}
        self.assertEqual(sorted(am.effective_permissions(cfg, "custom")),
                         [am.PKG_PACKAGE_INSTALL, am.PKG_SYSTEM_CONFIG])


class NoBypassTests(unittest.TestCase):
    """Nicht das Symptom pruefen, sondern das Vorbeigehen verbieten.

    Vier Stellen lasen die Rechte frueher selbst aus der Config. Kommt eine fuenfte
    dazu, faellt sie hier auf, bevor sie live geht.
    """

    SOURCES = (
        "app/core/agent_manager.py",
        "app/api/agents.py",
        "app/services/agent_settings.py",
    )
    PATTERN = re.compile(r"""config\.get\(\s*["']permissions["']""")

    def test_nobody_reads_the_permission_list_directly(self):
        for rel in self.SOURCES:
            src = (ORCH / rel).read_text()
            hits = self.PATTERN.findall(src)
            self.assertEqual(
                hits, [],
                f"{rel} liest config['permissions'] selbst — "
                "autonomy_matrix.effective_permissions() nutzen, sonst laufen "
                "Matrix und sudoers wieder auseinander.",
            )

    def test_every_container_path_uses_the_helper(self):
        """Erstellung und beide Neuerstellungs-Wege muessen die Ableitung aufrufen."""
        src = (ORCH / "app/core/agent_manager.py").read_text()
        self.assertGreaterEqual(
            src.count("autonomy_matrix.effective_permissions("), 3,
            "Ein Container-Weg holt seine Rechte nicht ueber die Ableitung.",
        )

    def test_level_change_syncs_the_running_container(self):
        settings_src = (ORCH / "app/services/agent_settings.py").read_text()
        self.assertIn("_sync_container_sudo", settings_src)
        agents_src = (ORCH / "app/api/agents.py").read_text()
        self.assertIn("_apply_permissions", agents_src,
                      "Stufenwechsel schreibt die sudoers-Datei nicht in den laufenden Container.")

    def test_matrix_change_syncs_too(self):
        """Beide Wege in die Matrix — Stufen-Preset UND Feinjustierung."""
        src = (ORCH / "app/api/agents.py").read_text()
        endpoint = src.split("async def update_autonomy_matrix")[1].split("\nasync def ")[0]
        self.assertIn("_sync_container_sudo", endpoint)


class VoicePathHandsOverTheManagerTests(unittest.IsolatedAsyncioTestCase):
    """Harness-Paritaet: die Sprachfront kann die Stufe auch setzen — mit Manager.

    Ohne den Manager traegt niemand die neue sudo-Gewaehrung in den LAUFENDEN
    Container: der Agent glaubt, er stehe auf L4, die Kiste bleibt auf L1. Genau die
    Luecke, die diese Datei ueberall sonst zumauert — nur ueber die Sprache hinein.
    """

    async def _set(self, level, docker=object()):
        with patch("app.db.session.async_session_factory", lambda: _FakeDB()), \
             patch("app.api.ws._docker", docker), \
             patch("app.core.agent_manager.AgentManager", _FakeManager), \
             patch("app.services.agent_settings.change_autonomy_level",
                   new=AsyncMock(return_value={"autonomy_level": level})) as ruf:
            antwort = await _voice()._set_autonomy(level)
        return ruf, antwort

    def _manager(self, ruf):
        """Das 5. Argument — mit lesbarer Meldung statt IndexError, wenn es fehlt."""
        args = ruf.await_args.args
        self.assertEqual(
            len(args), 5,
            "Sprachfront ruft change_autonomy_level ohne Manager-Argument auf — "
            f"uebergeben wurden {len(args)} Argumente. Die neue sudo-Gewaehrung "
            "erreicht den laufenden Container damit nie.")
        return args[4]

    async def test_the_manager_is_handed_over(self):
        ruf, antwort = await self._set("l4")
        self.assertIsInstance(
            self._manager(ruf), _FakeManager,
            "Sprachfront setzt die Stufe ohne Manager — sudo bliebe alt.")
        self.assertEqual(ruf.await_args.args[2:4], ("a1", "l4"))
        self.assertIn("L4", antwort)

    async def test_without_docker_it_still_switches(self):
        """Kein Docker-Griff heisst: beim naechsten Start — nicht: gar nicht."""
        ruf, antwort = await self._set("l3", docker=None)
        self.assertIsNone(self._manager(ruf))
        self.assertIn("L3", antwort)

    async def test_an_invalid_level_never_reaches_the_switch(self):
        # Die Sitzungsfabrik wird MITgestubbt: faellt die Pruefung weg, soll der Test
        # sauber "wurde doch aufgerufen" melden statt in einen Netzfehler zu laufen.
        ruf, antwort = await self._set("root")
        ruf.assert_not_awaited()
        self.assertIn("gültige Autonomiestufe", antwort)


class UiTests(unittest.TestCase):
    """Die Oberflaeche darf die Kopplung nicht aushebeln.

    Das Erstell-Modal schickte frueher IMMER eine Paketliste mit — damit waere jeder
    neue Agent auf "manuell" gelandet und die Ableitung nie zum Zug gekommen.
    """

    MODAL = "frontend/src/components/agents/create-agent-modal.tsx"
    DETAIL = "frontend/src/app/agents/[id]/page.tsx"
    # Issue #787 Punkt 1: die JSX-Kopie (Icons, Kopplungs-Knopf, Karten) ist
    # keine Kopie mehr, sondern eine gemeinsame Komponente -- der wortgleiche
    # Text "Wieder an Stufe koppeln" steht seitdem nur noch EINMAL im Baum.
    SHARED_PANEL = "frontend/src/components/agents/permission-packages-panel.tsx"

    def test_modal_sends_nothing_in_auto_mode(self):
        src = (REPO / self.MODAL).read_text()
        self.assertIn('permissionsMode === "manual" ? selectedPermissions : undefined', src)
        self.assertNotIn("selectedPermissions.length > 0 ? selectedPermissions", src,
                         "Modal schickt wieder eine Liste an der Ableitung vorbei.")

    def test_modal_defaults_to_auto(self):
        src = (REPO / self.MODAL).read_text()
        self.assertIn('useState<"auto" | "manual">("auto")', src)

    def test_both_surfaces_use_the_shared_panel_instead_of_their_own_copy(self):
        for rel in (self.MODAL, self.DETAIL):
            with self.subTest(surface=rel):
                src = (REPO / rel).read_text()
                self.assertIn("PermissionPackagesPanel", src,
                              f"{rel} rendert die Sudo-Pakete nicht mehr ueber die "
                              "gemeinsame Komponente -- droht wieder auseinanderzulaufen.")

    def test_the_shared_panel_still_has_the_coupling_toggle(self):
        src = (REPO / self.SHARED_PANEL).read_text()
        self.assertIn("Wieder an Stufe koppeln", src)
        self.assertIn("Selbst festlegen", src)

    def test_rule_is_not_reimplemented_in_typescript(self):
        """Welche Stufe welches Paket ergibt, steht NUR in Python."""
        for rel in (self.MODAL, self.DETAIL, self.SHARED_PANEL):
            src = (REPO / rel).read_text()
            self.assertNotIn('"l3" ? ["package-install"', src)
        for rel in (self.MODAL, self.DETAIL):
            self.assertIn("derivedPermissions", (REPO / rel).read_text())

    def test_endpoint_serves_the_derivation(self):
        src = (ORCH / "app/api/agents.py").read_text()
        self.assertIn('"derived": derived', src)


if __name__ == "__main__":
    unittest.main()
