"""#828 Schritt 5: Audit-Protokoll und Replay-Transkript durften kein Geheimnis tragen.

Befund (eigene Messung am Quelltext, nicht aus dem Issue-Text): `dispatch_bridge_command`
schrieb `req.params` UNVERAENDERT in `session["audit_log"]` -- und, wenn gerade eine
Aufnahme lief, zusaetzlich in `session["recording_steps"]` MITSAMT Screenshot (jede
Aktion aus `_SCREEN_CHANGING_ACTIONS` haengt dort einen frischen Screenshot an). Tippt
der Agent ueber die Aktion `type` ein Passwort oder einen Einmalcode auf den echten
Bildschirm des Nutzers, landete der Klartext in beiden Protokollen -- aus dem
Replay-Transkript kann `recording/to-skill` sogar einen geteilten Skill bauen.

Diese Tests pruefen die Gegenmassnahme `_redacted_params`: Sichtbar bleiben nur
Felder aus einer Zulassliste (Koordinaten, Selektor, Ziel-App, benannte Tasten);
Freitext und jedes unbekannte Feld werden durch ihre Laenge ersetzt, Adressen
verlieren Query und Fragment. Der tatsaechliche Befehl an die Bridge bekommt
weiterhin die unveraenderten Original-Params.
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

    def test_benannte_tasten_bleiben_einzelne_zeichen_nicht(self):
        """Ein Passwort Taste fuer Taste ist getippter Text."""
        self.assertEqual(cu._redacted_params("key", {"keys": ["enter"]})["keys"], ["enter"])
        self.assertEqual(cu._redacted_params("key", {"keys": "tab"})["keys"], "tab")
        self.assertEqual(cu._redacted_params("hotkey", {"keys": ["ctrl", "v"]})["keys"], ["ctrl", "v"])
        self.assertEqual(cu._redacted_params("key", {"keys": "p"})["keys"], "<redacted:key>")
        self.assertEqual(cu._redacted_params("key", {"keys": ["shift", "a"]})["keys"], "<redacted:key>")

    def test_shell_und_skripte_werden_geschwaerzt(self):
        befehl = cu._redacted_params("shell_run", {"command": "curl -u admin:pw https://x", "cwd": "/tmp"})
        self.assertEqual(befehl["command"], "<redacted:26 chars>")
        self.assertEqual(befehl["cwd"], "/tmp")
        skript = cu._redacted_params("ego_run", {"script": "document.querySelector('#pw').value='x'"})
        self.assertNotIn("#pw", json.dumps(skript))

    def test_adressen_ohne_query_und_zugangsdaten(self):
        for action in ("open_url", "browser_navigate", "ego_navigate"):
            with self.subTest(action=action):
                out = cu._redacted_params(action, {"url": "https://u:p@login.example.com/cb?code=GEHEIM#t=1"})
                self.assertEqual(out["url"], "https://login.example.com/cb")

    def test_unbekannte_felder_jeder_aktion_werden_geschwaerzt(self):
        """Zulassen statt sperren: auch ein Feld, das eine kuenftige Aktion einfuehrt."""
        alle = {a for aktionen in cu.CAPABILITY_GROUPS.values() for a in aktionen}
        for action in sorted(alle):
            with self.subTest(action=action):
                out = cu._redacted_params(action, {"neues_feld": "geheim-123", "text": "pw-456"})
                self.assertNotIn("geheim-123", json.dumps(out))
                self.assertNotIn("pw-456", json.dumps(out))

    def test_params_ohne_dict_werfen_nicht(self):
        self.assertEqual(cu._redacted_params("type", None), {})
        self.assertEqual(cu._redacted_params("type", "pw"), {})

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
        anfassen -- 'name' bei 'open_app' steht in der Zulassliste."""
        cu._sessions["s1"] = _session(recording=True)

        await self._dispatch_instantly(
            session_id="s1", action="open_app", params={"name": "Excel"},
            caller_user_id="u1", caller_label="agent-1",
        )

        self.assertEqual(cu._sessions["s1"]["audit_log"][-1]["params"], {"name": "Excel"})


    async def test_nach_type_kein_bild_im_transkript(self):
        cu._sessions["s1"] = _session(recording=True)

        async def _bild(session_id):
            return "BILD"

        orig = cu._refresh_screenshot_cache
        cu._refresh_screenshot_cache = _bild
        try:
            await self._dispatch_instantly(
                session_id="s1", action="type", params={"text": "123456"},
                caller_user_id="u1", caller_label="agent-1",
            )
        finally:
            cu._refresh_screenshot_cache = orig
        self.assertIsNone(cu._sessions["s1"]["recording_steps"][-1]["screenshot_b64"])


class MenschlicheAufzeichnung(unittest.TestCase):
    """Fuehrt der Mensch vor, schickt die Bridge das komplett getippte Wort."""

    def test_getipptes_und_bild_kommen_nicht_ins_transkript(self):
        session = _session(recording=True)
        cu._menschlichen_schritt_aufzeichnen(session, {"event": {
            "action": "type", "params": {"text": "s3cr3t-pw"}, "ts": 1.0,
            "screenshot_b64": "BILD", "source": "human", "extra": "s3cr3t-pw",
        }})
        schritt = session["recording_steps"][-1]
        self.assertNotIn("s3cr3t-pw", json.dumps(schritt))
        self.assertIsNone(schritt["screenshot_b64"])
        self.assertEqual("human", schritt["source"])

    def test_klick_behaelt_koordinaten_und_bild(self):
        session = _session(recording=True)
        cu._menschlichen_schritt_aufzeichnen(session, {"event": {
            "action": "click", "params": {"x": 10, "y": 20, "button": "left"},
            "screenshot_b64": "BILD",
        }})
        schritt = session["recording_steps"][-1]
        self.assertEqual({"x": 10, "y": 20, "button": "left"}, schritt["params"])
        self.assertEqual("BILD", schritt["screenshot_b64"])

    def test_kaputtes_ereignis_und_ohne_aufnahme_nichts(self):
        session = _session(recording=True)
        for msg in ({"event": None}, {"event": {"action": 5}}, {"event": {"params": {}}}):
            cu._menschlichen_schritt_aufzeichnen(session, msg)
        self.assertEqual([], session["recording_steps"])
        aus = _session(recording=False)
        cu._menschlichen_schritt_aufzeichnen(aus, {"event": {"action": "click", "params": {}}})
        self.assertEqual([], aus["recording_steps"])


if __name__ == "__main__":
    unittest.main()
