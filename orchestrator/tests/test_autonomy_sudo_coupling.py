"""Die Autonomiestufe bestimmt auch den sudo-Zugriff im Container.

Bis hierher waren es zwei Systeme: die Matrix sagte dem Agenten, was er darf, und
``config["permissions"]`` sagte dem Container, was er technisch kann. Ein L1-Agent
("nur lesen") bekam trotzdem das Standardpaket ``package-install`` — der Prompt sagte
nein, die Kiste sagte ja.

Die Tests pruefen beides: die Ableitung selbst UND dass keine der vier Stellen, die
frueher ``config.get("permissions")`` selbst gelesen haben, daran vorbeigeht.
"""

import re
import unittest
from pathlib import Path

from app.core import autonomy_matrix as am

REPO = Path(__file__).resolve().parents[2]
ORCH = REPO / "orchestrator"


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

    def test_voice_path_has_it_too(self):
        """Harness-Paritaet: die Sprachfront kann die Stufe auch setzen."""
        src = (REPO / "orchestrator/app/services/realtime_voice_session.py").read_text()
        call = src.split("change_autonomy_level(db, user, self.agent_id, lvl")[1][:20]
        self.assertIn("manager", call,
                      "Sprachfront setzt die Stufe ohne Manager — sudo bliebe alt.")


class UiTests(unittest.TestCase):
    """Die Oberflaeche darf die Kopplung nicht aushebeln.

    Das Erstell-Modal schickte frueher IMMER eine Paketliste mit — damit waere jeder
    neue Agent auf "manuell" gelandet und die Ableitung nie zum Zug gekommen.
    """

    MODAL = "frontend/src/components/agents/create-agent-modal.tsx"
    DETAIL = "frontend/src/app/agents/[id]/page.tsx"

    def test_modal_sends_nothing_in_auto_mode(self):
        src = (REPO / self.MODAL).read_text()
        self.assertIn('permissionsMode === "manual" ? selectedPermissions : undefined', src)
        self.assertNotIn("selectedPermissions.length > 0 ? selectedPermissions", src,
                         "Modal schickt wieder eine Liste an der Ableitung vorbei.")

    def test_modal_defaults_to_auto(self):
        src = (REPO / self.MODAL).read_text()
        self.assertIn('useState<"auto" | "manual">("auto")', src)

    def test_both_surfaces_can_switch_back(self):
        for rel in (self.MODAL, self.DETAIL):
            with self.subTest(surface=rel):
                self.assertIn("Wieder an Stufe koppeln", (REPO / rel).read_text())

    def test_rule_is_not_reimplemented_in_typescript(self):
        """Welche Stufe welches Paket ergibt, steht NUR in Python."""
        for rel in (self.MODAL, self.DETAIL):
            src = (REPO / rel).read_text()
            self.assertIn("derivedPermissions", src)
            self.assertNotIn('"l3" ? ["package-install"', src)

    def test_endpoint_serves_the_derivation(self):
        src = (ORCH / "app/api/agents.py").read_text()
        self.assertIn('"derived": derived', src)


if __name__ == "__main__":
    unittest.main()
