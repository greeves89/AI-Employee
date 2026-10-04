"""Zwei-Faktor-Anmeldung per TOTP (RFC 6238) für Passwort-Konten (#915).

Befund im Markttest: wer das Passwort eines Kontos kannte, war drin. SSO-Konten
haben ihren zweiten Faktor beim Identitätsanbieter — Passwort-Konten hatten keinen.

Was hier liegt, ist die EINE Stelle für alles, was den zweiten Faktor ausmacht:

- ``naechster_schritt``: die Anmeldeentscheidung (Zugang, Code, Einrichtung,
  Ablehnung) als reine Funktion — getestet mit MC/DC.
- TOTP über ``cryptography`` (ohnehin Abhängigkeit, kein neues Paket):
  30 Sekunden, 6 Stellen, ±1 Zeitfenster.
- Das Geheimnis liegt Fernet-verschlüsselt (``core/encryption``), nie im Klartext.
- Wiederherstellungscodes nur als SHA-256-Hash; jeder gilt genau einmal.
- Das Zwischen-Token nach korrektem Passwort (``mfa_pending``) ist mit einem
  ABGELEITETEN Schlüssel signiert und trägt eine eigene Zielgruppe. Der normale
  ``decode_token`` lehnt es deshalb schon an der Signatur ab — auch an Stellen,
  die den Token-Typ nicht selbst prüfen.
- Redis: ein Zeitschritt gilt je Konto nur einmal (Replay-Schutz), nach
  ``MAX_FEHLVERSUCHE`` falschen Codes ist das Konto ``SPERRE_SEKUNDEN`` gesperrt.
  Ohne Redis wird NICHT durchgewunken (fail-closed).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum
from urllib.parse import quote

import jwt
from cryptography.hazmat.primitives.hashes import SHA1
from cryptography.hazmat.primitives.twofactor.totp import TOTP

from app.config import settings

PERIODE = 30
STELLEN = 6
TOLERANZ = 1  # ± Zeitfenster
AUSSTELLER = "AI Employee"

PENDING_GUELTIG = timedelta(minutes=10)  # Zeit für Code bzw. QR-Scan bei der Pflicht-Einrichtung
TYP_PENDING = "mfa_pending"
_PENDING_AUD = "ai-employee:mfa"
ZWECK_CODE = "code"
ZWECK_EINRICHTEN = "einrichten"

MAX_FEHLVERSUCHE = 5
SPERRE_SEKUNDEN = 15 * 60

ANZAHL_WIEDERHERSTELLUNGSCODES = 10
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # ohne 0/O, 1/I — abtippbar
_CODE_LAENGE = 12  # 60 Bit

#: Plattform-Einstellung „Zwei-Faktor für alle Passwort-Konten erzwingen“.
EINSTELLUNG_PFLICHT = "require_mfa_for_password_accounts"


class Schritt(str, Enum):
    ABGELEHNT = "abgelehnt"
    ZUGANG = "zugang"
    CODE = "code"
    EINRICHTEN = "einrichten"


class ZweiFaktorGesperrt(Exception):
    def __init__(self, sekunden: int):
        super().__init__(f"gesperrt für {sekunden} s")
        self.sekunden = sekunden


class ZweiFaktorNichtVerfuegbar(Exception):
    """Redis fehlt — ohne Replay-Schutz und Sperre wird nicht geprüft."""


# --- Entscheidung ---------------------------------------------------------


def naechster_schritt(*, sso_anmeldung: bool, passwort_ok: bool, mfa_aktiv: bool,
                      erzwungen: bool) -> Schritt:
    """Was nach dem ersten Faktor passiert.

    SSO-Anmeldungen sind ausgenommen: den zweiten Faktor verantwortet dort der
    Identitätsanbieter. Für den PASSWORT-Weg zählt dagegen nicht, ob das Konto
    zusätzlich mit SSO verknüpft ist — sonst wäre das Verknüpfen ein Umweg um
    die Pflicht. Ein falsches Passwort verrät nie, ob Zwei-Faktor aktiv ist.
    """
    if sso_anmeldung:
        return Schritt.ZUGANG
    if not passwort_ok:
        return Schritt.ABGELEHNT
    if mfa_aktiv:
        return Schritt.CODE
    if erzwungen:
        return Schritt.EINRICHTEN
    return Schritt.ZUGANG


def mfa_aktiv(user) -> bool:
    return bool(getattr(user, "mfa_enabled_at", None) and getattr(user, "totp_secret_encrypted", None))


def pflicht_aktiv() -> bool:
    return bool(getattr(settings, EINSTELLUNG_PFLICHT, False))


def pflicht_fuer(user) -> bool:
    """Gilt die Pflicht für dieses Konto? Nur für Konten mit Passwort."""
    return pflicht_aktiv() and bool(getattr(user, "password_hash", None))


# --- TOTP -----------------------------------------------------------------


def neues_geheimnis() -> str:
    """160 Bit, Base32 — so, wie Authenticator-Apps es erwarten."""
    return base64.b32encode(os.urandom(20)).decode("ascii")


def _totp(geheimnis: str) -> TOTP:
    schluessel = base64.b32decode(geheimnis.upper() + "=" * (-len(geheimnis) % 8))
    return TOTP(schluessel, STELLEN, SHA1(), PERIODE, enforce_key_length=False)


def code_generieren(geheimnis: str, zeitpunkt: float) -> str:
    """Code zu einem Zeitpunkt — für Tests und die Abnahme, nie für Antworten."""
    return _totp(geheimnis).generate(int(zeitpunkt)).decode("ascii")


def passender_zeitschritt(geheimnis: str, code: str, zeitpunkt: float | None = None) -> int | None:
    """Zeitschritt, zu dem ``code`` passt (±``TOLERANZ``), sonst ``None``."""
    code = (code or "").strip().replace(" ", "")
    if len(code) != STELLEN or not code.isdigit():
        return None
    jetzt = int(time.time() if zeitpunkt is None else zeitpunkt)
    totp = _totp(geheimnis)
    treffer = None
    # Alle Fenster vergleichen, nicht beim ersten Treffer aussteigen — gleich lange Prüfung.
    for versatz in range(-TOLERANZ, TOLERANZ + 1):
        t = jetzt + versatz * PERIODE
        if hmac.compare_digest(totp.generate(t), code.encode("ascii")):
            treffer = t // PERIODE
    return treffer


def otpauth_uri(geheimnis: str, email: str, aussteller: str = AUSSTELLER) -> str:
    """``otpauth://``-Adresse für den QR-Code der Authenticator-App."""
    label = quote(f"{aussteller}:{email}", safe=":@")
    return (f"otpauth://totp/{label}?secret={geheimnis}&issuer={quote(aussteller)}"
            f"&algorithm=SHA1&digits={STELLEN}&period={PERIODE}")


