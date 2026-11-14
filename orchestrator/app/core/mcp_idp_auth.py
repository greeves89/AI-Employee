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
from sqlalchemy import select

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
    #: Erlaubte aufrufende Anwendungen (``azp`` bei v2-, ``appid`` bei v1-Tokens).
    #: Leer = keine Prüfung; dann muss ``required_scope`` gesetzt sein.
    allowed_clients: tuple[str, ...] = ()


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
    required_scope = (getattr(settings, "mcp_idp_required_scope", "") or "").strip()
    allowed_clients = liste(getattr(settings, "mcp_idp_allowed_clients", "") or "")
    if not issuer or not audiences:
        return None
    # Ohne Begrenzung der aufrufenden Anwendung UND ohne Pflicht-Berechtigung nähme
    # jede App im Verzeichnis, die ein Token für diesen Empfänger bekommt, den Weg.
    if not allowed_clients and not required_scope:
        return None
    return IdpKonfig(
        issuer=issuer,
        audiences=audiences,
        jwks_uri=(getattr(settings, "mcp_idp_jwks_uri", "") or "").strip(),
        required_scope=required_scope,
        allowed_clients=allowed_clients,
    )


def liste(wert: str) -> tuple[str, ...]:
    """Komma- oder leerzeichengetrennte Angabe als Tupel, ohne Leereinträge."""
    return tuple(t.strip().lower() for t in wert.replace(",", " ").split() if t.strip())


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
_discovery: dict[str, str] = {}
#: Zeitpunkt des letzten FEHLGESCHLAGENEN Abrufs je Adresse (Negativ-Speicher).
_fehlschlag: dict[str, float] = {}
#: Laufende Abrufe je Adresse — gleichzeitige Anfragen warten auf denselben
#: Abruf, statt den IdP parallel zu befragen (single flight). Keine Sperre um den
#: Netzabruf: ein langsamer IdP hält so nicht jede andere Prüfung an.
_laufend: dict[str, asyncio.Future] = {}

#: Wie lange nach einem Fehlschlag nicht erneut abgerufen wird.
FEHLSCHLAG_SPERRE_SEKUNDEN = 45
#: Zeitlimit eines Abrufs beim IdP.
ABRUF_TIMEOUT_SEKUNDEN = 4.0


def cache_leeren() -> None:
    """Nach einer Änderung der Einrichtung — alte Schlüssel gelten nicht weiter."""
    _jwks.clear()
    _letztes_neuladen.clear()
    _discovery.clear()
    _fehlschlag.clear()
    _laufend.clear()


async def _hole_json(url: str) -> dict:
    """Ein HTTPS-Abruf beim IdP. In Tests ersetzt."""
    from app.core.feste_verbindung import FesteAdresse

    # Per einmal geprueffter Adresse verbinden: sonst loeste httpx den Namen nach der
    # SSRF-Pruefung ein zweites Mal auf (DNS-Rebinding, Plan Sicherheit 2026-11 Punkt 8).
    async with httpx.AsyncClient(timeout=ABRUF_TIMEOUT_SEKUNDEN, follow_redirects=False,
                                 transport=FesteAdresse(True, name="MCP-IdP")) as client:
        resp = await client.get(url, headers={"Accept": "application/json"})
        resp.raise_for_status()
        data = resp.json()
    if not isinstance(data, dict):
        raise ValueError("Antwort ist kein JSON-Objekt")
    return data


async def _ziel_erlaubt(url: str) -> bool:
    """SSRF-Sperre: nur https und kein internes Ziel (nach DNS-Auflösung).

    Dieselbe Prüfung wie für Rückruf-Adressen (``core/url_guard``). Die Auflösung
    blockiert, deshalb in einem eigenen Faden.
    """
    from app.core.url_guard import check_outbound_url

    erlaubt, grund = await asyncio.to_thread(check_outbound_url, url)
    if not erlaubt:
        logger.warning("MCP-IdP: Abruf von %s abgelehnt (%s)", scrub_log(url), grund)
    return erlaubt


