"""Guard test: Telegram voice transcription must try the local STT service first
and only fall back to the OpenAI Whisper API, mirroring ``_text_to_speech`` and
``agent_bot.py``.

Background (issue #367): ``_transcribe_voice`` used to call the OpenAI Whisper API
exclusively, and ``handle_voice`` hard-gated on ``OPENAI_API_KEY`` — so voice
messages were unusable on installs without a paid key even though a healthy local
faster-whisper service (``stt-service:8003``) was part of the stack and simply
never called.

Source-level AST/string guard so it runs without httpx/telegram/sqlalchemy or a
live service in the container.
"""

import ast
import unittest
from pathlib import Path

_VOICE = (
    Path(__file__).resolve().parent.parent
    / "app" / "telegram" / "handlers" / "voice.py"
)


def _module() -> ast.Module:
    return ast.parse(_VOICE.read_text())


def _find_func(mod: ast.Module, name: str) -> ast.AsyncFunctionDef:
    for node in ast.walk(mod):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in voice.py")


class TestVoiceLocalSttFirst(unittest.TestCase):
    def setUp(self) -> None:
        self.src = _VOICE.read_text()
        self.mod = _module()

    def test_transcribe_uses_local_stt_service(self) -> None:
        """_transcribe_voice must reference the local STT service URL + endpoint."""
        fn = _find_func(self.mod, "_transcribe_voice")
        src = ast.get_source_segment(self.src, fn)
        self.assertIn("stt_service_url", src)
        self.assertIn("/transcribe", src)

    def test_transcribe_still_has_openai_fallback(self) -> None:
        """The OpenAI Whisper endpoint must remain as a fallback path."""
        fn = _find_func(self.mod, "_transcribe_voice")
        src = ast.get_source_segment(self.src, fn)
        self.assertIn("api.openai.com/v1/audio/transcriptions", src)
        # Local call must appear before the OpenAI fallback.
        self.assertLess(
            src.index("/transcribe"),
            src.index("api.openai.com/v1/audio/transcriptions"),
            "local STT call must precede the OpenAI fallback",
        )

    def test_transcribe_local_call_is_guarded(self) -> None:
        """The local STT call must sit inside a try/except so the fallback can run."""
        fn = _find_func(self.mod, "_transcribe_voice")
        guarded = False
        for node in ast.walk(fn):
            if isinstance(node, ast.Try):
                seg = ast.get_source_segment(self.src, node) or ""
                if "/transcribe" in seg and node.handlers:
                    guarded = True
        self.assertTrue(guarded, "local STT call is not wrapped in try/except")

    def test_handle_voice_has_no_hard_openai_gate(self) -> None:
        """handle_voice must no longer refuse transcription when OPENAI_API_KEY is unset."""
        fn = _find_func(self.mod, "handle_voice")
        src = ast.get_source_segment(self.src, fn)
        self.assertNotIn("OPENAI_API_KEY fehlt", src)
        self.assertNotIn("_get_openai_key", src)


if __name__ == "__main__":
    unittest.main()
