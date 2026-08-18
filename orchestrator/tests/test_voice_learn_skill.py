"""Per Voice zuschauen und daraus ein Skill bauen.

Wunsch des Nutzers 2026-08-18: „schau mal zu, was ich mache … wenn ich 'fertig'
sage, nimmst du alle Screenshots, analysierst die Klicks und baust daraus ein
Skill. DAS MUSS AUCH MITTELS VOICE Layer gehen."

Der Mitschnitt-Weg (menschliche Klicks/Tasten + `replay_skill_service`) existierte
serverseitig schon, war aber nur ueber die HTTP-Oberflaeche erreichbar — die
Stimme kannte ihn nicht. Dieses Werkzeug schliesst die Luecke und nutzt dieselben
Bausteine (kein zweiter Aufzeichnungsweg).
"""

import unittest
from unittest.mock import AsyncMock, patch

from app.api import computer_use as cu
from app.services import realtime_voice_session as rvs


class _FakeDB:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *a):
        return False


def _voice(user_id="u1"):
    s = rvs.RealtimeVoiceSession.__new__(rvs.RealtimeVoiceSession)
    s.user_id = user_id
    s.agent_id = "a1"
    return s


def _bridge_session(user_id="u1", caps=("input_capture", "screenshots")):
    return {
        "user_id": user_id, "bridge_connected": True, "bridge_ws": object(),
        "allowed_capabilities": set(caps), "pending_results": {},
        "recording": False, "recording_steps": [], "action_count": 0,
    }


class LearnSkillVoiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        cu._sessions.clear()
        cu._sessions["s1"] = _bridge_session()

    async def asyncTearDown(self):
        cu._sessions.clear()

    def test_tool_is_registered_and_named(self):
        self.assertEqual(rvs.LEARN_SKILL_TOOL["toolSpec"]["name"], "learn_skill")

    async def test_no_bridge_means_clear_message(self):
        cu._sessions.clear()
        out = await _voice()._learn_skill("start")
        self.assertIn("keine Desktop-Bridge", out)

    async def test_start_requires_input_capture_capability(self):
        cu._sessions["s1"]["allowed_capabilities"] = {"screenshots"}
        out = await _voice()._learn_skill("start")
        self.assertIn("Eingaben mitschneiden", out)

    async def test_start_begins_recording(self):
        with patch.object(cu, "_send_bridge_action", AsyncMock(return_value={"ok": True})):
            out = await _voice()._learn_skill("start", goal="Rechnung anlegen")
        self.assertTrue(cu._sessions["s1"]["recording"])
        self.assertTrue(cu._sessions["s1"]["capture_human"])
        self.assertIn("schaue jetzt zu", out.lower())

    async def test_finish_without_recording_is_honest(self):
        out = await _voice()._learn_skill("finish")
        self.assertIn("zeichne gerade nichts", out.lower())

    async def test_finish_with_no_steps_hints_at_capability(self):
        cu._sessions["s1"]["recording"] = True
        cu._sessions["s1"]["recording_steps"] = []
        with patch.object(cu, "_send_bridge_action", AsyncMock(return_value={"ok": True})):
            out = await _voice()._learn_skill("finish")
        self.assertIn("keine Schritte", out)

    async def test_finish_builds_skill_from_recorded_steps(self):
        sess = cu._sessions["s1"]
        sess["recording"] = True
        sess["recording_steps"] = [
            {"action": "click", "params": {"x": 10, "y": 20}},
            {"action": "type", "params": {"text": "hallo"}},
        ]
        fake_skill = type("S", (), {"name": "rechnung-anlegen"})()
        import app.db.session as dbsession
        from app.services import replay_skill_service as rss
        with patch.object(cu, "_send_bridge_action", AsyncMock(return_value={"ok": True})), \
             patch.object(dbsession, "async_session_factory", lambda: _FakeDB()), \
             patch.object(rss, "create_skill_from_recording",
                          AsyncMock(return_value=fake_skill)) as author:
            out = await _voice()._learn_skill("finish", goal="Rechnung anlegen")
        self.assertFalse(sess["recording"])
        # Es MUSS derselbe Aufzeichnungs-Bau benutzt werden, nicht ein zweiter.
        author.assert_awaited_once()
        _, kwargs = author.await_args
        self.assertEqual(kwargs.get("created_by"), "u1")
        self.assertEqual(kwargs.get("goal_hint"), "Rechnung anlegen")
        self.assertIn("rechnung-anlegen", out)

    async def test_unknown_action(self):
        out = await _voice()._learn_skill("irgendwas")
        self.assertIn("sagt mir nichts", out)


if __name__ == "__main__":
    unittest.main()