async def _abruf(url: str) -> dict:
    """Ein Abruf beim IdP mit SSRF-Sperre, Negativ-Speicher und single flight."""
    if time.monotonic() - _fehlschlag.get(url, -1e9) < FEHLSCHLAG_SPERRE_SEKUNDEN:
        raise IdpTokenFehler("Schlüssel des IdP nicht verfügbar")
    laufend = _laufend.get(url)
    if laufend is not None:
        return await asyncio.shield(laufend)

    async def _los() -> dict:
        if not await _ziel_erlaubt(url):
            raise IdpTokenFehler("Adresse des IdP nicht erlaubt")
        return await _hole_json(url)

    aufgabe = asyncio.ensure_future(_los())
    _laufend[url] = aufgabe
    try:
        return await asyncio.shield(aufgabe)
    except IdpTokenFehler:
        _fehlschlag[url] = time.monotonic()
        raise
    except Exception as e:  # noqa: BLE001 — Netz, Zeitlimit, kaputtes JSON
        _fehlschlag[url] = time.monotonic()
        logger.warning("MCP-IdP: Abruf von %s fehlgeschlagen: %s", scrub_log(url), scrub_log(e))
        raise IdpTokenFehler("Schlüssel des IdP nicht verfügbar")
    finally:
        if _laufend.get(url) is aufgabe:
            _laufend.pop(url, None)


def _nur_https(url: str) -> str:
    if not url.lower().startswith("https://"):
        raise IdpTokenFehler("Schlüsseladresse des IdP muss https sein")
    return url


def _host(url: str) -> str:
    from app.core.url_guard import eindeutiger_host

    return eindeutiger_host(url) or ""


async def _jwks_uri(k: IdpKonfig) -> str:
    if k.jwks_uri:
        return _nur_https(k.jwks_uri)  # ausdrücklich gesetzt: Host frei wählbar
    if k.issuer in _discovery:
        return _discovery[k.issuer]
    oidc = await _abruf(_nur_https(k.issuer.rstrip("/") + "/.well-known/openid-configuration"))
    # Die Konfiguration muss zum eingetragenen Aussteller gehören (OIDC Discovery 4.3).
    if oidc.get("issuer") != k.issuer:
        raise IdpTokenFehler("OpenID-Konfiguration des IdP passt nicht zum Aussteller")
    uri = _nur_https(str(oidc.get("jwks_uri") or ""))
    # Ohne ausdrückliche Angabe nur Schlüssel vom Host des Ausstellers — sonst könnte
    # ein manipuliertes Discovery-Dokument auf einen beliebigen Host zeigen.
    if not _host(uri) or _host(uri) != _host(k.issuer):
        raise IdpTokenFehler("Schlüsseladresse liegt nicht beim Aussteller")
    _discovery[k.issuer] = uri
    return uri


async def _lade_jwks(uri: str) -> dict[str, dict]:
    data = await _abruf(uri)
    schluessel = {}
    for jwk in data.get("keys") or []:
        if isinstance(jwk, dict) and isinstance(jwk.get("kid"), str) and jwk.get("use", "sig") == "sig":
            schluessel[jwk["kid"]] = jwk
    jetzt = time.monotonic()
    _jwks[uri] = (jetzt, schluessel)
    # JEDES Laden zählt für die Sperrfrist, auch das nach Ablauf — sonst löste
    # direkt danach eine erfundene kid gleich den nächsten Abruf aus.
    _letztes_neuladen[uri] = jetzt
    return schluessel


async def _schluessel(k: IdpKonfig, kid: str) -> dict:
    uri = await _jwks_uri(k)
    eintrag = _jwks.get(uri)
    if eintrag is None or time.monotonic() - eintrag[0] > JWKS_TTL_SEKUNDEN:
        schluessel = await _lade_jwks(uri)
    else:
        schluessel = eintrag[1]
    if kid in schluessel:
        return schluessel[kid]
    # Unbekannte kid: der IdP hat womöglich gerade gewechselt — EINMAL neu laden,
    # aber höchstens alle JWKS_MIN_NEULADEN_SEKUNDEN (zählt ab dem letzten Laden).
    if time.monotonic() - _letztes_neuladen.get(uri, -1e9) >= JWKS_MIN_NEULADEN_SEKUNDEN:
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

    jwk = await _schluessel(k, kid)

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

    # Ein ID-Token (Anmeldebeleg für den Client) ist kein Zugriffstoken für uns —
    # erkennbar an ``nonce`` bzw. ``at_hash``, die nur ID-Tokens tragen.
    if "nonce" in claims or "at_hash" in claims:
        raise IdpTokenFehler("ID-Token statt Zugriffstoken")

    if k.allowed_clients:
        client = claims.get("azp") or claims.get("appid")
        if not isinstance(client, str) or not client.strip():
            raise IdpTokenFehler("Aufrufende Anwendung nicht genannt")
        if client.strip().lower() not in k.allowed_clients:
            raise IdpTokenFehler("Aufrufende Anwendung nicht erlaubt")

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

    * ``email_verified: true`` im Token (Anbieter, die das ausweisen);
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


