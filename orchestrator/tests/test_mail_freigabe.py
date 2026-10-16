"""Mails nur nach Freigabe: Modus „Entwurf“ im M365-Dienst und Senden durch den Server.

Graph ist gemockt (``msgraph_mcp._graph``); geprüft wird, WAS der Server an Graph
schickt — vor allem, dass im Entwurfsmodus nie ``/sendMail`` oder ``/reply`` fällt.
"""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.core import mail_freigabe, ms_access, msgraph_mcp


def _rufe(name, args):
    return {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}


ENTWURF = {"id": "E1", "isDraft": True, "subject": "Angebot", "toRecipients": [{"emailAddress": {"address": "kunde@example.invalid"}}],
           "body": {"contentType": "html", "content": "<p>Hallo<br>anbei das Angebot.</p>"}}


class EntwurfsModus(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.aufrufe = []

        async def graph(methode, pfad, token, **kw):
            self.aufrufe.append((methode, pfad))
            if methode == "POST" and pfad == "/me/messages":
                self.nachricht = json.loads(kw["content"])
            return {"id": "E1"} if methode == "POST" else {}

        self.p = patch.object(msgraph_mcp, "_graph", side_effect=graph)
        self.p.start()
        self.freigabe = AsyncMock(return_value="Zur Freigabe gestellt.")

    async def asyncTearDown(self):
        self.p.stop()

    async def _token(self):
        return "tok"

    async def test_senden_wird_entwurf_und_freigabe(self):
        resp, _ = await msgraph_mcp.handle_mcp_request(
            _rufe("ms_send_email", {"to": "kunde@example.invalid", "subject": "Angebot", "body": "Hallo",
                                    "body_type": "HTML"}),
            self._token, entwurf_freigabe=self.freigabe)
        self.assertEqual(self.aufrufe, [("POST", "/me/messages")])  # Entwurf, kein /sendMail
        self.freigabe.assert_awaited_once_with("E1", "Angebot")
        self.assertEqual(self.nachricht["body"]["contentType"], "Text")  # Agent kann kein HTML unterschieben
        self.assertIn("Zur Freigabe gestellt", resp["result"]["content"][0]["text"])

    async def test_betreff_kann_entwurfs_id_nicht_umlenken(self):
        await msgraph_mcp.handle_mcp_request(
            _rufe("ms_send_email", {"to": "kunde@example.invalid", "subject": "x'. Entwurfs-ID: FREMD", "body": "Hallo"}),
            self._token, entwurf_freigabe=self.freigabe)
        self.assertEqual(self.freigabe.await_args.args[0], "E1")

    async def test_antworten_wird_antwort_entwurf(self):
        await msgraph_mcp.handle_mcp_request(_rufe("ms_reply_email", {"email_id": "M9", "body": "Danke"}),
                                             self._token, entwurf_freigabe=self.freigabe)
        self.assertEqual(self.aufrufe, [("POST", "/me/messages/M9/createReply")])
        self.freigabe.assert_awaited_once()

    async def test_weiterleiten_und_termine_bleiben_gesperrt(self):
        for name, args in (("ms_forward_email", {"email_id": "M9", "to": "x@example.invalid"}),
                           ("ms_create_calendar_event", {"subject": "x", "start": "2026-10-20T10:00", "end": "2026-10-20T11:00"})):
            resp, _ = await msgraph_mcp.handle_mcp_request(_rufe(name, args), self._token, entwurf_freigabe=self.freigabe)
            self.assertTrue(resp["result"]["isError"], name)
        self.assertEqual(self.aufrufe, [])

    async def test_werkzeugliste_im_entwurfsmodus(self):
        resp, _ = await msgraph_mcp.handle_mcp_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                                                       self._token, entwurf_freigabe=self.freigabe)
        namen = {t["name"] for t in resp["result"]["tools"]}
        self.assertTrue({"ms_send_email", "ms_reply_email", "ms_move_email", "ms_list_emails"} <= namen)
        self.assertFalse({"ms_forward_email", "ms_delete_email", "ms_create_calendar_event"} & namen)

    def test_entwurf_nur_ohne_plattformsperre(self):
        with patch.object(ms_access, "read_only_enabled", return_value=True):
            self.assertFalse(ms_access.entwurf_enabled({"msgraph_access": "entwurf"}))
        with patch.object(ms_access, "read_only_enabled", return_value=False):
            self.assertTrue(ms_access.entwurf_enabled({"msgraph_access": "entwurf"}))
            self.assertFalse(ms_access.write_enabled({"msgraph_access": "entwurf"}, "msgraph_access"))