def geheimnis_verschluesseln(geheimnis: str) -> str:
    from app.core.encryption import encrypt_token

    return encrypt_token(geheimnis)


def geheimnis_entschluesseln(user) -> str | None:
    from app.core.encryption import decrypt_token

    roh = getattr(user, "totp_secret_encrypted", None)
    if not roh:
        return None
    try:
        return decrypt_token(roh)
    except ValueError:
        return None


# --- Wiederherstellungscodes ---------------------------------------------


def _normalisieren(code: str) -> str:
    return "".join(ch for ch in (code or "").upper() if ch.isalnum())


def _code_hash(code: str) -> str:
    return hashlib.sha256(_normalisieren(code).encode("ascii", "ignore")).hexdigest()


def wiederherstellungscodes_erzeugen() -> tuple[list[str], str]:
    """(Klartext für die einmalige Anzeige, JSON-Liste der Hashes für die Ablage)."""
    klartext = []
    for _ in range(ANZAHL_WIEDERHERSTELLUNGSCODES):
        roh = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LAENGE))
        klartext.append("-".join(roh[i:i + 4] for i in range(0, _CODE_LAENGE, 4)))
    return klartext, json.dumps([_code_hash(c) for c in klartext])


def _hashes(user) -> list[str]:
    try:
        werte = json.loads(getattr(user, "mfa_recovery_codes", None) or "[]")
    except (TypeError, ValueError):
        return []
    return [w for w in werte if isinstance(w, str)]


def verbleibende_codes(user) -> int:
    return len(_hashes(user))


def ist_wiederherstellungscode(code: str) -> bool:
    return len(_normalisieren(code)) == _CODE_LAENGE


def wiederherstellungscode_einloesen(user, code: str) -> bool:
    """Code verbrauchen. Ändert ``user`` — der Aufrufer schreibt fest."""
    if not ist_wiederherstellungscode(code):
        return False
    gesucht = _code_hash(code)
    rest, gefunden = [], False
    for h in _hashes(user):
        if not gefunden and hmac.compare_digest(h, gesucht):
            gefunden = True
            continue
        rest.append(h)
    if gefunden:
        user.mfa_recovery_codes = json.dumps(rest)
    return gefunden


# --- Zwischen-Token nach dem Passwort ------------------------------------


def _pending_schluessel() -> bytes:
    # Abgeleitet, nicht derselbe: ein Zugangstoken-Dekoder kann dieses Token nie annehmen.
    return hmac.new(settings.api_secret_key.encode(), b"ai-employee:mfa-pending", hashlib.sha256).digest()


