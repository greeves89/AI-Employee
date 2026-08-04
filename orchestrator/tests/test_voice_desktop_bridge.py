"""#489: Der Sprach-Agent darf die Desktop-Bridge bedienen — mit denselben Schranken.

Bisher hatte die Realtime-Sprachsitzung keinerlei `computer_*`-Werkzeug und wimmelte
interne Adressen ab („ruf es selbst im Browser auf"). Jetzt geht sie über
`dispatch_bridge_command` — dieselbe Funktion wie der HTTP-Endpunkt.

Der Kern dieser Tests ist NICHT, dass es funktioniert, sondern dass der neue Weg
**keine Abkürzung** ist: Besitz, Capability-Freigabe, Aktions-Limit und Timeout
müssen für den Sprach-Agenten genauso greifen. Es geht um Maus und Tastatur auf dem
echten Rechner eines Menschen.
"""

import time
import unittest
from unittest.mock import AsyncMock

from fastapi import HTTPException

from app.api import computer_use as cu


class _FakeWS:
    def __init__(self):
        self.sent: list[str] = []

    async def send_text(self, msg):
        self.sent.append(msg)


def _session(user_id="u1", caps=None, connected=True, **over):
    s = {
        "user_id": user_id,
        "created_at": time.time(),
        "last_activity_at": time.time(),
        "bridge_connected": connected,
        "bridge_ws": _FakeWS() if connected else None,
        "action_count": 0,
        "audit_log": [],
        "pending_results": {},
        "allowed_capabilities": set(caps if caps is not None else cu.DEFAULT_ALLOWED_CAPABILITIES),
        "agent_id": None,
        "recording": False,
        "recording_steps": [],
    }
    s.update(over)
    return s


class DispatchGuardTests(unittest.IsolatedAsyncioTestCase):
    """Die Schranken in `dispatch_bridge_command` — unabhaengig vom Aufrufer."""

    def setUp(self):
        cu._sessions.clear()
        cu._redis = None
        cu._sessions["s1"] = _session()

    async def asyncTearDown(self):
        cu._sessions.clear()

    async def _call(self, action="open_app", params=None, user="u1", **kw):
        return await cu.dispatch_bridge_command(
            "s1", action, params if params is not None else {"name": "https://intranet/x"},
            caller_user_id=user, caller_label="voice:agent-1", **kw,
        )

    async def test_foreign_user_is_refused(self):
        """Der Sprach-Agent eines anderen Nutzers darf hier nicht durch."""
        with self.assertRaises(HTTPException) as cm:
            await self._call(user="u2")
        self.assertEqual(cm.exception.status_code, 403)

    async def test_disabled_capability_is_refused(self):
        """Hat der Nutzer 'apps' nicht freigegeben, oeffnet auch die Stimme nichts."""
        cu._sessions["s1"]["allowed_capabilities"] = {"screenshots"}
        with self.assertRaises(HTTPException) as cm:
            await self._call(action="open_app")
        self.assertEqual(cm.exception.status_code, 403)
        self.assertIn("apps", cm.exception.detail)

    async def test_unknown_action_is_refused(self):
        """Unbekannte Aktionen sind nicht freigegeben — fail-closed."""
        with self.assertRaises(HTTPException) as cm:
            await self._call(action="format_disk", params={})
        self.assertEqual(cm.exception.status_code, 403)

    async def test_action_limit_applies(self):
        cu._sessions["s1"]["action_count"] = cu.MAX_ACTIONS_PER_SESSION
        with self.assertRaises(HTTPException) as cm:
            await self._call()
        self.assertEqual(cm.exception.status_code, 429)

    async def test_expired_session_is_refused_and_forgotten(self):
        cu._sessions["s1"]["last_activity_at"] = time.time() - cu.SESSION_TIMEOUT_SECS - 10
        with self.assertRaises(HTTPException) as cm:
            await self._call()
        self.assertEqual(cm.exception.status_code, 410)
        self.assertNotIn("s1", cu._sessions)

    async def test_missing_bridge_is_refused(self):
        cu._sessions["s1"]["bridge_connected"] = False
        cu._sessions["s1"]["bridge_ws"] = None
        with self.assertRaises(HTTPException) as cm:
            await self._call()
        self.assertEqual(cm.exception.status_code, 503)

    async def test_session_assigned_to_another_agent_is_refused(self):
        cu._sessions["s1"]["agent_id"] = "agent-A"
        with self.assertRaises(HTTPException) as cm:
            await self._call(caller_agent_id="agent-B")
        self.assertEqual(cm.exception.status_code, 403)

    async def test_allowed_action_reaches_the_bridge_and_is_audited(self):
        ws = cu._sessions["s1"]["bridge_ws"]

        async def _instant(fut, timeout):
            return {"ok": True}

        import asyncio as _a
        orig = _a.wait_for
        _a.wait_for = _instant
        try:
            await self._call()
        finally:
            _a.wait_for = orig

        self.assertEqual(len(ws.sent), 1)
        self.assertEqual(cu._sessions["s1"]["action_count"], 1)
        entry = cu._sessions["s1"]["audit_log"][-1]
        self.assertEqual(entry["action"], "open_app")
        self.assertEqual(entry["caller"], "voice:agent-1")   # der Sprachweg ist nachvollziehbar