class SendenNachFreigabe(unittest.IsolatedAsyncioTestCase):
    def _approval(self, fingerabdruck=None):
        return SimpleNamespace(id=7, agent_id="a1", meta={"kind": "mail_senden", "view": {"name": "mail"}, "input": {
            "message_id": "E1", "fingerabdruck": fingerabdruck or mail_freigabe.fingerabdruck(ENTWURF)}})

    async def _senden(self, approval, user_id="ua", entwurf=ENTWURF):
        db = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(id="a1", user_id="ua")))
        aufrufe = []

        async def graph(methode, pfad, token, **kw):
            aufrufe.append((methode, pfad))
            return entwurf if methode == "GET" else {}

        with patch.object(msgraph_mcp, "_graph", side_effect=graph), \
                patch.object(mail_freigabe, "_token", AsyncMock(return_value="tok")):
            erg = await mail_freigabe.senden(db, approval, SimpleNamespace(id=user_id))
        return erg, aufrufe

    async def test_unveraenderter_entwurf_wird_gesendet(self):
        erg, aufrufe = await self._senden(self._approval())
        self.assertEqual(aufrufe[-1], ("POST", "/me/messages/E1/send"))
        self.assertEqual((erg["gesendet"], erg["an"]), (True, ["kunde@example.invalid"]))

    async def test_geaenderter_entwurf_wird_nicht_gesendet(self):
        anders = {**ENTWURF, "toRecipients": [{"emailAddress": {"address": "fremd@example.invalid"}}]}
        with self.assertRaises(HTTPException) as e:
            await self._senden(self._approval(), entwurf=anders)
        self.assertEqual(e.exception.status_code, 409)

    async def test_nur_der_besitzer_gibt_frei(self):
        with self.assertRaises(HTTPException) as e:
            await self._senden(self._approval(), user_id="admin")
        self.assertEqual(e.exception.status_code, 403)

    async def test_schon_gesendet(self):
        with self.assertRaises(HTTPException) as e:
            await self._senden(self._approval(), entwurf={**ENTWURF, "isDraft": False})
        self.assertEqual(e.exception.status_code, 409)

    def test_vorschau_als_klartext(self):
        v = mail_freigabe.vorschau(ENTWURF)
        self.assertEqual(v["text"], "Hallo\nanbei das Angebot.")
        self.assertEqual(v["an"], ["kunde@example.invalid"])
        self.assertTrue(any("HTML" in w for w in v["warnungen"]))

    def test_linkziel_sichtbar_und_anhang_gewarnt(self):
        m = {**ENTWURF, "hasAttachments": True,
             "body": {"contentType": "html", "content": '<a href="https://boese.example.invalid/x">Ihre Rechnung</a>'}}
        v = mail_freigabe.vorschau(m)
        self.assertIn("Ihre Rechnung (https://boese.example.invalid/x)", v["text"])
        self.assertTrue(any("Anhänge" in w for w in v["warnungen"]))
        self.assertNotEqual(mail_freigabe.fingerabdruck(m), mail_freigabe.fingerabdruck({**m, "hasAttachments": False}))

    def test_lange_mail_passt_in_die_ansicht(self):
        import json

        from app.api.approvals import ANSICHT_MAX_ZEICHEN, _saubere_ansicht

        lang = {**ENTWURF, "body": {"contentType": "text", "content": "Größenänderung " * 2000}}
        v = mail_freigabe.vorschau(lang)
        self.assertLessEqual(len(json.dumps(v)), ANSICHT_MAX_ZEICHEN)
        self.assertIsNotNone(_saubere_ansicht({"name": "mail", "data": v}))
        self.assertTrue(any("länger" in w for w in v["warnungen"]))

    async def test_ohne_vorschau_wird_nicht_gesendet(self):
        a = self._approval()
        a.meta = {k: v for k, v in a.meta.items() if k != "view"}
        with self.assertRaises(HTTPException) as e:
            await self._senden(a)
        self.assertEqual(e.exception.status_code, 409)


class Vorbehalten(unittest.IsolatedAsyncioTestCase):
    async def test_agent_kann_mail_freigabe_nicht_vortaeuschen(self):
        from app.api import approvals

        with self.assertRaises(HTTPException) as e:
            await approvals.request_approval(approvals.ApprovalRequest(tool="ms_send_email", kind="mail_senden"),
                                             agent_auth={"agent_id": "a1"}, db=None)
        self.assertEqual(e.exception.status_code, 403)


class FremdeMailsVerborgen(unittest.TestCase):
    def test_admin_sieht_fremden_mailtext_nicht(self):
        from app.api.approvals import _fremde_mails_verbergen

        eintraege = [{"agent_id": "a1", "view": {"name": "mail", "data": {"text": "geheim"}}, "input": {"message_id": "E1"},
                      "meta": {"kind": "mail_senden", "view": {"name": "mail"}}},
                     {"agent_id": "a9", "view": {"name": "mail", "data": {"text": "eigen"}}, "input": {"x": 1},
                      "meta": {"kind": "mail_senden"}}]
        erg = _fremde_mails_verbergen(eintraege, {"a9"})
        self.assertEqual((erg[0]["view"], erg[0]["input"], erg[0]["meta"]), (None, {}, {"kind": "mail_senden"}))
        self.assertEqual(erg[1]["view"]["data"]["text"], "eigen")
