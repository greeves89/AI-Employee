"""Nach dem OAuth-Login zurueck in die App — aber nur in die App.

28.09.2026: Microsoft verbindet man jetzt direkt aus dem Chat eines Agenten.
Damit man danach wieder dort landet, nimmt der Login ein Ziel an. Ein frei
waehlbares Ziel waere ein offener Umleiter (Login-Link, der danach auf eine
fremde Seite fuehrt). Erlaubt sind nur Pfade dieser App.
"""
import json
import unittest
from types import SimpleNamespace

from app.services.oauth_service import OAuthService, sicherer_ruecksprung


class RuecksprungTests(unittest.TestCase):
    def test_app_pfade_sind_erlaubt(self):
        for pfad in ("/agents/86a7ba07", "/agents/86a7ba07?tab=chat", "/integrations"):
            self.assertEqual(sicherer_ruecksprung(pfad), pfad, pfad)

    def test_fremde_ziele_werden_verworfen(self):
        for pfad in (
            "https://evil.example",
            "//evil.example/pfad",
            "/\\evil.example",
            "javascript:alert(1)",
            "evil.example",
            "/agents/../../x",
            "/agents/%2F%2Fevil.example",
            "/agents/x?next=https://evil.example",
            "/a b",
            "/" + "a" * 500,
            "",
            None,
        ):
            self.assertIsNone(sicherer_ruecksprung(pfad), repr(pfad))


class _Redis:
    def __init__(self):
        self.daten = {}

    async def get(self, key):
        return self.daten.get(key)


class ZustandTests(unittest.IsolatedAsyncioTestCase):
    async def test_gespeichertes_ziel_wird_beim_lesen_erneut_geprueft(self):
        """Tiefenverteidigung: auch ein manipulierter Zustand fuehrt nicht nach aussen."""
        redis = _Redis()
        dienst = OAuthService(db=None, redis=SimpleNamespace(client=redis.__class__()))
        dienst.redis.client.daten = {
            "oauth:state:gut": json.dumps({"provider": "microsoft", "ruecksprung": "/agents/a1"}),
            "oauth:state:boese": json.dumps({"provider": "microsoft", "ruecksprung": "//evil.example"}),
        }
        self.assertEqual(await dienst.ruecksprung_fuer("gut"), "/agents/a1")
        self.assertIsNone(await dienst.ruecksprung_fuer("boese"))
        self.assertIsNone(await dienst.ruecksprung_fuer("unbekannt"))




class CallbackMaskiertTests(unittest.IsolatedAsyncioTestCase):
    """Sicherheits-Review 28.09.2026: ``provider``/``error`` standen roh in der
    Rueckleitung — ``error=y&pwn=1`` schleuste einen zusaetzlichen Parameter ein."""

    async def test_fehler_und_provider_werden_maskiert(self):
        from app.api.integrations import oauth_callback

        antwort = await oauth_callback("x&pwn=1", code="c", state="s", error="y&admin=1", service=None)
        ziel = antwort.headers["location"]
        self.assertNotIn("&pwn=1", ziel)
        self.assertNotIn("&admin=1", ziel)
        self.assertIn("error=y%26admin%3D1", ziel)


if __name__ == "__main__":
    unittest.main()
