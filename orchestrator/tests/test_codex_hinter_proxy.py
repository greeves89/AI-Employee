"""Codex-Zugang hinter dem Egress-Proxy (EGRESS_CODEX_ZUGANG).

Die Codex-CLI zerlegt ihr Zugangs-Token nicht, sondern schickt es als Kopfzeile an
chatgpt.com (auf einer Anlage mit einem Platzhalter geprüft). Damit kann es wie der
Claude-Zugang ein Platzhalter sein, den der Proxy dort durch den aktuellen Wert ersetzt.
"""

import json
import unittest
from unittest.mock import AsyncMock, patch

from app.config import settings
from app.core import egress

AUTH = {"auth_mode": "chatgpt", "last_refresh": "2026-10-31T20:06:08Z",
        "tokens": {"id_token": "a.b.c", "access_token": "x.y.z", "refresh_token": "rt-echt", "account_id": "acc"}}


def _stufe(wert):
    return patch.multiple(settings, egress_proxy_enabled=True, egress_proxy_token="t" * 40,
                          egress_codex_zugang=wert, create=True)


class Umstellen(unittest.TestCase):
    def test_platzhalter_statt_tokens(self):
        env = {"CODEX_AUTH_JSON": json.dumps(AUTH)}
        with _stufe("a1"), patch("app.core.encryption.encrypt_token", lambda w: "enc:" + w):
            gespeichert = egress.codex_umstellen("a1", env)
        neu = json.loads(env["CODEX_AUTH_JSON"])
        self.assertEqual(neu["tokens"]["access_token"], egress.platzhalter(egress.KI_CODEX, egress.KI))
        self.assertEqual(neu["tokens"]["refresh_token"], egress.CODEX_OHNE_ERNEUERUNG)
        self.assertEqual(neu["tokens"]["account_id"], "acc")
        self.assertNotIn("rt-echt", env["CODEX_AUTH_JSON"])
        self.assertNotIn("x.y.z", env["CODEX_AUTH_JSON"])
        self.assertEqual(gespeichert, {"CODEX_ACCESS_TOKEN": "enc:x.y.z"})
        self.assertIsNone(egress.platzhalter_aus(egress.CODEX_OHNE_ERNEUERUNG), "nie auflösbar")

    def test_nichts_vom_original_bleibt(self):
        """Keine Signatur, kein Erneuerungs-Token, kein Schlüssel im Agenten — nur die Angaben."""
        kopf, inhalt, signatur = "eyJhbGciOiJSUzI1NiJ9", "eyJlbWFpbCI6ImFAZXhhbXBsZS5jb20ifQ", "SIGNATUR-ECHT"
        roh = {**AUTH, "OPENAI_API_KEY": "sk-echt",
               "tokens": {**AUTH["tokens"], "id_token": f"{kopf}.{inhalt}.{signatur}"}}
        env = {"CODEX_AUTH_JSON": json.dumps(roh)}
        with _stufe("a1"), patch("app.core.encryption.encrypt_token", lambda w: "enc:" + w):
            egress.codex_umstellen("a1", env)
        neu = json.loads(env["CODEX_AUTH_JSON"])
        for echt in ("x.y.z", "rt-echt", "sk-echt", signatur, kopf):
            self.assertNotIn(echt, env["CODEX_AUTH_JSON"])
        self.assertIsNone(neu["OPENAI_API_KEY"])
        teile = neu["tokens"]["id_token"].split(".")
        self.assertEqual(len(teile), 3)
        self.assertEqual(teile[1], inhalt, "Angaben für die CLI bleiben lesbar")
        self.assertTrue(all(teile), "die CLI verlangt drei nicht leere Teile")

    def test_unlesbares_id_token(self):
        for kaputt in (None, "", "ohne-punkte", "a..c"):
            with self.subTest(kaputt=kaputt):
                env = {"CODEX_AUTH_JSON": json.dumps({**AUTH, "tokens": {**AUTH["tokens"], "id_token": kaputt}})}
                with _stufe("a1"), patch("app.core.encryption.encrypt_token", lambda w: "enc:" + w):
                    egress.codex_umstellen("a1", env)
                # Ein Nicht-JWT schaltet die CLI auf api.openai.com um: immer lesbar halten.
                self.assertEqual(json.loads(env["CODEX_AUTH_JSON"])["tokens"]["id_token"],
                                 egress._JWT_UNSIGNIERT + ".e30.unsigniert")

    def test_nur_in_der_stufe(self):
        for wert, agent in (("", "a1"), ("b2", "a1")):
            with self.subTest(wert=wert), _stufe(wert):
                env = {"CODEX_AUTH_JSON": json.dumps(AUTH)}
                self.assertEqual(egress.codex_umstellen(agent, env), {})
                self.assertIn("rt-echt", env["CODEX_AUTH_JSON"])
        with _stufe("alle"), patch("app.core.encryption.encrypt_token", lambda w: w):
            self.assertTrue(egress.codex_umstellen("a1", {"CODEX_AUTH_JSON": json.dumps(AUTH)}))

    def test_ohne_codex_oder_kaputt(self):
        with _stufe("alle"):
            self.assertEqual(egress.codex_umstellen("a1", {}), {})
            self.assertEqual(egress.codex_umstellen("a1", {"CODEX_AUTH_JSON": "kein json"}), {})

    def test_hinweis_aktualisieren_bei_codex_stufe(self):
        with _stufe("a1"), patch.object(settings, "egress_ki_zugang", "", create=True):
            self.assertTrue(egress.veraltet({"egress": True}, "a1"))
            self.assertFalse(egress.veraltet({"egress": True, "egress_codex_stufe": True}, "a1"))


class Aufloesen(unittest.IsolatedAsyncioTestCase):
    async def test_aktuelles_token_nur_fuer_chatgpt(self):
        from types import SimpleNamespace

        from app.api import egress as api
        from app.core import agent_credentials as creds

        agent = SimpleNamespace(id="a1", user_id="u1", mode="codex_cli",
                                config={"egress_ki": {"CODEX_ACCESS_TOKEN": "enc"}})
        heute = json.dumps({**AUTH, "tokens": {**AUTH["tokens"], "access_token": "neu.token.jwt"}})
        with patch.object(creds, "resolve", AsyncMock(return_value=(creds.SOURCE_TEAM, "codex", heute))):
            (freigabe,) = await api._ki_zugang(None, agent)
        self.assertEqual(freigabe.hosts, ["chatgpt.com"])
        self.assertEqual(freigabe.lesen(), "neu.token.jwt")
        self.assertEqual((freigabe.art, freigabe.id), (egress.KI, egress.KI_CODEX))


if __name__ == "__main__":
    unittest.main()
