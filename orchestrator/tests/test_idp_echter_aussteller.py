"""MCP-Anmeldung über einen IdP gegen einen ECHTEN Aussteller (Plan Sicherheit 2026-11, Punkt 4).

Die übrigen Tests (``test_mcp_idp_oauth``) ersetzen den Abruf beim IdP. Hier läuft der
ganze Weg wie im Betrieb: SSRF-Sperre mit echter DNS-Auflösung, Abruf über die feste
Verbindung (``core/feste_verbindung``), OpenID-Discovery, JWKS, RS256-Prüfung.

Aussteller ist GitHub Actions (``token.actions.githubusercontent.com``): Der CI-Schritt
``idp-echter-aussteller`` darf dort ein Token mit selbst gewähltem Empfänger holen.
Entra lässt sich ohne Microsoft-Konto nicht so prüfen; der Weg ist derselbe, nur die
Entra-eigenen Angaben (``azp``, ``scp``, ``oid``) fehlen im GitHub-Token. Deren Prüfung
deckt ``test_mcp_idp_oauth`` ab.

Ohne ``ACTIONS_ID_TOKEN_REQUEST_URL`` (lokal, PRs aus Forks) wird übersprungen.
"""

import json
import os
import unittest
import urllib.request
from unittest.mock import patch

from app.config import settings
from app.core import mcp_idp_auth as m

AUSSTELLER = "https://token.actions.githubusercontent.com"
EMPFAENGER = "ai-employee-mcp-probe"

_URL = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL", "")
_ANFRAGE = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "")


def _token(empfaenger: str = EMPFAENGER) -> str:
    anfrage = urllib.request.Request(f"{_URL}&audience={empfaenger}",
                                     headers={"Authorization": f"Bearer {_ANFRAGE}"})
    with urllib.request.urlopen(anfrage, timeout=20) as antwort:
        return json.load(antwort)["value"]


def _einstellung(**mehr):
    werte = dict(mcp_idp_oauth_enabled=True, mcp_idp_issuer=AUSSTELLER, mcp_idp_audience=EMPFAENGER,
                 mcp_idp_jwks_uri="", mcp_idp_required_scope="", mcp_idp_allowed_clients="")
    werte.update(mehr)
    return patch.multiple(settings, **werte)


def _ohne_entra_angaben():
    """GitHub-Tokens nennen weder aufrufende Anwendung noch Berechtigung. ``konfig``
    verlangt eins von beiden (sonst nähme jede App den Weg); für die Signaturprüfung
    hier gezielt ohne."""
    return patch.object(m, "konfig", lambda: m.IdpKonfig(
        issuer=AUSSTELLER, audiences=(EMPFAENGER,), jwks_uri="", required_scope="", allowed_clients=()))


@unittest.skipUnless(_URL and _ANFRAGE, "nur im CI-Schritt mit id-token: write")
class EchterAussteller(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        m.cache_leeren()

    async def test_echtes_token_wird_angenommen(self):
        token = _token()
        with _ohne_entra_angaben():
            angaben = await m.pruefe_token(token)
        self.assertEqual(angaben["iss"], AUSSTELLER)
        self.assertEqual(angaben["aud"], EMPFAENGER)
        self.assertEqual(angaben.get("repository"), os.environ.get("GITHUB_REPOSITORY"))
        # Discovery lief wirklich und blieb auf dem Host des Ausstellers.
        self.assertTrue(m._discovery[AUSSTELLER].startswith(AUSSTELLER + "/"))

    async def test_veraenderte_signatur_abgewiesen(self):
        kopf, inhalt, signatur = _token().split(".")
        falsch = signatur[:-4] + ("AAAA" if not signatur.endswith("AAAA") else "BBBB")
        with _ohne_entra_angaben(), self.assertRaises(m.IdpTokenFehler) as f:
            await m.pruefe_token(f"{kopf}.{inhalt}.{falsch}")
        self.assertEqual(f.exception.grund, "Token ungültig")

    async def test_anderer_empfaenger_abgewiesen(self):
        token = _token("eine-andere-ressource")
        with _ohne_entra_angaben(), self.assertRaises(m.IdpTokenFehler) as f:
            await m.pruefe_token(token)
        self.assertEqual(f.exception.grund, "Token für eine andere Ressource")

    async def test_falscher_aussteller_abgewiesen(self):
        token = _token()
        with patch.object(m, "konfig", lambda: m.IdpKonfig(
                issuer="https://login.microsoftonline.com/00000000-0000-0000-0000-000000000000/v2.0",
                audiences=(EMPFAENGER,), jwks_uri=AUSSTELLER + "/.well-known/jwks",
                required_scope="", allowed_clients=())), self.assertRaises(m.IdpTokenFehler) as f:
            await m.pruefe_token(token)
        self.assertEqual(f.exception.grund, "Falscher Aussteller")

    async def test_echte_einrichtung_verlangt_die_anwendung(self):
        """Mit der Einrichtung wie im Betrieb (erlaubte Anwendung gesetzt): die Signatur
        ist gültig, aber ohne ``azp`` kommt das Token nicht durch."""
        token = _token()
        with _einstellung(mcp_idp_allowed_clients="11111111-1111-1111-1111-111111111111"), \
                self.assertRaises(m.IdpTokenFehler) as f:
            await m.pruefe_token(token)
        self.assertEqual(f.exception.grund, "Aufrufende Anwendung nicht genannt")

    async def test_ohne_person_kein_konto(self):
        """Ein gültiges Token ohne Objekt-ID führt zu keinem Konto (403)."""
        token = _token()
        with _ohne_entra_angaben():
            angaben = await m.pruefe_token(token)
            with self.assertRaises(m.IdpTokenFehler) as f:
                await m.nutzer_zum_token(None, angaben)
        self.assertEqual(f.exception.status, 403)


if __name__ == "__main__":
    unittest.main()
