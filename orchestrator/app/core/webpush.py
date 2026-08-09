"""Web Push — Benachrichtigungen an Browser, auch wenn die Seite zu ist.

Die Plattform konnte bisher nur iOS-Geraete erreichen (APNs). Wer im Browser
arbeitet, sah eine Freigabe-Anfrage erst beim naechsten Hinsehen — bei einem Agenten,
der auf eine Freigabe wartet, heisst das im Zweifel stundenlanger Stillstand.

Umgesetzt ohne neue Abhaengigkeit: ``cryptography`` (fuer ECDH/HKDF/AES-GCM) und
``PyJWT`` sind ohnehin im Bild, und das Projekt schreibt Protokolldetails lieber selbst
— der Bedrock-Aufruf macht SigV4 genauso von Hand. ``pywebpush`` haette drei weitere
Pakete in die Lieferkette geholt fuer rund hundert Zeilen gut spezifizierten Code.

Zwei Spezifikationen stecken hier drin:

* **RFC 8292 (VAPID)** — wir weisen uns gegenueber dem Push-Dienst mit einem
  ES256-JWT aus, damit Google/Mozilla wissen, wer da sendet.
* **RFC 8291 + RFC 8188 (aes128gcm)** — der Inhalt wird fuer den Empfaenger
  verschluesselt. Der Push-Dienst leitet nur weiter und kann NICHT mitlesen; das ist
  der Grund, warum das ueberhaupt datenschutzkonform einsetzbar ist.
"""

import base64
import logging
import os
import time
from dataclasses import dataclass

import httpx
import jwt
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

logger = logging.getLogger(__name__)

# Der Push-Dienst darf die Nachricht so lange vorhalten, falls das Geraet offline ist.
DEFAULT_TTL = 24 * 3600
# Aus RFC 8188. Groesser als jede Meldung, die wir schicken — es gibt also nie mehr
# als einen Datensatz und wir brauchen keine Zerlegung.
RECORD_SIZE = 4096
_MAX_PAYLOAD = RECORD_SIZE - 17   # 16 Byte GCM-Pruefsumme + 1 Byte Trennzeichen


def b64u_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64u_decode(data: str) -> bytes:
    """Base64url ohne Fuellzeichen — so liefern es die Browser."""
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


@dataclass(frozen=True)
class VapidKeys:
    """Das Schluesselpaar, mit dem sich dieser Server beim Push-Dienst ausweist.

    Der oeffentliche Teil geht an den Browser (``applicationServerKey``), der private
    bleibt hier. Wird er ausgetauscht, sind ALLE bestehenden Anmeldungen wertlos —
    deshalb wird er einmal erzeugt und dann verschluesselt in den Einstellungen
    aufbewahrt, nicht bei jedem Start neu gewuerfelt.
    """

    private_pem: str
    public_b64: str

    @classmethod
    def generate(cls) -> "VapidKeys":
        key = ec.generate_private_key(ec.SECP256R1())
        pem = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("ascii")
        return cls(private_pem=pem, public_b64=cls._public_b64(key))

    @staticmethod
    def _public_b64(key: ec.EllipticCurvePrivateKey) -> str:
        raw = key.public_key().public_bytes(
            encoding=serialization.Encoding.X962,
            format=serialization.PublicFormat.UncompressedPoint,
        )
        return b64u_encode(raw)

    def private_key(self) -> ec.EllipticCurvePrivateKey:
        return serialization.load_pem_private_key(self.private_pem.encode("ascii"), password=None)


def vapid_header(keys: VapidKeys, endpoint: str, subject: str) -> str:
    """Der ``Authorization``-Wert fuer einen Push-Dienst.

    ``aud`` ist der Ursprung des Endpunkts (also z. B. ``https://fcm.googleapis.com``),
    NICHT der volle Pfad — mit dem vollen Pfad lehnen die Dienste ab.
    """
    from urllib.parse import urlparse

    parsed = urlparse(endpoint)
    token = jwt.encode(
        {
            "aud": f"{parsed.scheme}://{parsed.netloc}",
            "exp": int(time.time()) + 12 * 3600,
            "sub": subject,
        },
        keys.private_key(),
        algorithm="ES256",
    )
    return f"vapid t={token}, k={keys.public_b64}"


def encrypt_payload(plaintext: bytes, p256dh: str, auth: str) -> bytes:
    """Inhalt fuer genau diesen Empfaenger verschluesseln (RFC 8291, aes128gcm).

    Der Push-Dienst sieht nur einen Blob — er kann weder mitlesen noch veraendern.
    """
    if len(plaintext) > _MAX_PAYLOAD:
        plaintext = plaintext[:_MAX_PAYLOAD]

    ua_public_raw = b64u_decode(p256dh)
    auth_secret = b64u_decode(auth)

    ua_public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public_raw)
    as_private = ec.generate_private_key(ec.SECP256R1())
    as_public_raw = as_private.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )

    shared = as_private.exchange(ec.ECDH(), ua_public)

    # Erst das gemeinsame Geheimnis mit dem Anmelde-Geheimnis des Browsers mischen,
    # dann daraus Schluessel und Nonce ableiten. Die Info-Zeichenketten sind aus der
    # Spezifikation und muessen Byte fuer Byte stimmen.
    ikm = HKDF(
        algorithm=hashes.SHA256(), length=32, salt=auth_secret,
        info=b"WebPush: info\x00" + ua_public_raw + as_public_raw,
    ).derive(shared)

    salt = os.urandom(16)
    cek = HKDF(
        algorithm=hashes.SHA256(), length=16, salt=salt,
        info=b"Content-Encoding: aes128gcm\x00",
    ).derive(ikm)
    nonce = HKDF(
        algorithm=hashes.SHA256(), length=12, salt=salt,
        info=b"Content-Encoding: nonce\x00",
    ).derive(ikm)

    # 0x02 ist das Trennzeichen fuer den LETZTEN Datensatz. Mit 0x01 wartet der
    # Empfaenger auf weitere und zeigt nichts an.
    ciphertext = AESGCM(cek).encrypt(nonce, plaintext + b"\x02", None)

    header = salt + RECORD_SIZE.to_bytes(4, "big") + len(as_public_raw).to_bytes(1, "big") + as_public_raw
    return header + ciphertext


async def send(
    *, endpoint: str, p256dh: str, auth: str, payload: bytes,
    keys: VapidKeys, subject: str, ttl: int = DEFAULT_TTL,
) -> int:
    """Eine Meldung zustellen. Gibt den HTTP-Status zurueck.

    404/410 heisst: diese Anmeldung ist erloschen (Browser deinstalliert, Rechte
    entzogen). Der Aufrufer soll sie dann loeschen, sonst waechst die Tabelle mit
    Karteileichen, die bei jeder Meldung erneut angefragt werden.
    """
    body = encrypt_payload(payload, p256dh, auth)
    headers = {
        "Authorization": vapid_header(keys, endpoint, subject),
        "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream",
        "TTL": str(ttl),
        "Urgency": "normal",
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(endpoint, content=body, headers=headers)
    return resp.status_code


def is_gone(status: int) -> bool:
    """Anmeldung endgueltig weg — aufraeumen statt weiter anzufragen."""
    return status in (404, 410)
