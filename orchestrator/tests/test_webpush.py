"""Web Push — Browser-Meldungen, auch wenn die Seite zu ist.

Die Plattform konnte bisher nur iPhones erreichen. Wartet ein Agent auf eine Freigabe,
steht er bis zur Antwort still — ohne Push sieht man das erst beim naechsten Hinsehen,
bei einem naechtlichen Lauf also am naechsten Morgen.

Der heikle Teil ist die Verschluesselung (RFC 8291/8188): stimmt dort ein Byte nicht,
lehnt der Browser die Meldung stillschweigend ab — kein Fehler, keine Anzeige, nichts.
Deshalb wird hier gegen die ECHTE Gegenrichtung geprueft: verschluesseln, dann als
Empfaenger wieder entschluesseln.
"""

import os
import unittest
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core import webpush

REPO = Path(__file__).resolve().parents[2]
ORCH = REPO / "orchestrator"


def _receiver():
    """Was sonst der Browser ist: Schluesselpaar + Anmelde-Geheimnis."""
    priv = ec.generate_private_key(ec.SECP256R1())
    pub_raw = priv.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    auth = os.urandom(16)
    return priv, pub_raw, auth


def _decrypt(blob: bytes, priv, auth_secret: bytes) -> bytes:
    """Entschluesseln wie ein Browser (RFC 8188 aes128gcm)."""
    salt = blob[:16]
    idlen = blob[20]
    as_pub_raw = blob[21:21 + idlen]
    ciphertext = blob[21 + idlen:]

    as_pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_pub_raw)
    ua_pub_raw = priv.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    shared = priv.exchange(ec.ECDH(), as_pub)
    ikm = HKDF(algorithm=hashes.SHA256(), length=32, salt=auth_secret,
               info=b"WebPush: info\x00" + ua_pub_raw + as_pub_raw).derive(shared)
    cek = HKDF(algorithm=hashes.SHA256(), length=16, salt=salt,
               info=b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(algorithm=hashes.SHA256(), length=12, salt=salt,
                 info=b"Content-Encoding: nonce\x00").derive(ikm)
    return AESGCM(cek).decrypt(nonce, ciphertext, None)


class EncryptionTests(unittest.TestCase):
    def test_the_receiver_can_read_it(self):
        priv, pub_raw, auth = _receiver()
        msg = b'{"title":"Freigabe noetig","body":"Agent wartet"}'
        blob = webpush.encrypt_payload(msg, webpush.b64u_encode(pub_raw),
                                       webpush.b64u_encode(auth))
        out = _decrypt(blob, priv, auth)
        self.assertEqual(out[:-1], msg)

    def test_last_record_delimiter(self):
        """0x02 heisst 'letzter Datensatz'. Mit 0x01 wartet der Browser auf mehr
        und zeigt gar nichts an — ein Fehler, der voellig lautlos waere."""
        priv, pub_raw, auth = _receiver()
        blob = webpush.encrypt_payload(b"x", webpush.b64u_encode(pub_raw),
                                       webpush.b64u_encode(auth))
        self.assertEqual(_decrypt(blob, priv, auth)[-1], 0x02)

    def test_header_layout(self):
        """salt(16) + Datensatzgroesse(4) + Schluessellaenge(1) + Schluessel(65)."""
        _, pub_raw, auth = _receiver()
        blob = webpush.encrypt_payload(b"x", webpush.b64u_encode(pub_raw),
                                       webpush.b64u_encode(auth))
        self.assertEqual(int.from_bytes(blob[16:20], "big"), webpush.RECORD_SIZE)
        self.assertEqual(blob[20], 65)

    def test_a_wrong_key_cannot_read_it(self):
        """Der Push-Dienst leitet nur weiter — mitlesen darf er nicht koennen."""
        _, pub_raw, auth = _receiver()
        blob = webpush.encrypt_payload(b"geheim", webpush.b64u_encode(pub_raw),
                                       webpush.b64u_encode(auth))
        other_priv, _, _ = _receiver()
        with self.assertRaises(Exception):
            _decrypt(blob, other_priv, auth)

    def test_every_message_uses_a_fresh_salt(self):
        """Gleiche Nachricht, gleicher Empfaenger — trotzdem anderer Geheimtext."""
        _, pub_raw, auth = _receiver()
        p, a = webpush.b64u_encode(pub_raw), webpush.b64u_encode(auth)
        self.assertNotEqual(webpush.encrypt_payload(b"x", p, a),
                            webpush.encrypt_payload(b"x", p, a))

    def test_oversized_payload_is_cut_not_crashed(self):
        priv, pub_raw, auth = _receiver()
        blob = webpush.encrypt_payload(b"y" * 9000, webpush.b64u_encode(pub_raw),
                                       webpush.b64u_encode(auth))
        self.assertLessEqual(len(_decrypt(blob, priv, auth)), webpush.RECORD_SIZE)


class Base64Tests(unittest.TestCase):
    def test_round_trip_without_padding(self):
        """Browser liefern Base64url OHNE Fuellzeichen — daran scheitert naives b64decode."""
        for n in (1, 2, 3, 16, 65):
            data = os.urandom(n)
            with self.subTest(n=n):
                encoded = webpush.b64u_encode(data)
                self.assertNotIn("=", encoded)
                self.assertEqual(webpush.b64u_decode(encoded), data)


class VapidTests(unittest.TestCase):
    def test_public_key_is_an_uncompressed_point(self):
        keys = webpush.VapidKeys.generate()
        self.assertEqual(len(webpush.b64u_decode(keys.public_b64)), 65)
        self.assertEqual(webpush.b64u_decode(keys.public_b64)[0], 0x04)

    def test_header_audience_is_the_origin_only(self):
        """Mit dem vollen Pfad als `aud` lehnen die Push-Dienste ab."""
        import jwt as pyjwt

        keys = webpush.VapidKeys.generate()
        header = webpush.vapid_header(keys, "https://fcm.googleapis.com/fcm/send/abc123",
                                      "mailto:a@b.de")
        self.assertTrue(header.startswith("vapid t="))
        token = header.split("t=")[1].split(",")[0]
        claims = pyjwt.decode(token, keys.private_key().public_key(), algorithms=["ES256"],
                              options={"verify_aud": False})
        self.assertEqual(claims["aud"], "https://fcm.googleapis.com")
        self.assertEqual(claims["sub"], "mailto:a@b.de")

    def test_keys_survive_a_round_trip_through_storage(self):
        """Sie werden als PEM abgelegt — daraus muss wieder derselbe Schluessel werden,
        sonst waeren nach einem Neustart alle Anmeldungen wertlos."""
        keys = webpush.VapidKeys.generate()
        again = webpush.VapidKeys(private_pem=keys.private_pem, public_b64=keys.public_b64)
        self.assertEqual(
            again.private_key().private_numbers().private_value,
            keys.private_key().private_numbers().private_value,
        )


class GoneTests(unittest.TestCase):
    def test_403_404_410_mean_gone(self):
        """Apples Push-Dienst meldet abgelaufene Anmeldungen oft als 403, nicht 410 —
        ohne das bleibt die tote Anmeldung fuer immer in der Tabelle."""
        self.assertTrue(webpush.is_gone(403))
        self.assertTrue(webpush.is_gone(404))
        self.assertTrue(webpush.is_gone(410))
        for status in (201, 429, 500, 503):
            with self.subTest(status=status):
                self.assertFalse(webpush.is_gone(status),
                                 "Voruebergehende Fehler duerfen keine Anmeldung loeschen.")


class OneFanOutPointTests(unittest.TestCase):
    """Der eigentliche Punkt: Web Push darf kein zweiter Meldeweg neben APNs sein."""

    CALLERS = ("app/main.py", "app/core/task_router.py",
               "app/api/approvals.py", "app/api/notifications.py")

    def test_all_callers_use_the_neutral_point(self):
        for rel in self.CALLERS:
            src = (ORCH / rel).read_text()
            with self.subTest(file=rel):
                self.assertIn("from app.core.push import push_to_user", src)
                self.assertNotIn("from app.services.apns_service import push_to_user", src,
                                 f"{rel} meldet nur an iPhones.")

    def test_apns_module_no_longer_fans_out(self):
        """Bliebe die alte Funktion bestehen, wuerde der naechste Aufrufer sie greifen
        und seine Meldung erreichte lautlos nur die Haelfte der Geraete."""
        src = (ORCH / "app/services/apns_service.py").read_text()
        self.assertNotIn("async def push_to_user", src)

    def test_fan_out_covers_both_channels(self):
        src = (ORCH / "app/core/push.py").read_text()
        self.assertIn("_push_apns", src)
        self.assertIn("_push_web", src)

    def test_dead_subscriptions_are_cleaned_up(self):
        src = (ORCH / "app/core/push.py").read_text()
        self.assertIn("is_gone", src)
        self.assertIn("db.delete", src)


class PwaTests(unittest.TestCase):
    def test_manifest_exists_and_is_standalone(self):
        import json

        manifest = json.loads((REPO / "frontend/public/manifest.json").read_text())
        self.assertEqual(manifest["display"], "standalone")
        self.assertTrue(manifest["icons"])

    def test_service_worker_handles_push_and_click(self):
        src = (REPO / "frontend/public/sw.js").read_text()
        self.assertIn('addEventListener("push"', src)
        self.assertIn('addEventListener("notificationclick"', src)

    def test_service_worker_is_registered_on_load(self):
        """Nur beim Umschalten zu registrieren, macht die App nicht installierbar."""
        src = (REPO / "frontend/src/app/layout.tsx").read_text()
        self.assertIn("PwaRegistrar", src)
        self.assertIn('manifest: "/manifest.json"', src)

    def test_settings_offer_the_switch(self):
        src = (REPO / "frontend/src/app/settings/view.tsx").read_text()
        self.assertIn("PushToggle", src)

    def test_endpoints_exist(self):
        src = (ORCH / "app/api/notifications.py").read_text()
        for route in ('"/push/public-key"', '"/push/subscribe"', '"/push/unsubscribe"'):
            with self.subTest(route=route):
                self.assertIn(route, src)

    def test_unsubscribe_is_scoped_to_the_own_user(self):
        """Sonst koennte jeder Eingeloggte fremde Geraete stummschalten."""
        src = (ORCH / "app/api/notifications.py").read_text()
        block = src.split("async def webpush_unsubscribe")[1].split("\n# ---")[0]
        self.assertIn("PushSubscription.user_id == str(user.id)", block)


if __name__ == "__main__":
    unittest.main()
