"""Regression test for the Nova Sonic input-stream dead-guard.

When the AWS server completes the bidirectional HTTP/2 stream (after a transient
"Invalid event bytes"/"unexpected error" or a normal end), the receive loop
exits. Before this guard the session was never fenced off, so the caller kept
pushing mic PCM via ``send_audio`` → ``input_stream.send`` onto a completed
stream. awscrt then raised ``AWS_ERROR_HTTP_STREAM_HAS_COMPLETED`` from an
internal task whose exception was never retrieved (recurring asyncio ERROR in
the platform log).

The fix: once the receive loop ends (or ``close()`` runs), ``_input_dead`` is set
and every subsequent send short-circuits before touching the dead stream.
"""

import unittest

from app.services.voice_providers.realtime_nova_sonic import NovaSonicSession


class _FakeInputStream:
    def __init__(self) -> None:
        self.sends = 0
        self.closed = False

    async def send(self, _chunk) -> None:
        if self.closed:
            # Mirror awscrt raising once the stream has completed.
            raise RuntimeError("AWS_ERROR_HTTP_STREAM_HAS_COMPLETED")
        self.sends += 1

    async def close(self) -> None:
        self.closed = True


class _FakeStream:
    def __init__(self) -> None:
        self.input_stream = _FakeInputStream()


def _make_session() -> NovaSonicSession:
    async def _noop(_kind, _data):
        return None

    sess = NovaSonicSession(
        region="us-east-1",
        access_key="a",
        secret_key="b",
        system_prompt="hi",
        on_event=_noop,
    )
    sess._stream = _FakeStream()
    sess._audio_started = True
    return sess


class TestNovaSonicInputGuard(unittest.IsolatedAsyncioTestCase):
    async def test_send_audio_works_while_stream_alive(self):
        sess = _make_session()
        await sess.send_audio(b"\x00\x01")
        self.assertEqual(sess._stream.input_stream.sends, 1)

    async def test_no_send_after_input_dead(self):
        sess = _make_session()
        sess._input_dead = True  # receive loop ended
        await sess.send_audio(b"\x00\x01")
        await sess.inject_user_text("late report")
        await sess.send_tool_result("id-1", "answer")
        self.assertEqual(
            sess._stream.input_stream.sends,
            0,
            "no writes may reach a completed stream once input is dead",
        )

    async def test_send_event_short_circuits_without_stream(self):
        sess = _make_session()
        sess._stream = None
        # Must not raise AttributeError on the missing stream.
        await sess._send_event({"noop": {}})

    async def test_close_marks_input_dead_and_blocks_further_sends(self):
        sess = _make_session()
        await sess.close()
        self.assertTrue(sess._input_dead)
        before = sess._stream.input_stream.sends
        await sess.send_audio(b"\x00\x01")
        self.assertEqual(sess._stream.input_stream.sends, before)


if __name__ == "__main__":
    unittest.main()
