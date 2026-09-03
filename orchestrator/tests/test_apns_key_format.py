"""Der APNs-Schluessel muss in beiden Schreibweisen funktionieren.

Apple gibt den Schluessel als mehrzeiligen PEM-Block heraus. Die Konfiguration
kennt aber nur eine Zeile pro Wert, also traegt man ihn in der Praxis mit \\n
statt echter Zeilenumbrueche ein. Kommt dann die Zeichenfolge Backslash-n im
Code an, scheitert das Signieren mit einer Meldung ueber ein ungueltiges
Schluesselformat — und der Push bleibt aus, ohne dass jemand die Ursache sieht.

Geprueft wird deshalb gegen einen echten Schluessel und mit echtem Signieren:
Aus beiden Schreibweisen muss ein gueltiges, mit demselben Schluessel
pruefbares Token entstehen.
"""

import unittest
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.services.apns_service import APNsService


def _schluesselpaar():
    priv = ec.generate_private_key(ec.SECP256R1())
    pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    pub = priv.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return pem, pub


class _Settings:
    def __init__(self, key):
        self.apns_auth_key = key
        self.apns_key_id = "KEYID12345"
        self.apns_team_id = "TEAMID6789"


class SchluesselformatTests(unittest.TestCase):
    def setUp(self):
        # Der Zwischenspeicher wuerde sonst das Ergebnis des vorigen Falls
        # zurueckgeben und den Test wertlos machen.
        APNsService._token = None
        APNsService._token_at = 0.0

    def _signieren(self, hinterlegt):
        with patch("app.services.apns_service.settings", _Settings(hinterlegt)):
            return APNsService._provider_token()

    def test_echte_zeilenumbrueche(self):
        pem, pub = _schluesselpaar()
        token = self._signieren(pem)
        daten = jwt.decode(token, pub, algorithms=["ES256"])
        self.assertEqual("TEAMID6789", daten["iss"])

    def test_maskierte_zeilenumbrueche(self):
        """Der Fall, der in der Praxis vorkommt — und bisher scheiterte."""
        pem, pub = _schluesselpaar()
        einzeilig = pem.replace("\n", "\\n")
        self.assertNotIn("\n", einzeilig)   # wirklich einzeilig
        token = self._signieren(einzeilig)
        daten = jwt.decode(token, pub, algorithms=["ES256"])
        self.assertEqual("TEAMID6789", daten["iss"])
        self.assertEqual("KEYID12345", jwt.get_unverified_header(token)["kid"])

    def test_umgebende_leerzeichen_stoeren_nicht(self):
        pem, pub = _schluesselpaar()
        token = self._signieren("\n  " + pem + "  \n")
        jwt.decode(token, pub, algorithms=["ES256"])


if __name__ == "__main__":
    unittest.main()
