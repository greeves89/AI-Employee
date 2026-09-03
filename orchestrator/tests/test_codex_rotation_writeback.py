"""Ein zurueckgemeldeter Codex-Zugang darf den gespeicherten nie verschlechtern.

Der ChatGPT-Refresh-Token ist einmalig. Erneuert die CLI im Container den Zugang,
muss die neue Fassung zurueck in die Datenbank — sonst spielt der naechste Start
die verbrauchte ein und der Anbieter antwortet dauerhaft mit
``refresh_token_reused`` (Issue #646).

Der Rueckweg ist damit aber auch ein Schreibzugriff auf die Zugangsdaten eines
Nutzers. Geprueft wird deshalb genau das, was den Zugang stillschweigend
zerstoeren wuerde: eine unbrauchbare Form, ein **fremdes** Konto und eine
**aeltere** Fassung. Die Pruefung ist eine reine Funktion, damit diese Grenzfaelle
ohne Datenbank belegbar sind.
"""

import base64
import json
import unittest

from app.api.agent_codex_auth import rotation_rejection


def _access_token(exp: int) -> str:
    """Ein JWT-aehnlicher Token, aus dem nur das ``exp`` gelesen wird."""
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
    return f"header.{payload}.signature"


def _auth(exp: int = 2_000_000_000, account: str = "acct-1", refresh: str = "r1") -> dict:
    return {
        "tokens": {
            "access_token": _access_token(exp),
            "refresh_token": refresh,
            "account_id": account,
        }
    }


class RotationRejectionTests(unittest.TestCase):
    def test_gute_erneuerung_wird_angenommen(self):
        stored = _auth(exp=1_000_000_000, refresh="alt")
        incoming = _auth(exp=1_000_003_600, refresh="neu")
        self.assertIsNone(rotation_rejection(stored, incoming))

    def test_erster_zugang_ohne_vergleichswert_wird_angenommen(self):
        # Gespeichertes unlesbar/leer: dann ist jede brauchbare Fassung besser.
        self.assertIsNone(rotation_rejection({}, _auth()))

    def test_ohne_tokens_abschnitt_abgelehnt(self):
        self.assertIsNotNone(rotation_rejection(_auth(), {"foo": "bar"}))
        self.assertIsNotNone(rotation_rejection(_auth(), {"tokens": "nicht-dict"}))

    def test_leerer_refresh_token_abgelehnt(self):
        incoming = _auth()
        incoming["tokens"]["refresh_token"] = "   "
        self.assertIsNotNone(rotation_rejection(_auth(), incoming))

    def test_fehlender_access_token_abgelehnt(self):
        incoming = _auth()
        del incoming["tokens"]["access_token"]
        self.assertIsNotNone(rotation_rejection(_auth(), incoming))

    def test_unplausibel_grosse_nutzlast_abgelehnt(self):
        """Sonst landet beliebiger Ballast dauerhaft in der Zugangszeile des Nutzers."""
        incoming = _auth()
        incoming["ballast"] = "x" * 100_000
        self.assertEqual(rotation_rejection(_auth(), incoming), "auth.json unplausibel gross")

    def test_fremdes_konto_abgelehnt(self):
        """Sonst koennte ein Agent den Zugang des Besitzers gegen einen anderen tauschen."""
        stored = _auth(account="acct-1")
        incoming = _auth(account="acct-fremd", refresh="neu")
        self.assertEqual(
            rotation_rejection(stored, incoming), "Zugang gehoert zu einem anderen Konto"
        )

    def test_aelterer_token_abgelehnt(self):
        """Ein Nachzuegler darf eine bereits neuere Fassung nicht ueberschreiben."""
        stored = _auth(exp=1_000_003_600, refresh="neu")
        incoming = _auth(exp=1_000_000_000, refresh="alt")
        self.assertEqual(
            rotation_rejection(stored, incoming), "eingehender Token ist aelter als der gespeicherte"
        )

    def test_gleiches_ablaufdatum_ist_kein_rueckschritt(self):
        stored = _auth(exp=1_000_000_000, refresh="alt")
        incoming = _auth(exp=1_000_000_000, refresh="neu")
        self.assertIsNone(rotation_rejection(stored, incoming))


if __name__ == "__main__":
    unittest.main()