def ist_gast_oder_app(claims: dict) -> bool:
    """Gast-/B2B-Konten und App-Tokens kommen nie auf ein Personenkonto.

    * ``#EXT#`` im ``upn``/``preferred_username``/``unique_name`` — Gastkonto;
    * ``acct == 1`` — Gastkonto (optionaler Entra-Claim);
    * ``idtyp`` vorhanden und nicht ``user`` — App-Token ohne Person.
    """
    for feld in ("upn", "preferred_username", "unique_name"):
        wert = claims.get(feld)
        if isinstance(wert, str) and "#ext#" in wert.lower():
            return True
    if str(claims.get("acct", "")) == "1":
        return True
    idtyp = claims.get("idtyp")
    return idtyp is not None and str(idtyp).lower() != "user"


def _email_aus(claims: dict) -> str:
    """Nur der ``email``-Claim. ``upn`` ist ein Anmeldename, keine bestätigte Adresse."""
    wert = claims.get("email")
    if isinstance(wert, str) and "@" in wert:
        return wert.strip()
    return ""


async def _nutzer_per_oid(db, oid: str):
    from app.models.user import User

    return await db.scalar(select(User).where(User.idp_object_id == oid))


async def _nutzer_per_email(db, email: str):
    from app.models.user import User

    # Exakt wie der SSO-Login (``_find_or_create_user``) — keine lockerere Regel hier.
    return await db.scalar(select(User).where(User.email == email))


def _benutzbar(user) -> bool:
    return bool(user) and bool(getattr(user, "is_active", False)) and bool(getattr(user, "approved", True))


async def _bindung_protokollieren(db, user, oid: str, email: str) -> None:
    from app.core.audit import protokolliere
    from app.models.audit_log import AuditEventType

    await protokolliere(
        db, AuditEventType.USER_UPDATED, user_id=user.id,
        command=f"IdP-Objekt-ID per E-Mail an Konto gebunden (MCP-Anmeldung): {email}",
        meta={"weg": "mcp_idp_email_rueckfall", "target_user_id": user.id, "target_email": email,
              "idp_object_id": oid},
    )


async def nutzer_zum_token(db, claims: dict):
    """Das AI-Employee-Konto zum geprüften Token — sonst ``IdpTokenFehler(403)``."""
    k = konfig()
    if k is None:
        raise IdpTokenFehler("Anmeldung über den IdP ist nicht eingerichtet")
    if ist_gast_oder_app(claims):
        raise IdpTokenFehler("Gast- oder App-Token", 403)
    oid = claims.get("oid")
    oid = oid.strip().lower() if isinstance(oid, str) and oid.strip() else ""

    if oid:
        user = await _nutzer_per_oid(db, oid)
        if user is not None:
            if not _benutzbar(user):
                raise IdpTokenFehler("Konto gesperrt oder nicht freigegeben", 403)
            return user

    # Rückfall E-Mail: nur mit Objekt-ID (sonst gäbe es nichts zu binden und jedes
    # Token mit dieser Adresse käme immer wieder durch), nur bestätigte Adresse, und
    # nur für Konten, die selbst per Microsoft-SSO entstanden bzw. verknüpft sind.
    email = _email_aus(claims)
    if not oid or not email or not _email_vertrauenswuerdig(claims, k):
        raise IdpTokenFehler("Person ist in AI Employee nicht bekannt", 403)
    user = await _nutzer_per_email(db, email)
    if user is None or getattr(user, "sso_provider", None) != "microsoft":
        raise IdpTokenFehler("Person ist in AI Employee nicht bekannt", 403)
    vorhanden = (getattr(user, "idp_object_id", None) or "").lower()
    if vorhanden and vorhanden != oid:
        # Gleiche Adresse, andere Person im IdP (z. B. neu vergebenes Postfach).
        logger.warning("MCP-IdP: E-Mail %s gehört zu einer anderen Objekt-ID — abgewiesen", scrub_log(email))
        raise IdpTokenFehler("Person ist in AI Employee nicht bekannt", 403)
    if not _benutzbar(user):
        raise IdpTokenFehler("Konto gesperrt oder nicht freigegeben", 403)
    if not vorhanden:
        user.idp_object_id = oid
        await _bindung_protokollieren(db, user, oid, email)
        await db.commit()
        logger.info("MCP-IdP: Objekt-ID an Konto %s gebunden", scrub_log(email))
    return user


async def nutzer_aus_bearer(db, token: str):
    """Token prüfen und Konto zuordnen — der eine Einstieg für die MCP-Endpunkte."""
    claims = await pruefe_token(token)
    return await nutzer_zum_token(db, claims)