class VoiceToolTests(unittest.IsolatedAsyncioTestCase):
    """Das Sprach-Werkzeug selbst: verstaendliche Ansagen statt Ausweichen."""

    def _voice(self, user_id="u1"):
        from app.services.realtime_voice_session import RealtimeVoiceSession
        v = RealtimeVoiceSession.__new__(RealtimeVoiceSession)
        v.user_id = user_id
        v.agent_id = "agent-1"
        v.redis = AsyncMock()
        v._emit = AsyncMock()
        return v

    async def test_no_bridge_says_so_instead_of_deflecting(self):
        v = self._voice()
        with unittest.mock.patch.object(cu, "_find_user_session", new=AsyncMock(return_value=None)):
            out = await v._desktop("open", "https://servicedesk.skbs.de/wm")
        self.assertIn("Bridge", out)
        self.assertNotIn("selbst", out.lower())   # kein "ruf es selbst auf"

    async def test_open_without_target_asks_back(self):
        v = self._voice()
        with unittest.mock.patch.object(
            cu, "_find_user_session", new=AsyncMock(return_value=("s1", _session()))
        ):
            out = await v._desktop("open", "")
        self.assertIn("fehlt", out.lower())

    async def test_click_without_coordinates_asks_for_a_screenshot(self):
        v = self._voice()
        with unittest.mock.patch.object(
            cu, "_find_user_session", new=AsyncMock(return_value=("s1", _session()))
        ):
            out = await v._desktop("click")
        self.assertIn("Screenshot", out)

    async def test_refusal_reason_is_passed_through_verbatim(self):
        """Sperrt die Capability, muss der Nutzer DAS hoeren — nicht eine Ausrede."""
        v = self._voice()
        with unittest.mock.patch.object(
            cu, "_find_user_session", new=AsyncMock(return_value=("s1", _session()))
        ), unittest.mock.patch.object(
            cu, "dispatch_bridge_command",
            new=AsyncMock(side_effect=HTTPException(status_code=403, detail="Action 'type' is not permitted")),
        ):
            out = await v._desktop("type", text="hallo")
        self.assertIn("not permitted", out)

    async def test_empty_screenshot_is_not_described(self):
        """Kein Bild heisst kein Bild — nicht raten, was drauf sein koennte."""
        v = self._voice()
        with unittest.mock.patch.object(
            cu, "_find_user_session", new=AsyncMock(return_value=("s1", _session()))
        ), unittest.mock.patch.object(
            cu, "dispatch_bridge_command", new=AsyncMock(return_value={"result": {}}),
        ):
            out = await v._desktop("screenshot")
        self.assertIn("leer", out.lower())
        v._emit.assert_not_awaited()