def pending_token_erstellen(user, zweck: str) -> str:
    jetzt = datetime.now(timezone.utc)
    return jwt.encode({
        "sub": user.id,
        "type": TYP_PENDING,
        "aud": _PENDING_AUD,
        "zweck": zweck,
        "tv": getattr(user, "token_version", 0) or 0,
        "iat": jetzt,
        "exp": jetzt + PENDING_GUELTIG,
        "jti": uuid.uuid4().hex[:12],
    }, _pending_schluessel(), algorithm="HS256")


def pending_token_pruefen(token: str) -> dict:
    """Gibt die Nutzdaten zurück oder wirft ``jwt.PyJWTError``."""
    daten = jwt.decode(token, _pending_schluessel(), algorithms=["HS256"], audience=_PENDING_AUD,
                       options={"require": ["exp", "sub", "aud", "type"]})
    if daten.get("type") != TYP_PENDING:
        raise jwt.InvalidTokenError("kein mfa_pending-Token")
    return daten


# --- Redis: Replay-Schutz und Sperre --------------------------------------


def _k_fehl(uid: str) -> str:
    return f"mfa:fehlversuche:{uid}"


def _k_schritt(uid: str, schritt: int) -> str:
    return f"mfa:verbraucht:{uid}:{schritt}"


async def gesperrt_fuer(redis, uid: str) -> int:
    """Restsekunden der Sperre, 0 wenn frei."""
    anzahl = int(await redis.get(_k_fehl(uid)) or 0)
    if anzahl < MAX_FEHLVERSUCHE:
        return 0
    rest = await redis.ttl(_k_fehl(uid))
    return int(rest) if rest and rest > 0 else SPERRE_SEKUNDEN


async def fehlversuch_merken(redis, uid: str) -> int:
    # Ablaufzeit beim Anlegen setzen (SET NX EX), INCR behält sie. Ein verlorenes
    # EXPIRE nach dem INCR hätte eine Dauersperre ergeben (vgl. #879).
    await redis.set(_k_fehl(uid), 0, ex=SPERRE_SEKUNDEN, nx=True)
    return int(await redis.incr(_k_fehl(uid)))


async def fehlversuche_loeschen(redis, uid: str) -> None:
    await redis.delete(_k_fehl(uid))


async def zeitschritt_verbrauchen(redis, uid: str, schritt: int) -> bool:
    """True, wenn dieser Zeitschritt für das Konto noch nicht benutzt war."""
    gueltig_noch = (2 * TOLERANZ + 2) * PERIODE
    return bool(await redis.set(_k_schritt(uid, schritt), 1, ex=gueltig_noch, nx=True))


async def zweiten_faktor_pruefen(redis, user, code: str, *, geheimnis: str | None = None,
                                 wiederherstellung_erlaubt: bool = True) -> str | None:
    """Den zweiten Faktor prüfen — die eine Stelle für Anmeldung, Einrichtung, Abschalten.

    Gibt ``"totp"`` oder ``"wiederherstellungscode"`` zurück, bei falschem Code
    ``None`` (und zählt den Fehlversuch). Wirft ``ZweiFaktorGesperrt`` während
    der Sperre und ``ZweiFaktorNichtVerfuegbar`` ohne Redis. ``geheimnis``
    überschreibt das gespeicherte (Bestätigung einer neuen Einrichtung).
    """
    if redis is None:
        raise ZweiFaktorNichtVerfuegbar()
    uid = str(user.id)
    try:
        rest = await gesperrt_fuer(redis, uid)
    except Exception as e:  # noqa: BLE001 — Redis-Fehler heißt: nicht prüfbar
        raise ZweiFaktorNichtVerfuegbar() from e
    if rest:
        raise ZweiFaktorGesperrt(rest)

    art = None
    g = geheimnis or geheimnis_entschluesseln(user)
    schritt = passender_zeitschritt(g, code) if g else None
    if schritt is not None:
        if await zeitschritt_verbrauchen(redis, uid, schritt):
            art = "totp"
    elif wiederherstellung_erlaubt and wiederherstellungscode_einloesen(user, code):
        art = "wiederherstellungscode"

    if art is None:
        await fehlversuch_merken(redis, uid)
        return None
    await fehlversuche_loeschen(redis, uid)
    return art


def redis_aus_anfrage(request):
    """Der Redis-Client der Anwendung, oder ``None``."""
    state = getattr(getattr(request, "app", None), "state", None)
    dienst = getattr(state, "redis", None)
    return getattr(dienst, "client", None)
