"""Tests for the Teams-transcript tools (transcript-to-tasks feature).

Covers:
  - catalog: both tools exist and are read-only (not write-gated)
  - ms_list_meeting_transcripts resolves calendarView -> onlineMeeting -> transcripts
  - transcript-access errors (403 organizer-only tenants) are skipped, not fatal
  - ms_get_meeting_transcript requests VTT and condenses it to dialog lines
  - _vtt_to_dialog merges consecutive cues per speaker and survives tag-less VTT
"""

import asyncio
import unittest

from app.core import msgraph_mcp
from app.core.msgraph_mcp import (
    MSGRAPH_TOOLS,
    WRITE_TOOLS,
    GraphError,
    _vtt_to_dialog,
    handle_tool,
)


def _run(coro):
    return asyncio.run(coro)


_VTT = (
    "WEBVTT\n\n"
    "00:00:03.180 --> 00:00:07.140\n"
    "<v Sprecher A>Erster Satz.</v>\n\n"
    "00:00:08.340 --> 00:00:26.260\n"
    "<v Sprecher A>Zweiter Satz,\ngleicher Sprecher.</v>\n\n"
    "00:00:27.540 --> 00:00:45.860\n"
    "<v Sprecher B>Antwort.</v>\n"
)


class _GraphFake:
    """Async _graph replacement: records calls, serves canned Graph payloads."""

    def __init__(self, transcripts_error: GraphError | None = None):
        self.calls = []
        self.transcripts_error = transcripts_error

    async def __call__(self, method, path, token, **kwargs):
        self.calls.append({"method": method, "path": path, "kwargs": kwargs})
        if path == "/me/calendarView":
            return {"value": [
                {"id": "EV1", "subject": "Team Daily", "isOnlineMeeting": True,
                 "onlineMeetingUrl": "https://teams.microsoft.com/l/meetup-join/abc",
                 "start": {"dateTime": "2026-08-13T07:30:00.0000000"}},
                {"id": "EV2", "subject": "Ohne Teams", "isOnlineMeeting": False,
                 "start": {"dateTime": "2026-08-13T09:00:00.0000000"}},
            ]}
        if path == "/me/onlineMeetings":
            return {"value": [{"id": "OM1"}]}
        if path.endswith("/transcripts"):
            if self.transcripts_error is not None:
                raise self.transcripts_error
            return {"value": [{"id": "TR1"}, {"id": "TR2"}]}
        return {}


class CatalogTests(unittest.TestCase):
    def test_tools_registered_and_readonly(self):
        names = {t["name"] for t in MSGRAPH_TOOLS}
        self.assertIn("ms_list_meeting_transcripts", names)
        self.assertIn("ms_get_meeting_transcript", names)
        # Read-only tools: must stay visible for read-only agents.
        self.assertNotIn("ms_list_meeting_transcripts", WRITE_TOOLS)
        self.assertNotIn("ms_get_meeting_transcript", WRITE_TOOLS)


class ListTranscriptsTests(unittest.TestCase):
    def _swap(self, fake):
        self._orig = msgraph_mcp._graph
        msgraph_mcp._graph = fake

    def tearDown(self):
        msgraph_mcp._graph = self._orig

    def test_resolves_meeting_and_lists_fragments(self):
        fake = _GraphFake()
        self._swap(fake)
        out = _run(handle_tool("ms_list_meeting_transcripts", {"days_back": 2}, "tok"))
        self.assertIn("OM1", out)
        self.assertIn("TR1", out)
        self.assertIn("TR2", out)  # stop/restart -> several fragments, all listed
        self.assertIn("Team Daily", out)
        paths = [c["path"] for c in fake.calls]
        self.assertEqual(paths[0], "/me/calendarView")
        self.assertIn("/me/onlineMeetings", paths)
        self.assertIn("/me/onlineMeetings/OM1/transcripts", paths)
        # The non-Teams event must not trigger an onlineMeetings lookup.
        self.assertEqual(paths.count("/me/onlineMeetings"), 1)
        # joinWebUrl-Filter goes through OData $filter.
        flt = next(c for c in fake.calls if c["path"] == "/me/onlineMeetings")
        self.assertIn("JoinWebUrl eq", flt["kwargs"]["params"]["$filter"])

    def test_transcript_403_is_skipped_not_fatal(self):
        fake = _GraphFake(transcripts_error=GraphError(403, "forbidden"))
        self._swap(fake)
        out = _run(handle_tool("ms_list_meeting_transcripts", {}, "tok"))
        self.assertIn("No transcripts found", out)


class GetTranscriptTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self._orig = msgraph_mcp._graph_bytes
        vtt = _VTT
        calls = self.calls

        class _Resp:
            text = vtt

        async def fake_bytes(method, path, token, **kwargs):
            calls.append({"method": method, "path": path})
            return _Resp()

        msgraph_mcp._graph_bytes = fake_bytes

    def tearDown(self):
        msgraph_mcp._graph_bytes = self._orig

    def test_condenses_vtt_to_dialog(self):
        out = _run(handle_tool(
            "ms_get_meeting_transcript",
            {"online_meeting_id": "OM1", "transcript_id": "TR1"}, "tok",
        ))
        self.assertIn("Sprecher A: Erster Satz. Zweiter Satz, gleicher Sprecher.", out)
        self.assertIn("Sprecher B: Antwort.", out)
        self.assertNotIn("-->", out)
        self.assertIn("$format=text/vtt", self.calls[0]["path"])
        self.assertIn("/me/onlineMeetings/OM1/transcripts/TR1/content", self.calls[0]["path"])

    def test_raw_returns_vtt(self):
        out = _run(handle_tool(
            "ms_get_meeting_transcript",
            {"online_meeting_id": "OM1", "transcript_id": "TR1", "raw": True}, "tok",
        ))
        self.assertIn("WEBVTT", out)
        self.assertIn("-->", out)

    def test_truncation(self):
        out = _run(handle_tool(
            "ms_get_meeting_transcript",
            {"online_meeting_id": "OM1", "transcript_id": "TR1", "max_chars": 1000}, "tok",
        ))
        self.assertLessEqual(len(out), 1100)


class VttCondenserTests(unittest.TestCase):
    def test_merges_consecutive_speaker_cues(self):
        out = _vtt_to_dialog(_VTT)
        self.assertEqual(
            out,
            "Sprecher A: Erster Satz. Zweiter Satz, gleicher Sprecher.\n"
            "Sprecher B: Antwort.",
        )

    def test_tagless_vtt_falls_back_to_plain_lines(self):
        vtt = "WEBVTT\n\n1\n00:00:01.000 --> 00:00:02.000\nHallo Welt\n"
        self.assertEqual(_vtt_to_dialog(vtt), "Hallo Welt")


if __name__ == "__main__":
    unittest.main()
