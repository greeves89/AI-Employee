"""Voice-on-Desktop Phase 3 (#478): tray mic-status indicator.

Phase 1 (PR #584) added VoiceCapture with no way for a GUI to know when it is
actually recording other than polling `dispatcher.voice_capture.active` from a
foreign thread. This adds an `on_active_change` callback — the same pattern
`Bridge.on_state` already uses for connection state — so the tray can show a
mic indicator the instant capture starts/stops, and wires it through to both
tray implementations (macOS/rumps and Windows-Linux/pystray).

Same testing constraints as test_bridge_voice_capture.py and
test_tray_app_edit_menu_ordering.py: neither `sounddevice` nor `rumps`/`pystray`
are installed in this container, so native calls are stubbed and anything that
can only run inside `run_macos()`/`run_tray()` (which import those lazily) is
covered by source guards instead of execution.
"""

import importlib.util
import sys
import types
import unittest
from pathlib import Path

_BRIDGE_SRC_PATH = Path(__file__).resolve().parents[2] / "computer-use-bridge" / "bridge.py"
_TRAY_SRC_PATH = Path(__file__).resolve().parents[2] / "computer-use-bridge" / "tray_app.py"


class _FakeStream:
    """Stand-in for sounddevice.InputStream — just tracks start/stop calls."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.started = False

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def close(self):
        pass


def _load_bridge_module(stub_sounddevice=True):
    """Import bridge.py. Returns (module, installed_fake_sounddevice: bool) —
    the caller must pop sys.modules["sounddevice"] in tearDown if the second
    value is True, or a fake mic implementation leaks into other test files'
    real-ImportError coverage (test_bridge_voice_capture.py) that run in the
    same pytest process.
    """
    for name in ("websockets", "pyautogui"):
        if name not in sys.modules:
            try:
                __import__(name)
            except ImportError:
                sys.modules[name] = types.ModuleType(name)
    installed_fake_sounddevice = False
    if stub_sounddevice and "sounddevice" not in sys.modules:
        fake_sd = types.ModuleType("sounddevice")
        fake_sd.InputStream = _FakeStream
        sys.modules["sounddevice"] = fake_sd
        installed_fake_sounddevice = True
    spec = importlib.util.spec_from_file_location("bridge_under_test_mic_status", _BRIDGE_SRC_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, installed_fake_sounddevice


def _load_tray_module():
    spec = importlib.util.spec_from_file_location("tray_app_under_test_mic_status", _TRAY_SRC_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class VoiceCaptureCallbackTests(unittest.TestCase):
    def setUp(self):
        self.m, self._installed_fake_sounddevice = _load_bridge_module()

    def tearDown(self):
        if self._installed_fake_sounddevice:
            del sys.modules["sounddevice"]

    def test_start_notifies_true_when_mic_opens(self):
        seen = []
        vc = self.m.VoiceCapture(emit=lambda e: None, on_active_change=seen.append)
        result = vc.start()
        self.assertTrue(result["ok"])
        self.assertTrue(vc.active)
        self.assertEqual(seen, [True])

    def test_stop_notifies_false_after_start(self):
        seen = []
        vc = self.m.VoiceCapture(emit=lambda e: None, on_active_change=seen.append)
        vc.start()
        vc.stop()
        self.assertFalse(vc.active)
        self.assertEqual(seen, [True, False])

    def test_already_active_start_does_not_renotify(self):
        seen = []
        vc = self.m.VoiceCapture(emit=lambda e: None, on_active_change=seen.append)
        vc.start()
        result = vc.start()
        self.assertTrue(result.get("already_active"))
        self.assertEqual(seen, [True])

    def test_stop_before_start_does_not_notify(self):
        seen = []
        vc = self.m.VoiceCapture(emit=lambda e: None, on_active_change=seen.append)
        vc.stop()
        self.assertEqual(seen, [])

    def test_missing_callback_is_optional(self):
        # No on_active_change given — must not raise.
        vc = self.m.VoiceCapture(emit=lambda e: None)
        self.assertTrue(vc.start()["ok"])
        self.assertTrue(vc.stop()["ok"])

    def test_broken_callback_does_not_break_capture(self):
        def _boom(active):
            raise RuntimeError("GUI callback exploded")

        vc = self.m.VoiceCapture(emit=lambda e: None, on_active_change=_boom)
        result = vc.start()
        self.assertTrue(result["ok"])
        self.assertTrue(vc.active)

    def test_bridge_forwards_on_voice_state_to_voice_capture(self):
        seen = []
        bridge = self.m.Bridge("wss://x", "TOK", "sess", on_voice_state=seen.append)
        dispatcher = bridge._ensure_dispatcher()
        dispatcher.voice_capture.start()
        self.assertEqual(seen, [True])

    def test_run_accepts_and_forwards_on_voice_state(self):
        # Source guard: the module-level library entry point must accept and
        # pass through on_voice_state, same as it already does for on_state.
        src = _BRIDGE_SRC_PATH.read_text()
        run_start = src.index("\nasync def run(")
        run_body = src[run_start:]
        self.assertIn("on_voice_state=None", run_body[:600])
        self.assertIn("on_voice_state=on_voice_state", run_body)


class TrayMicStatusSourceGuard(unittest.TestCase):
    def setUp(self):
        self.src = _TRAY_SRC_PATH.read_text()

    def test_run_bridge_thread_wires_voice_state_callback(self):
        self.assertIn("on_voice_state=_on_voice_state", self.src)

    def test_make_icon_accepts_mic_active(self):
        self.assertIn("def make_icon(connected, mic_active=False):", self.src)

    def test_refresh_loop_passes_mic_state(self):
        refresh_start = self.src.index("def refresh(icon):")
        refresh_end = self.src.index("\n    icon = pystray.Icon(", refresh_start)
        self.assertIn("_mic_active", self.src[refresh_start:refresh_end])


class TrayMicStatusBehaviourTests(unittest.TestCase):
    def setUp(self):
        self.m = _load_tray_module()

    def tearDown(self):
        # _mic_active is a module-level global mutated by the callback under
        # test — reset it so tests don't leak state into each other.
        self.m._mic_active = False

    def test_on_voice_state_sets_module_global(self):
        self.m._on_voice_state(True)
        self.assertTrue(self.m._mic_active)
        self.m._on_voice_state(False)
        self.assertFalse(self.m._mic_active)

    def test_status_symbol_shows_mic_only_when_connected_and_active(self):
        self.assertEqual(self.m.status_symbol(connected=True, connecting=False, mic_active=False), "●")
        self.assertEqual(self.m.status_symbol(connected=True, connecting=False, mic_active=True), "● 🎙")
        self.assertEqual(self.m.status_symbol(connected=False, connecting=True, mic_active=True), "◐")
        self.assertEqual(self.m.status_symbol(connected=False, connecting=False, mic_active=True), "○")


if __name__ == "__main__":
    unittest.main()