class VoiceAgentScopingTests(unittest.IsolatedAsyncioTestCase):
    """Der Sprachweg darf keine Hintertuer an der Agenten-Zuordnung vorbei sein."""

    def _voice(self, agent_id="agent-B"):
        from app.services.realtime_voice_session import RealtimeVoiceSession
        v = RealtimeVoiceSession.__new__(RealtimeVoiceSession)
        v.user_id = "u1"
        v.agent_id = agent_id
        v.redis = AsyncMock()
        v._emit = AsyncMock()
        return v

    async def test_voice_passes_its_agent_id_through(self):
        """Ohne caller_agent_id greift die Zuordnungspruefung in dispatch nicht."""
        seen = {}

        async def _spy(session_id, action, params, **kw):
            seen.update(kw)
            return {"result": {}}

        v = self._voice("agent-B")
        with unittest.mock.patch.object(
            cu, "_find_user_session", new=AsyncMock(return_value=("s1", _session()))
        ), unittest.mock.patch.object(cu, "dispatch_bridge_command", new=_spy):
            await v._desktop("open", "https://intranet/x")
        self.assertEqual(seen.get("caller_agent_id"), "agent-B")

    async def test_session_of_another_agent_is_refused_end_to_end(self):
        cu._sessions.clear()
        cu._redis = None
        cu._sessions["s1"] = _session(agent_id="agent-A")
        v = self._voice("agent-B")
        with unittest.mock.patch.object(
            cu, "_find_user_session", new=AsyncMock(return_value=("s1", cu._sessions["s1"]))
        ):
            out = await v._desktop("open", "https://intranet/x")
        cu._sessions.clear()
        self.assertIn("different agent", out)

    async def test_unknown_user_never_reaches_the_bridge(self):
        v = self._voice()
        v.user_id = "unknown"
        find = AsyncMock(return_value=("s1", _session()))
        with unittest.mock.patch.object(cu, "_find_user_session", new=find):
            out = await v._desktop("open", "https://intranet/x")
        find.assert_not_awaited()
        self.assertIn("zuordnen", out.lower())

    async def test_non_numeric_coordinates_do_not_hang_the_turn(self):
        """Vorher flog hier eine ValueError am try/except vorbei — der Sprach-Turn
        bekam nie eine Antwort und blieb stehen (Review-Fund M1)."""
        v = self._voice()
        with unittest.mock.patch.object(
            cu, "_find_user_session", new=AsyncMock(return_value=("s1", _session()))
        ):
            out = await v._desktop("click", x="links", y="oben")
        self.assertIn("Zahlen", out)

    async def test_screenshot_goes_to_the_bound_agent_with_the_image(self):
        """Nova Sonic hat keinen Bildkanal — aber der Agent, an dem die Stimme
        haengt, sieht Bilder mit seinem eigenen Zugang. Genau dorthin geht es."""
        import app.services.realtime_voice_session as rvs
        v = self._voice()
        with unittest.mock.patch.object(
            cu, "_find_user_session", new=AsyncMock(return_value=("s1", _session()))
        ), unittest.mock.patch.object(
            cu, "dispatch_bridge_command",
            new=AsyncMock(return_value={"result": {"screenshot_b64": "abc"}}),
        ), unittest.mock.patch.object(
            rvs, "ask_agent_via_chat", new=AsyncMock(return_value="Excel ist offen."),
        ) as ask:
            out = await v._desktop("screenshot")
        self.assertIn("Excel ist offen", out)
        imgs = ask.await_args.kwargs["images"]
        self.assertEqual(imgs, [{"media_type": "image/png", "data": "abc"}])
        v._emit.assert_awaited()

    async def test_failing_analysis_is_admitted_not_invented(self):
        """Kommt keine Auswertung zurueck, wird das gesagt — nicht geraten."""
        import app.services.realtime_voice_session as rvs
        v = self._voice()
        with unittest.mock.patch.object(
            cu, "_find_user_session", new=AsyncMock(return_value=("s1", _session()))
        ), unittest.mock.patch.object(
            cu, "dispatch_bridge_command",
            new=AsyncMock(return_value={"result": {"screenshot_b64": "abc"}}),
        ), unittest.mock.patch.object(
            rvs, "ask_agent_via_chat", new=AsyncMock(return_value="[Fehler: Timeout]"),
        ):
            out = await v._desktop("screenshot")
        self.assertIn("nicht zurueck", out)
        self.assertIn("erfinde nichts", out.lower())


if __name__ == "__main__":
    unittest.main()


class BridgeResultHonestyTests(unittest.IsolatedAsyncioTestCase):
    """Aus dem Live-Mitschnitt 2026-08-04: „Chrome ist jetzt bei dir geoeffnet."
    Chrome war nicht offen. Die Bridge hatte ok=False gemeldet, der Handler gab
    trotzdem stur Erfolg zurueck — der Agent behauptete es dann gutglaeubig.
    """

    def _voice(self):
        from app.services.realtime_voice_session import RealtimeVoiceSession
        v = RealtimeVoiceSession.__new__(RealtimeVoiceSession)
        v.user_id, v.agent_id = "u1", "agent-1"
        v.redis = AsyncMock()
        v._emit = AsyncMock()
        return v

    async def _run(self, action, target="", result=None):
        v = self._voice()
        with unittest.mock.patch.object(
            cu, "_find_user_session", new=AsyncMock(return_value=("s1", _session()))
        ), unittest.mock.patch.object(
            cu, "dispatch_bridge_command", new=AsyncMock(return_value={"result": result or {}}),
        ) as disp:
            out = await v._desktop(action, target)
        return out, disp

    async def test_failed_open_is_reported_as_failure(self):
        out, _ = await self._run("open", "Google Chrome",
                                 result={"ok": False, "error": '"Google Chrome" not found'})
        self.assertIn("NICHT geklappt", out)
        self.assertIn("not found", out)
        self.assertNotIn("wurde geöffnet", out)

    async def test_successful_open_is_reported_as_success(self):
        out, _ = await self._run("open", "Safari", result={"ok": True, "app": "Safari"})
        self.assertIn("geöffnet", out)

    async def test_url_uses_open_url_not_open_app(self):
        """`open -a <url>` gibt es nicht — genau daran scheiterte „oeffne google"."""
        _out, disp = await self._run("open", "https://google.de", result={"ok": True})
        act = disp.await_args[0][1]
        self.assertEqual(act, "open_url")

    async def test_bare_domain_gets_a_scheme(self):
        _out, disp = await self._run("open", "google.de", result={"ok": True})
        self.assertEqual(disp.await_args[0][1], "open_url")
        self.assertEqual(disp.await_args[0][2]["url"], "https://google.de")

    async def test_plain_app_name_still_uses_open_app(self):
        _out, disp = await self._run("open", "Taschenrechner", result={"ok": True})
        self.assertEqual(disp.await_args[0][1], "open_app")

    async def test_open_url_is_a_known_capability(self):
        """Unbekannte Aktionen sind fail-closed — ohne Eintrag waere open_url tot."""
        self.assertEqual(cu._ACTION_TO_GROUP.get("open_url"), "apps")
