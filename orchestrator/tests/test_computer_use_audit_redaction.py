"""#828 Schritt 5: Audit-Protokoll und Replay-Transkript durften kein Geheimnis tragen.

Befund (eigene Messung am Quelltext, nicht aus dem Issue-Text): `dispatch_bridge_command`
schrieb `req.params` UNVERAENDERT in `session["audit_log"]` -- und, wenn gerade eine
Aufnahme lief, zusaetzlich in `session["recording_steps"]` MITSAMT Screenshot (jede
Aktion aus `_SCREEN_CHANGING_ACTIONS` haengt dort einen frischen Screenshot an). Tippt
der Agent ueber die Aktion `type` ein Passwort oder einen Einmalcode auf den echten
Bildschirm des Nutzers, landete der Klartext in beiden Protokollen -- aus dem
Replay-Transkript kann `recording/to-skill` sogar einen geteilten Skill bauen.

Diese Tests pruefen die Gegenmassnahme `_redacted_params`: nur die Felder, die laut
Bridge-Quelltext (`computer-use-bridge/bridge.py`) tatsaechlich Freitext tragen
("text" bei `type`/`clipboard_write`/`set_clipboard`, "value" bei `browser_fill`/
`ego_fill`), werden durch ihre Laenge ersetzt -- alles andere (Koordinaten, Selektor,
Tastenname, Ziel-App) bleibt unveraendert, und der tatsaechliche Befehl an die Bridge
bekommt weiterhin die unveraenderten Original-Params.
"""
import json
import time
import unittest

from app.api import computer_use as cu


class _FakeWS:
    def __init__(self):
        self.sent: list[str] = []

    async def send_text(self, msg):
        self.sent.append(msg)


def _session(user_id="u1", **over):
    s = {
        "user_id": user_id,
        "created_at": time.time(),
        "last_activity_at": time.time(),
        "bridge_connected": True,
        "bridge_ws": _FakeWS(),
        "action_count": 0,
        "audit_log": [],
        "pending_results": {},
        "allowed_capabilities": set(cu.DEFAULT_ALLOWED_CAPABILITIES),
        "agent_id": None,
        "recording": False,
        "recording_steps": [],
    }
    s.update(over)
    return s


class RedactedParamsPureFunctionTests(unittest.TestCase):
    def test_type_text_is_redacted_to_its_length(self):
        out = cu._redacted_params("type", {"text": "s3cr3t-pw"})
        self.assertEqual(out["text"], "<redacted:9 chars>")

    def test_clipboard_write_and_set_clipboard_are_both_covered(self):
        for action in ("clipboard_write", "set_clipboard"):
            with self.subTest(action=action):
                out = cu._redacted_params(action, {"text": "geheim"})
                self.assertEqual(out["text"], "<redacted:6 chars>")

    def test_browser_fill_and_ego_fill_redact_value_not_selector(self):
        for action in ("browser_fill", "ego_fill"):
            with self.subTest(action=action):
                out = cu._redacted_params(action, {"selector": "#password", "value": "geheim123"})
                self.assertEqual(out["value"], "<redacted:9 chars>")
                self.assertEqual(out["selector"], "#password")  # strukturell, kein Geheimnis

    def test_unrelated_action_is_returned_untouched(self):
        params = {"app": "Terminal"}
        out = cu._redacted_params("open_app", params)
        self.assertEqual(out, params)

    def test_key_action_is_not_treated_as_sensitive(self):
        """'key'/'hotkey' tragen Tastennamen ('enter', ['ctrl','c']), kein
        Freitext -- siehe bridge.py: beide lesen params['keys'], nicht 'text'."""
        out = cu._redacted_params("key", {"keys": ["enter"]})
        self.assertEqual(out["keys"], ["enter"])

    def test_empty_text_is_left_as_is(self):
        """Eine leere Eingabe ist kein Geheimnis -- und soll von einer fehlenden
        Eingabe unterscheidbar bleiben."""
        out = cu._redacted_params("type", {"text": ""})
        self.assertEqual(out["text"], "")

    def test_original_params_dict_is_not_mutated(self):
        """dispatch_bridge_command schickt dieselbe req.params-Referenz
        danach unveraendert an die Bridge -- eine In-Place-Maskierung wuerde
        dem Nutzer ein falsches Zeichen auf den Bildschirm bringen."""
        original = {"text": "s3cr3t-pw"}
        cu._redacted_params("type", original)
        self.assertEqual(original["text"], "s3cr3t-pw")


class DispatchAuditAndRecordingRedactionTests(unittest.IsolatedAsyncioTestCase):
    """End-to-end durch dispatch_bridge_command: die Bridge bekommt das echte
    Passwort, die beiden Protokolle nicht."""

    def setUp(self):
        cu._sessions.clear()
        cu._redis = None

    async def asyncTearDown(self):
        cu._sessions.clear()

    async def _dispatch_instantly(self, **kw):
        async def _instant(fut, timeout):
            return {"ok": True}

        import asyncio as _a
        orig = _a.wait_for
        _a.wait_for = _instant
        try:
            return await cu.dispatch_bridge_command(**kw)
        finally:
            _a.wait_for = orig

    async def test_audit_log_never_sees_the_typed_secret(self):
        cu._sessions["s1"] = _session()
        ws = cu._sessions["s1"]["bridge_ws"]

        await self._dispatch_instantly(
            session_id="s1", action="type", params={"text": "s3cr3t-pw"},
            caller_user_id="u1", caller_label="agent-1",
        )

        entry = cu._sessions["s1"]["audit_log"][-1]
        self.assertNotIn("s3cr3t-pw", json.dumps(entry))
        self.assertEqual(entry["params"]["text"], "<redacted:9 chars>")

        # Die Bridge selbst bekommt weiterhin den echten Text -- sonst tippt
        # sie das falsche (oder gar kein) Passwort.
        befehle = [json.loads(m) for m in ws.sent if json.loads(m).get("type") == "command"]
        self.assertEqual(befehle[0]["command"]["params"]["text"], "s3cr3t-pw")

    async def test_recording_transcript_never_sees_the_typed_secret_either(self):
        cu._sessions["s1"] = _session(recording=True)

        await self._dispatch_instantly(
            session_id="s1", action="type", params={"text": "s3cr3t-pw"},
            caller_user_id="u1", caller_label="agent-1",
        )

        steps = cu._sessions["s1"]["recording_steps"]
        self.assertEqual(len(steps), 1)
        self.assertNotIn("s3cr3t-pw", json.dumps(steps[0]))
        self.assertEqual(steps[0]["params"]["text"], "<redacted:9 chars>")

    async def test_non_sensitive_action_keeps_its_params_in_both_logs(self):
        """Regressionsschutz: die Maskierung darf harmlose Aktionen nicht
        anfassen -- 'open_app' hat kein Feld in _SENSITIVE_PARAM_FIELDS."""
        cu._sessions["s1"] = _session(recording=True)

        await self._dispatch_instantly(
            session_id="s1", action="open_app", params={"name": "Excel"},
            caller_user_id="u1", caller_label="agent-1",
        )

        self.assertEqual(cu._sessions["s1"]["audit_log"][-1]["params"], {"name": "Excel"})


if __name__ == "__main__":
    unittest.main()
