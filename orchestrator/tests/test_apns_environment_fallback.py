"""Testflug- und Xcode-Geraete muessen beide erreichbar sein.

Apple trennt Test- und Verkaufsumgebung strikt: Ein Geraete-Schluessel aus einem
Xcode-Build ist an der Verkaufsadresse ungueltig und umgekehrt. Beide Arten sind
gleichzeitig im Umlauf — auf dem Schreibtisch die Xcode-Version, beim Kunden die
aus dem Testflug. Von aussen ist der Unterschied nicht erkennbar, denn Apple
antwortet in beiden Faellen mit BadDeviceToken. Steht der Schalter falsch,
kommen die Meldungen der jeweils anderen Haelfte nie an, ohne Hinweis auf die
Ursache.

Deshalb wird bei genau diesem Fehler die andere Adresse ebenfalls versucht.
"""

import unittest
from unittest.mock import patch

from app.services.apns_service import APNsService


class _Antwort:
    def __init__(self, status, text=""):
        self.status_code = status
        self.text = text


class _Client:
    """Zeichnet auf, welche Adressen angesprochen wurden."""

    def __init__(self, antworten):
        self._antworten = antworten
        self.adressen = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None):
        self.adressen.append(url.split("/3/device/")[0])
        return self._antworten[len(self.adressen) - 1]


class _Settings:
    def __init__(self, sandbox):
        self.apns_auth_key = "x"
        self.apns_key_id = "k"
        self.apns_team_id = "t"
        self.apns_bundle_id = "b"
        self.apns_sandbox = sandbox


def _lauf(antworten, sandbox=True):
    client = _Client(antworten)
    with patch("app.services.apns_service.settings", _Settings(sandbox)), \
         patch.object(APNsService, "_provider_token", classmethod(lambda cls: "jwt")), \
         patch("app.services.apns_service.httpx.AsyncClient", lambda **kw: client):
        import asyncio
        ok = asyncio.run(APNsService.send("dev", "Titel", "Text"))
    return ok, client.adressen


class UmgebungsWechselTests(unittest.TestCase):
    def test_erfolg_beim_ersten_versuch_fragt_nur_eine_adresse(self):
        ok, adressen = _lauf([_Antwort(200)])
        self.assertTrue(ok)
        self.assertEqual(1, len(adressen))
        self.assertIn("sandbox", adressen[0])

    def test_bei_bad_device_token_wird_die_andere_adresse_versucht(self):
        """Der eigentliche Zweck: Testflug-Geraete trotz Schalter auf Test."""
        ok, adressen = _lauf([
            _Antwort(400, '{"reason":"BadDeviceToken"}'),
            _Antwort(200),
        ])
        self.assertTrue(ok)
        self.assertEqual(2, len(adressen))
        self.assertIn("sandbox", adressen[0])
        self.assertNotIn("sandbox", adressen[1])

    def test_produktivschalter_beginnt_bei_der_verkaufsadresse(self):
        ok, adressen = _lauf([_Antwort(200)], sandbox=False)
        self.assertTrue(ok)
        self.assertNotIn("sandbox", adressen[0])

    def test_anderer_fehler_wird_nicht_zweimal_versucht(self):
        """Ein abgelaufener Schluessel wird durch Wiederholen nicht besser."""
        ok, adressen = _lauf([_Antwort(403, '{"reason":"ExpiredProviderToken"}')])
        self.assertFalse(ok)
        self.assertEqual(1, len(adressen))

    def test_zweimal_ungueltig_meldet_misserfolg(self):
        ok, adressen = _lauf([
            _Antwort(400, '{"reason":"BadDeviceToken"}'),
            _Antwort(400, '{"reason":"BadDeviceToken"}'),
        ])
        self.assertFalse(ok)
        self.assertEqual(2, len(adressen))


if __name__ == "__main__":
    unittest.main()
