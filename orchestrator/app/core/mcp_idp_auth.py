"""MCP-Endpunkte als OAuth-2.1-Ressource des Firmen-IdP (#1195).

Die eigenen MCP-Endpunkte (Second Brains ``/api/v1/mcp/brains/{slug}``, Agenten
``/api/v1/mcp/agents/{id}``) nehmen zusätzlich zum bisherigen Schlüssel ein
Zugriffstoken des Identitätsanbieters der Anlage an (Microsoft Entra ID oder ein
anderer OIDC-Anbieter). AI Employee stellt dabei KEINE Tokens aus — es ist nur
Ressourcenserver nach der MCP-Autorisierung:

* Protected Resource Metadata (RFC 9728) nennt den IdP als Authorization Server.
* Ein 401 trägt ``WWW-Authenticate: Bearer resource_metadata="…"``.
* Das Token wird vollständig hier geprüft: Signatur über das JWKS des IdP (mit
  Zwischenspeicher und Schlüsselwechsel), Algorithmus aus einer FESTEN Liste
  (nie aus dem Token; ``none`` und HS* sind ausgeschlossen), Aussteller exakt,
  Empfänger (``aud``) = konfigurierte Kennung dieser Ressource, Laufzeit.
* Das Token wird einem vorhandenen Konto zugeordnet — über die unveränderliche
  Objekt-ID (``oid``), die der SSO-Login am Konto hinterlegt. Die E-Mail dient nur
  als Rückfall und nur unter denselben Bedingungen, unter denen der SSO-Login ihr
  vertraut. Unbekannte Personen werden abgewiesen, nie als Gast behandelt; es wird
  nie ein Konto angelegt.

Ohne Konfiguration (Schalter, Aussteller, Empfänger) ist das alles aus.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx
import jwt
from sqlalchemy import func, select

from app.config import settings
from app.core.log_redaction import scrub_log

logger = logging.getLogger(__name__)

#: Erlaubte Signaturverfahren — fest auf dem Server, nie aus dem Token übernommen.
#: Bewusst nur asymmetrische Verfahren: ``none`` und HS* (dort würde der öffentliche
#: Schlüssel zum gemeinsamen Geheimnis) kommen nicht vor.
ERLAUBTE_ALGORITHMEN = ("RS256", "RS384", "RS512", "PS256", "PS384", "PS512",
                        "ES256", "ES384", "ES512")
_SCHLUESSELTYP = {"RS": "RSA", "PS": "RSA", "ES": "EC"}

#: Toleranz für Uhrabweichung bei ``exp``/``nbf``/``iat`` in Sekunden.
LEEWAY_SEKUNDEN = 60
#: Wie lange ein geladenes JWKS ohne Anlass gilt.
JWKS_TTL_SEKUNDEN = 3600
#: Mindestabstand zwischen zwei erzwungenen Neuladungen wegen unbekannter ``kid``
#: — sonst könnte jeder mit erfundenen ``kid`` den IdP über uns beschießen.
JWKS_MIN_NEULADEN_SEKUNDEN = 60

_MS_MULTITENANT = {"common", "organizations", "consumers"}


class IdpTokenFehler(Exception):
    """Abweisung eines Tokens. ``status`` 401 = Token unbrauchbar, 403 = gültiges
    Token, aber keine berechtigte Person dahinter."""

    def __init__(self, grund: str, status: int = 401):
        super().__init__(grund)
        self.grund = grund
        self.status = status


@dataclass(frozen=True)
class IdpKonfig:
    issuer: str
    audiences: tuple[str, ...]
    jwks_uri: str  # leer = über die OpenID-Konfiguration des Ausstellers ermitteln
    required_scope: str


# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

def _ist_guid(wert: str) -> bool:
    import uuid

    try:
        uuid.UUID(wert)
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def _microsoft_tenant() -> str:
    return (getattr(settings, "oauth_microsoft_tenant_id", "") or "").strip().lower()


def abgeleiteter_issuer() -> str:
    """Aussteller aus der SSO-Einrichtung, wenn keiner eingetragen ist.

    Nur bei einer Microsoft-Verzeichnis-ID (GUID): Entra-v2-Tokens tragen genau
    ``https://login.microsoftonline.com/<GUID>/v2.0``. Ein Domänenname oder
    ``common`` ergibt keinen prüfbaren Aussteller → leer.
    """
    tenant = _microsoft_tenant()
    if tenant and tenant not in _MS_MULTITENANT and _ist_guid(tenant):
        return f"https://login.microsoftonline.com/{tenant}/v2.0"
    return ""


def konfig() -> IdpKonfig | None:
    """Die wirksame Einrichtung — ``None``, solange die Funktion aus ist."""
    if not getattr(settings, "mcp_idp_oauth_enabled", False):
        return None
    issuer = (getattr(settings, "mcp_idp_issuer", "") or "").strip() or abgeleiteter_issuer()
    audiences = tuple(
        a.strip() for a in (getattr(settings, "mcp_idp_audience", "") or "").split(",") if a.strip()
    )
    if not issuer or not audiences:
        return None
    return IdpKonfig(
        issuer=issuer,
        audiences=audiences,
        jwks_uri=(getattr(settings, "mcp_idp_jwks_uri", "") or "").strip(),
        required_scope=(getattr(settings, "mcp_idp_required_scope", "") or "").strip(),
    )


def aktiv() -> bool:
    return konfig() is not None


# ---------------------------------------------------------------------------
# Protected Resource Metadata (RFC 9728) + WWW-Authenticate
# ---------------------------------------------------------------------------

def _basis() -> str:
    return (settings.oauth_redirect_base_url or "").rstrip("/")


def ressource_url(pfad: str) -> str:
    """Kennung einer MCP-Ressource, z. B. ``…/api/v1/mcp/brains/vertrieb``."""
    return f"{_basis()}/api/v1/mcp/{quote(pfad.strip('/'), safe='/')}"


def prm_url(pfad: str) -> str:
    """Well-known-Adresse mit Pfad-Anhang nach RFC 9728, Abschnitt 3.1."""
    return f"{_basis()}/.well-known/oauth-protected-resource/api/v1/mcp/{quote(pfad.strip('/'), safe='/')}"


def prm_dokument(pfad: str) -> dict:
    k = konfig()
    if k is None:
        return {}
    doc: dict[str, Any] = {
        "resource": ressource_url(pfad),
        "authorization_servers": [k.issuer],
        "bearer_methods_supported": ["header"],
        "resource_name": "AI Employee MCP",
    }
    if k.required_scope:
        doc["scopes_supported"] = [k.required_scope]
    return doc


def www_authenticate(pfad: str, fehler: str = "") -> str:
    """Kopfzeile für ein 401 — ohne aktive Funktion nur ``Bearer``."""
    k = konfig()
    if k is None:
        return "Bearer"
    teile = [f'resource_metadata="{prm_url(pfad)}"']
    if k.required_scope:
        teile.append(f'scope="{k.required_scope}"')
    if fehler:
        teile.append(f'error="{fehler}"')
    return "Bearer " + ", ".join(teile)


# ---------------------------------------------------------------------------
# JWKS (zwischengespeichert, Schlüsselwechsel über unbekannte kid)
# ---------------------------------------------------------------------------

_jwks: dict[str, tuple[float, dict[str, dict]]] = {}
_letztes_neuladen: dict[str, float] = {}
_jwks_sperre = asyncio.Lock()
_discovery: dict[str, str] = {}


def cache_leeren() -> None:
    """Nach einer Änderung der Einrichtung — alte Schlüssel gelten nicht weiter."""
    _jwks.clear()
    _letztes_neuladen.clear()
    _discovery.clear()


async def _hole_json(url: str) -> dict:
    """Ein HTTPS-Abruf beim IdP. In Tests ersetzt."""
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
        resp = await client.get(url, headers={"Accept": "application/json"})
        resp.raise_for_status()
        data = resp.json()
    if not isinstance(data, dict):
        raise ValueError("Antwort ist kein JSON-Objekt")
    return data


def _nur_https(url: str) -> str:
    if not url.lower().startswith("https://"):
        raise IdpTokenFehler("Schlüsseladresse des IdP muss https sein")
    return url


async def _jwks_uri(k: IdpKonfig) -> str:
    if k.jwks_uri:
        return _nur_https(k.jwks_uri)
    if k.issuer in _discovery:
        return _discovery[k.issuer]
    oidc = await _hole_json(_nur_https(k.issuer.rstrip("/") + "/.well-known/openid-configuration"))
    # Die Konfiguration muss zum eingetragenen Aussteller gehören (OIDC Discovery 4.3).
    if oidc.get("issuer") != k.issuer:
        raise IdpTokenFehler("OpenID-Konfiguration des IdP passt nicht zum Aussteller")
    uri = _nur_https(str(oidc.get("jwks_uri") or ""))
    _discovery[k.issuer] = uri
    return uri


async def _lade_jwks(uri: str) -> dict[str, dict]:
    data = await _hole_json(uri)
    schluessel = {}
    for jwk in data.get("keys") or []:
        if isinstance(jwk, dict) and isinstance(jwk.get("kid"), str) and jwk.get("use", "sig") == "sig":
            schluessel[jwk["kid"]] = jwk
    _jwks[uri] = (time.monotonic(), schluessel)
    return schluessel


async def _schluessel(k: IdpKonfig, kid: str) -> dict:
    uri = await _jwks_uri(k)
    async with _jwks_sperre:
        eintrag = _jwks.get(uri)
        jetzt = time.monotonic()
        if eintrag is None or jetzt - eintrag[0] > JWKS_TTL_SEKUNDEN:
            schluessel = await _lade_jwks(uri)
        else:
            schluessel = eintrag[1]
        if kid in schluessel:
            return schluessel[kid]
        # Unbekannte kid: der IdP hat womöglich gerade gewechselt — EINMAL neu laden,
        # aber höchstens alle JWKS_MIN_NEULADEN_SEKUNDEN.
        if jetzt - _letztes_neuladen.get(uri, -1e9) >= JWKS_MIN_NEULADEN_SEKUNDEN:
            _letztes_neuladen[uri] = jetzt
            schluessel = await _lade_jwks(uri)
            if kid in schluessel:
                return schluessel[kid]
    raise IdpTokenFehler("Unbekannter Signaturschlüssel")


# ---------------------------------------------------------------------------
# Tokenprüfung
# ---------------------------------------------------------------------------

def sieht_wie_jwt_aus(token: str) -> bool:
    return token.count(".") == 2 and len(token) < 16384


async def pruefe_token(token: str) -> dict:
    """Prüft ein Zugriffstoken des IdP und gibt seine Angaben zurück.

    Wirft ``IdpTokenFehler`` (401) bei jedem Mangel.
    """
    k = konfig()
    if k is None:
        raise IdpTokenFehler("Anmeldung über den IdP ist nicht eingerichtet")
    if not token or not sieht_wie_jwt_aus(token):
        raise IdpTokenFehler("Kein JWT")
    try:
        kopf = jwt.get_unverified_header(token)
    except jwt.PyJWTError:
        raise IdpTokenFehler("Kopf unlesbar")
    alg = kopf.get("alg")
    if alg not in ERLAUBTE_ALGORITHMEN:
        raise IdpTokenFehler("Signaturverfahren nicht erlaubt")
    kid = kopf.get("kid")
    if not isinstance(kid, str) or not kid:
        raise IdpTokenFehler("Token ohne Schlüsselkennung")

    try:
        jwk = await _schluessel(k, kid)
    except IdpTokenFehler:
        raise
    except Exception as e:  # noqa: BLE001 — IdP nicht erreichbar o. ä.
        logger.warning("JWKS des IdP nicht ladbar: %s", scrub_log(e))
        raise IdpTokenFehler("Schlüssel des IdP nicht verfügbar")

    # Schlüsseltyp muss zum (erlaubten) Verfahren passen; ein im JWK genanntes
    # Verfahren muss mit dem des Tokens übereinstimmen.
    if jwk.get("kty") != _SCHLUESSELTYP[alg[:2]]:
        raise IdpTokenFehler("Schlüsseltyp passt nicht zum Verfahren")
    if jwk.get("alg") and jwk["alg"] != alg:
        raise IdpTokenFehler("Verfahren passt nicht zum Schlüssel")
    try:
        oeffentlich = jwt.PyJWK(jwk, algorithm=alg).key
    except jwt.PyJWTError:
        raise IdpTokenFehler("Schlüssel des IdP unbrauchbar")

    try:
        claims = jwt.decode(
            token,
            oeffentlich,
            algorithms=[alg],
            audience=list(k.audiences),
            issuer=k.issuer,
            leeway=LEEWAY_SEKUNDEN,
            options={"require": ["exp", "iss", "aud"], "verify_signature": True},
        )
    except jwt.ExpiredSignatureError:
        raise IdpTokenFehler("Token abgelaufen")
    except jwt.ImmatureSignatureError:
        raise IdpTokenFehler("Token noch nicht gültig")
    except jwt.InvalidAudienceError:
        raise IdpTokenFehler("Token für eine andere Ressource")
    except jwt.InvalidIssuerError:
        raise IdpTokenFehler("Falscher Aussteller")
    except jwt.PyJWTError:
        raise IdpTokenFehler("Token ungültig")

    # PyJWT prüft ``iss`` nur auf Gleichheit, wenn es ein String ist — sicherheitshalber
    # noch einmal ausdrücklich.
    if claims.get("iss") != k.issuer:
        raise IdpTokenFehler("Falscher Aussteller")

    if k.required_scope:
        scp = claims.get("scp")
        scopes = set(scp.split()) if isinstance(scp, str) else set(scp or [])
        rollen = claims.get("roles") if isinstance(claims.get("roles"), list) else []
        if k.required_scope not in scopes and k.required_scope not in rollen:
            # Ein bekanntes Präfix (Entra: ``api://…/mcp``) steht im scp ohne Präfix.
            kurz = k.required_scope.rsplit("/", 1)[-1]
            if kurz not in scopes:
                raise IdpTokenFehler("Erforderliche Berechtigung fehlt")
    return claims


# ---------------------------------------------------------------------------
# Zuordnung zu einem Konto
# ---------------------------------------------------------------------------

def _email_vertrauenswuerdig(claims: dict, k: IdpKonfig) -> bool:
    """Gilt die E-Mail im Token als bestätigt? Dieselbe Regel wie beim SSO-Login.

    * ``email_verified: true`` im Token (OIDC-Anbieter, die das ausweisen);
    * Entra: der SSO-Login vertraut der Adresse nur, wenn die Anwendung auf EIN
      Verzeichnis festgelegt ist (keine der Multi-Tenant-Kennungen). Zusätzlich muss
      das Token aus genau diesem Verzeichnis stammen.
    """
    if claims.get("email_verified") is True:
        return True
    tid = str(claims.get("tid") or "").lower()
    tenant = _microsoft_tenant()
    if tid and tenant and tenant not in _MS_MULTITENANT and tid in k.issuer.lower():
        return tenant == tid or not _ist_guid(tenant)
    return False


def _email_aus(claims: dict) -> str:
    for feld in ("email", "upn"):
        wert = claims.get(feld)
        if isinstance(wert, str) and "@" in wert:
            return wert.strip()
    return ""


async def _nutzer_per_oid(db, oid: str):
    from app.models.user import User

    return await db.scalar(select(User).where(User.idp_object_id == oid))


async def _nutzer_per_email(db, email: str):
    from app.models.user import User

    return await db.scalar(select(User).where(func.lower(User.email) == email.lower()))


def _benutzbar(user) -> bool:
    return bool(user) and bool(getattr(user, "is_active", False)) and bool(getattr(user, "approved", True))


async def nutzer_zum_token(db, claims: dict):
    """Das AI-Employee-Konto zum geprüften Token — sonst ``IdpTokenFehler(403)``."""
    k = konfig()
    if k is None:
        raise IdpTokenFehler("Anmeldung über den IdP ist nicht eingerichtet")
    oid = claims.get("oid")
    oid = oid.strip().lower() if isinstance(oid, str) and oid.strip() else ""

    if oid:
        user = await _nutzer_per_oid(db, oid)
        if user is not None:
            if not _benutzbar(user):
                raise IdpTokenFehler("Konto gesperrt oder nicht freigegeben", 403)
            return user

    email = _email_aus(claims)
    if not email or not _email_vertrauenswuerdig(claims, k):
        raise IdpTokenFehler("Person ist in AI Employee nicht bekannt", 403)
    user = await _nutzer_per_email(db, email)
    if user is None:
        raise IdpTokenFehler("Person ist in AI Employee nicht bekannt", 403)
    vorhanden = (getattr(user, "idp_object_id", None) or "").lower()
    if oid and vorhanden and vorhanden != oid:
        # Gleiche Adresse, andere Person im IdP (z. B. neu vergebenes Postfach).
        logger.warning("MCP-IdP: E-Mail %s gehört zu einer anderen Objekt-ID — abgewiesen", scrub_log(email))
        raise IdpTokenFehler("Person ist in AI Employee nicht bekannt", 403)
    if not _benutzbar(user):
        raise IdpTokenFehler("Konto gesperrt oder nicht freigegeben", 403)
    if oid and not vorhanden:
        user.idp_object_id = oid
        await db.commit()
        logger.info("MCP-IdP: Objekt-ID an Konto %s gebunden", scrub_log(email))
    return user


async def nutzer_aus_bearer(db, token: str):
    """Token prüfen und Konto zuordnen — der eine Einstieg für die MCP-Endpunkte."""
    claims = await pruefe_token(token)
    return await nutzer_zum_token(db, claims)
