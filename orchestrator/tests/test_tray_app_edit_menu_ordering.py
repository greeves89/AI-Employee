"""Guard for issue #437: the Edit menu must exist before the first-run setup
dialog is shown, not only once run_macos() starts.

An LSUIElement app has no menu bar until _install_edit_menu() runs, so Cmd+V
does nothing in any dialog shown before it. On a fresh install, main() shows
show_setup_dialog() before run_macos() (which used to install the menu) ever
gets called -- the one dialog that most needs paste support never got it.

The bridge/tray app has no CI suite of its own, so this runs in the
orchestrator pytest job, same pattern as test_bridge_extra_headers.py.
"""

import importlib.util
import unittest
from pathlib import Path
from unittest import mock

_TRAY_SRC_PATH = (
    Path(__file__).resolve().parents[2] / "computer-use-bridge" / "tray_app.py"
)


def _load_tray_module():
    spec = importlib.util.spec_from_file_location("tray_app_under_test", _TRAY_SRC_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TrayAppEditMenuOrderingSourceGuard(unittest.TestCase):
    def setUp(self):
        self.src = _TRAY_SRC_PATH.read_text()

    def test_main_installs_edit_menu_before_setup_dialog(self):
        main_start = self.src.index("def main():")
        install_pos = self.src.index("_install_edit_menu()", main_start)
        dialog_pos = self.src.index("show_setup_dialog(cfg)", main_start)
        self.assertLess(
            install_pos,
            dialog_pos,
            "_install_edit_menu() must run before show_setup_dialog() in main()",
        )

    def test_run_macos_no_longer_installs_it_itself(self):
        macos_start = self.src.index("def run_macos(")
        macos_end = self.src.index("\ndef ", macos_start + 1)
        self.assertNotIn("_install_edit_menu()", self.src[macos_start:macos_end])


class TrayAppEditMenuOrderingBehaviour(unittest.TestCase):
    def test_main_calls_install_before_load_config_and_dialog(self):
        m = _load_tray_module()
        calls = []
        with mock.patch.object(m, "IS_MAC", True), \
             mock.patch.object(m, "_install_edit_menu", side_effect=lambda: calls.append("install")), \
             mock.patch.object(m, "load_config", side_effect=lambda: calls.append("load") or {}), \
             mock.patch.object(m, "show_setup_dialog", side_effect=lambda cfg: calls.append("dialog") or None), \
             mock.patch.object(m, "run_macos", side_effect=lambda cfg: calls.append("run_macos")), \
             mock.patch.object(m, "sys") as mock_sys:
            mock_sys.exit.side_effect = SystemExit
            with self.assertRaises(SystemExit):
                m.main()
        self.assertEqual(calls, ["install", "load", "dialog"])

    def test_main_skips_install_on_non_mac(self):
        m = _load_tray_module()
        calls = []
        with mock.patch.object(m, "IS_MAC", False), \
             mock.patch.object(m, "_install_edit_menu", side_effect=lambda: calls.append("install")), \
             mock.patch.object(m, "load_config", return_value={"url": "x", "token": "y", "session": "z"}), \
             mock.patch.object(m, "run_tray", side_effect=lambda cfg: calls.append("run_tray")):
            m.main()
        self.assertEqual(calls, ["run_tray"])
        self.assertNotIn("install", calls)


if __name__ == "__main__":
    unittest.main()
