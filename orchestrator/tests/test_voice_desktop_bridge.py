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
            out = await v._desktop("open", "https://servicedesk.example.com/wm")
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

    async def test_screenshot_returns_at_once_and_analyses_in_parallel(self):
        """Die Auswertung darf das Gespraech NICHT blockieren. Frueher wurde hier bis
        zu 90s auf den Agenten gewartet — im Sprachmodus eine Ewigkeit."""
        import asyncio
        import app.services.realtime_voice_session as rvs
        v = self._voice()
        started = asyncio.Event()

        async def _slow(*_a, **_kw):
            started.set()
            await asyncio.sleep(30)          # laenger als jeder Test warten wuerde
            return "kommt nie an"

        with unittest.mock.patch.object(
            cu, "_find_user_session", new=AsyncMock(return_value=("s1", _session()))
        ), unittest.mock.patch.object(
            cu, "dispatch_bridge_command",
            new=AsyncMock(return_value={"result": {"screenshot_b64": "abc"}}),
        ), unittest.mock.patch.object(rvs, "ask_agent_via_chat", new=_slow):
            out = await asyncio.wait_for(v._desktop("screenshot"), timeout=2.0)
            await asyncio.wait_for(started.wait(), timeout=2.0)   # laeuft wirklich an
            for t in asyncio.all_tasks():
                if t is not asyncio.current_task():
                    t.cancel()

        self.assertIn("draufschaust", out)
        self.assertIn("beschreibe NICHTS", out)
        v._emit.assert_awaited()

    async def test_analysis_result_is_spoken_when_it_arrives(self):
        """Das Ergebnis wird eingespeist, sobald es da ist."""
        import app.services.realtime_voice_session as rvs
        v = self._voice()
        v._closed = False
        v._drop_audio = False
        v._last_spoken = 0.0
        v._nova = unittest.mock.MagicMock()
        v._nova.inject_user_text = AsyncMock()
        with unittest.mock.patch.object(
            rvs, "ask_agent_via_chat", new=AsyncMock(return_value="Excel ist offen."),
        ):
            await v._analyse_screenshot_bg("abc", "was siehst du?", "darwin")
        said = v._nova.inject_user_text.await_args[0][0]
        self.assertIn("Excel ist offen", said)

    async def test_failing_analysis_is_admitted_not_invented(self):
        """Kommt keine Auswertung zurueck, wird das gesagt — nicht geraten."""
        import app.services.realtime_voice_session as rvs
        v = self._voice()
        v._closed = False
        v._drop_audio = False
        v._last_spoken = 0.0
        v._nova = unittest.mock.MagicMock()
        v._nova.inject_user_text = AsyncMock()
        with unittest.mock.patch.object(
            rvs, "ask_agent_via_chat", new=AsyncMock(return_value="[Fehler: Timeout]"),
        ):
            await v._analyse_screenshot_bg("abc", "", "")
        said = v._nova.inject_user_text.await_args[0][0]
        # Seit dem 21.08.2026 wird der GRUND mitgesagt, nicht nur „kam nicht
        # zurueck": damals lautete er „You've hit your limit · resets 3:10pm",
        # und der Nutzer suchte eine halbe Stunde bei den Bildern, weil die
        # Stimme ihn verschwieg. Geprueft wird weiterhin die Haltung — Fehler
        # eingestehen statt etwas zu erfinden.
        self.assertIn("fehlgeschlagen", said)
        self.assertIn("Timeout", said)
        self.assertIn("erfinde nichts", said.lower())


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


class EgoAndRawPassthroughTests(unittest.IsolatedAsyncioTestCase):
    """#ego-lite (27.08.2026): live gemeldet, dass die Sprachfront erst `open_app`
    mit Namensraten versuchte statt `ego_run` direkt zu nutzen, und danach
    forderte der Nutzer ausdruecklich "1:1 die gleichen Tools wie der Agent".
    Die Kurzform `action='ego'` deckt den ersten Fall ab; die Rohdurchreiche
    (jeder echte Bridge-Aktionsname + `params`) deckt den zweiten strukturell
    ab — keine Aktion soll je wieder von Hand nachgetragen werden muessen.
    """

    def _voice(self):
        from app.services.realtime_voice_session import RealtimeVoiceSession
        v = RealtimeVoiceSession.__new__(RealtimeVoiceSession)
        v.user_id, v.agent_id = "u1", "agent-1"
        v.redis = AsyncMock()
        v._emit = AsyncMock()
        return v

    async def _run(self, action, target="", text="", raw_params=None, result=None):
        v = self._voice()
        with unittest.mock.patch.object(
            cu, "_find_user_session", new=AsyncMock(return_value=("s1", _session()))
        ), unittest.mock.patch.object(
            cu, "dispatch_bridge_command", new=AsyncMock(return_value={"result": result or {}}),
        ) as disp:
            out = await v._desktop(action, target, text, raw_params=raw_params)
        return out, disp

    async def test_ego_maps_to_ego_run_with_the_script_in_text(self):
        _out, disp = await self._run(
            "ego", text="cliLog('hi')", result={"ok": True, "output": "hi\n"},
        )
        self.assertEqual(disp.await_args[0][1], "ego_run")
        self.assertEqual(disp.await_args[0][2], {"script": "cliLog('hi')"})

    async def test_ego_without_a_script_asks_back_instead_of_calling_the_bridge(self):
        out, disp = await self._run("ego", text="")
        self.assertIn("fehlt", out.lower())
        disp.assert_not_awaited()

    async def test_ego_output_is_spoken_verbatim(self):
        out, _ = await self._run(
            "ego", text="cliLog('geoeffnet: google.com')",
            result={"ok": True, "output": "geoeffnet: google.com\n"},
        )
        self.assertIn("geoeffnet: google.com", out)

    async def test_an_unrecognised_action_passes_straight_through(self):
        """Keine der acht Kurzformen — genau der Fall, der ego_run erst gefehlt hat."""
        _out, disp = await self._run(
            "browser_navigate", raw_params={"url": "https://intranet/reisekosten"},
            result={"ok": True, "url": "https://intranet/reisekosten"},
        )
        self.assertEqual(disp.await_args[0][1], "browser_navigate")
        self.assertEqual(disp.await_args[0][2], {"url": "https://intranet/reisekosten"})

    async def test_passthrough_result_is_reported_not_just_erledigt(self):
        """Ohne das laesst sich am gesprochenen "Erledigt" nicht ablesen, WAS z. B.
        shell_run tatsaechlich geliefert hat."""
        out, _ = await self._run(
            "shell_run", raw_params={"command": "ls"},
            result={"ok": True, "stdout": "todo.md\n"},
        )
        self.assertIn("todo.md", out)
        self.assertNotEqual(out.strip(), "Erledigt.")

    async def test_passthrough_still_goes_through_the_same_capability_gate(self):
        """Rohdurchreiche ist kein zweiter, schwaecherer Weg — dieselbe Sperre gilt."""
        v = self._voice()
        with unittest.mock.patch.object(
            cu, "_find_user_session", new=AsyncMock(return_value=("s1", _session()))
        ), unittest.mock.patch.object(
            cu, "dispatch_bridge_command",
            new=AsyncMock(side_effect=HTTPException(
                status_code=403, detail="Action 'ego_run' is not permitted",
            )),
        ):
            out = await v._desktop("ego_run", raw_params={"script": "cliLog(1)"})
        self.assertIn("not permitted", out)

    async def test_an_empty_action_asks_back_instead_of_guessing(self):
        out, disp = await self._run("")
        self.assertIn("fehlt", out.lower())
        disp.assert_not_awaited()
