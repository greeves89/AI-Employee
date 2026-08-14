"""Voice-on-Desktop Phase 1 (#478): bridge-side microphone capture.

The bridge already has an InputRecorder for replay-mode ("observe what the human
just did"). VoiceCapture is the same lifecycle discipline applied to the
microphone: only listens between an explicit start/stop, never buffers to disk,
chunks go straight out. This is phase 1 only — there is deliberately no server
side yet (RealtimeVoiceSession wiring is a later phase, see issue #478), so
these tests stay entirely inside the bridge process.

The bridge has no CI suite of its own, so this runs in the orchestrator pytest
job, same pattern as test_bridge_extra_headers.py: a dynamic import of bridge.py
with the optional native dependencies stubbed, plus source-level guards for the
wiring that can't be exercised without real audio hardware.
"""

import importlib.util
import sys
import types
import unittest
from pathlib import Path

_BRIDGE_SRC_PATH = Path(__file__).resolve().parents[2] / "computer-use-bridge" / "bridge.py"


def _load_bridge_module():
    """Import bridge.py, stubbing optional native deps (websockets, pyautogui)."""
    for name in ("websockets", "pyautogui"):
        if name not in sys.modules:
            try:
                __import__(name)
            except ImportError:
                sys.modules[name] = types.ModuleType(name)
    spec = importlib.util.spec_from_file_location("bridge_under_test_voice", _BRIDGE_SRC_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class VoiceCaptureSourceGuard(unittest.TestCase):
    """Wiring that isn't exercisable without a real microphone / websocket."""

    def setUp(self):
        self.src = _BRIDGE_SRC_PATH.read_text()

    def test_capabilities_announce_voice_capture(self):
        self.assertIn('"start_voice_capture", "stop_voice_capture"', self.src)

    def test_dispatcher_wires_voice_capture_actions(self):
        self.assertIn('action == "start_voice_capture"', self.src)
        self.assertIn('action == "stop_voice_capture"', self.src)

    def test_voice_capture_is_stopped_on_disconnect(self):
        # Same discipline as input_recorder: never leave the mic open past the
        # connection that started it.
        self.assertIn("self.dispatcher.voice_capture.stop()", self.src)

    def test_voice_chunks_use_own_queue_not_input_queue(self):
        # Audio and click/keystroke events must not share a queue — different
        # producers, different consumers, different message types on the wire.
        self.assertIn("self._voice_events: queue.Queue", self.src)
        self.assertIn('"type": "voice_chunk"', self.src)

    def test_import_is_lazy_like_pynput(self):
        # sounddevice must not be imported at module load time — the bridge
        # has to start fine on a machine without it, same as pynput.
        self.assertIn("import sounddevice as sd", self.src)
        self.assertNotIn("import sounddevice\n", self.src.split("class VoiceCapture")[0])


class VoiceCaptureBehaviourTests(unittest.TestCase):
    def setUp(self):
        self.m = _load_bridge_module()

    def test_missing_sounddevice_reports_actionable_error(self):
        # This container has no sounddevice installed — exercises the real
        # ImportError branch, not a mock.
        vc = self.m.VoiceCapture(emit=lambda e: None)
        result = vc.start()
        self.assertFalse(result["ok"])
        self.assertIn("sounddevice", result["error"])
        self.assertIn("pip install sounddevice", result["error"])
        self.assertFalse(vc.active)

    def test_stop_before_start_is_a_no_op(self):
        vc = self.m.VoiceCapture(emit=lambda e: None)
        result = vc.stop()
        self.assertTrue(result["ok"])
        self.assertTrue(result["already_stopped"])

    def test_on_audio_encodes_pcm16_and_emits(self):
        import base64
        import numpy as np

        events = []
        vc = self.m.VoiceCapture(emit=events.append)
        indata = np.array([[0.5], [-0.5], [0.0]], dtype=np.float32)
        vc._on_audio(indata, 3, None, None)

        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["sample_rate"], self.m.VoiceCapture.SAMPLE_RATE)
        self.assertEqual(event["channels"], 1)
        self.assertIn("ts", event)

        decoded = np.frombuffer(base64.b64decode(event["chunk_b64"]), dtype=np.int16)
        expected = (indata[:, 0] * 32767.0).astype(np.int16)
        np.testing.assert_array_equal(decoded, expected)

    def test_dispatcher_routes_start_stop_to_voice_capture(self):
        dispatcher = self.m.CommandDispatcher.__new__(self.m.CommandDispatcher)
        dispatcher._ctrl = None
        dispatcher.input_recorder = None
        dispatcher.voice_capture = None

        result = dispatcher.dispatch({"action": "start_voice_capture"})
        self.assertEqual(result, {"ok": False, "error": "voice capture not wired up"})

        result = dispatcher.dispatch({"action": "stop_voice_capture"})
        self.assertEqual(result, {"ok": False, "error": "voice capture not wired up"})

        calls = []

        class _FakeVoiceCapture:
            def start(self):
                calls.append("start")
                return {"ok": True}

            def stop(self):
                calls.append("stop")
                return {"ok": True}

        dispatcher.voice_capture = _FakeVoiceCapture()
        self.assertEqual(dispatcher.dispatch({"action": "start_voice_capture"}), {"ok": True})
        self.assertEqual(dispatcher.dispatch({"action": "stop_voice_capture"}), {"ok": True})
        self.assertEqual(calls, ["start", "stop"])

    def test_bridge_wires_voice_capture_into_new_dispatcher(self):
        bridge = self.m.Bridge("wss://x", "TOK", "sess")
        dispatcher = bridge._ensure_dispatcher()
        self.assertIsInstance(dispatcher.voice_capture, self.m.VoiceCapture)
        self.assertEqual(dispatcher.voice_capture._emit, bridge._queue_voice_event)


if __name__ == "__main__":
    unittest.main()
