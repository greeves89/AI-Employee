"""MCP-Endpunkte per OAuth 2.1 über den Firmen-IdP (#1195).

MC/DC-Matrix der Tokenprüfung (``core/mcp_idp_auth.pruefe_token``): Jede Zeile
kippt GENAU eine Bedingung gegenüber dem gültigen Token und muss allein dadurch
zur Abweisung führen.

| Fall                                  | Sig | alg | iss | aud | exp | nbf | kid | Ergebnis |
|---------------------------------------|-----|-----|-----|-----|-----|-----|-----|----------|
| gültig                                |  ok |  ok |  ok |  ok |  ok |  ok |  ok | Annahme  |
| falsches aud                          |  ok |  ok |  ok |  X  |  ok |  ok |  ok | 401      |
| abgelaufen                            |  ok |  ok |  ok |  ok |  X  |  ok |  ok | 401      |
| nbf in der Zukunft                    |  ok |  ok |  ok |  ok |  ok |  X  |  ok | 401      |
| falscher Aussteller                   |  ok |  ok |  X  |  ok |  ok |  ok |  ok | 401      |
| fremder Schlüssel, gleiche kid        |  X  |  ok |  ok |  ok |  ok |  ok |  ok | 401      |
| alg none                              |  X  |  X  |  ok |  ok |  ok |  ok |  ok | 401      |
| HS256 mit öffentl. Schlüssel als Geh. |  X  |  X  |  ok |  ok |  ok |  ok |  ok | 401      |
| unbekannte kid (einmal neu laden)     |  -  |  ok |  ok |  ok |  ok |  ok |  X  | 401      |

Dazu die Zuordnung zur Person (bekannte oid, unbekannte Person, E-Mail-Rückfall nur
unter den Bedingungen des SSO-Logins), der Schalter (ohne Einrichtung aus), und
die Endpunkte: Brain-Schlüssel funktioniert weiter, IdP-Token sieht nur
freigegebene Brains, 401 trägt ``WWW-Authenticate`` mit ``resource_metadata``.

Die Schlüssel entstehen hier mit ``cryptography``; das JWKS wird nicht geholt,
sondern ``_hole_json`` ersetzt.
"""

import asyncio
import base64
import json
import time
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from app.config import settings
from app.core import mcp_idp_auth as m

TENANT = "11111111-2222-3333-4444-555555555555"
ISSUER = f"https://login.microsoftonline.com/{TENANT}/v2.0"
AUDIENCE = "api://ai-employee.example.com"
JWKS_URI = "https://login.example.com/keys"
OID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _run(coro):
    return asyncio.run(coro)


def _rsa():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwk(priv, kid: str) -> dict:
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(priv.public_key()))
    jwk.update({"kid": kid, "use": "sig"})
    return jwk


SCHLUESSEL = _rsa()
FREMD = _rsa()
EC_SCHLUESSEL = ec.generate_private_key(ec.SECP256R1())


def _claims(**ueber) -> dict:
    jetzt = int(time.time())
    c = {"iss": ISSUER, "aud": AUDIENCE, "sub": "pairwise-sub", "oid": OID, "tid": TENANT,
         "iat": jetzt, "nbf": jetzt - 5, "exp": jetzt + 600, "scp": "mcp.access",
         "upn": "person@example.com"}
    c.update(ueber)
    return {k: v for k, v in c.items() if v is not None}


def _token(claims=None, key=SCHLUESSEL, kid="k1", alg="RS256") -> str:
    return jwt.encode(claims or _claims(), key, algorithm=alg, headers={"kid": kid})


def _b64(d: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()


@contextmanager
def _eingerichtet(keys=None, **ueber):
    """Funktion an, festes JWKS; ``_hole_json`` zählt die Abrufe."""
    werte = {"mcp_idp_oauth_enabled": True, "mcp_idp_issuer": ISSUER, "mcp_idp_audience": AUDIENCE,
             "mcp_idp_jwks_uri": JWKS_URI, "mcp_idp_required_scope": "",
             "oauth_microsoft_tenant_id": TENANT, "oauth_redirect_base_url": "https://ai.example.com"}
    werte.update(ueber)
    jwks = {"keys": keys if keys is not None else [_jwk(SCHLUESSEL, "k1")]}
    abrufe = []

    async def hole(url):
        abrufe.append(url)
        return jwks

    m.cache_leeren()
    with patch.multiple(settings, **werte), patch.object(m, "_hole_json", hole):
        yield SimpleNamespace(abrufe=abrufe, jwks=jwks)
    m.cache_leeren()


class TokenpruefungMatrix(unittest.TestCase):
    def _abgewiesen(self, token) -> str:
        with self.assertRaises(m.IdpTokenFehler) as ctx:
            _run(m.pruefe_token(token))
        self.assertEqual(ctx.exception.status, 401)
        return ctx.exception.grund

    def test_gueltiges_token(self):
        with _eingerichtet():
            claims = _run(m.pruefe_token(_token()))
        self.assertEqual(claims["oid"], OID)

    def test_falsches_aud(self):
        with _eingerichtet():
            self.assertIn("andere Ressource", self._abgewiesen(_token(_claims(aud="api://andere-app"))))

    def test_ohne_aud(self):
        with _eingerichtet():
            self._abgewiesen(_token(_claims(aud=None)))

    def test_abgelaufen(self):
        jetzt = int(time.time())
        with _eingerichtet():
            self.assertIn("abgelaufen", self._abgewiesen(_token(_claims(exp=jetzt - 3600, iat=jetzt - 7200))))

    def test_abgelaufen_innerhalb_der_toleranz_geht_noch(self):
        with _eingerichtet():
            _run(m.pruefe_token(_token(_claims(exp=int(time.time()) - 10))))

    def test_nbf_in_der_zukunft(self):
        with _eingerichtet():
            self.assertIn("noch nicht", self._abgewiesen(_token(_claims(nbf=int(time.time()) + 3600))))

    def test_falscher_aussteller(self):
        with _eingerichtet():
            self.assertIn("Aussteller", self._abgewiesen(
                _token(_claims(iss="https://login.microsoftonline.com/99999999-0000-0000-0000-000000000000/v2.0"))))

    def test_fremder_schluessel_mit_bekannter_kid(self):
        with _eingerichtet():
            self._abgewiesen(_token(key=FREMD, kid="k1"))

    def test_alg_none(self):
        kopf = _b64({"alg": "none", "typ": "JWT", "kid": "k1"})
        token = f"{kopf}.{_b64(_claims())}."
        with _eingerichtet():
            self.assertIn("nicht erlaubt", self._abgewiesen(token))

    def test_hs256_mit_oeffentlichem_schluessel_als_geheimnis(self):
        pem = SCHLUESSEL.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        # PyJWT verweigert PEM als HMAC-Geheimnis — von Hand signieren, wie ein Angreifer.
        import hashlib
        import hmac as _hmac
        kopf = _b64({"alg": "HS256", "typ": "JWT", "kid": "k1"})
        rumpf = _b64(_claims())
        sig = base64.urlsafe_b64encode(
            _hmac.new(pem, f"{kopf}.{rumpf}".encode(), hashlib.sha256).digest()).rstrip(b"=").decode()
        with _eingerichtet():
            self.assertIn("nicht erlaubt", self._abgewiesen(f"{kopf}.{rumpf}.{sig}"))

    def test_unbekannte_kid_laedt_einmal_neu_dann_abweisung(self):
        with _eingerichtet() as e:
            _run(m.pruefe_token(_token()))          # füllt den Speicher
            self.assertEqual(len(e.abrufe), 1)
            self.assertIn("Unbekannter", self._abgewiesen(_token(kid="unbekannt")))
            self.assertEqual(len(e.abrufe), 2)       # genau ein Neuladen
            self._abgewiesen(_token(kid="noch-eine"))
            self.assertEqual(len(e.abrufe), 2)       # Sperrfrist: kein weiterer Abruf

    def test_schluesselwechsel_wird_ueber_neuladen_gefunden(self):
        neu = _rsa()
        with _eingerichtet() as e:
            _run(m.pruefe_token(_token()))
            e.jwks["keys"] = [_jwk(SCHLUESSEL, "k1"), _jwk(neu, "k2")]
            _run(m.pruefe_token(_token(key=neu, kid="k2")))
            self.assertEqual(len(e.abrufe), 2)

    def test_es256_gegen_rsa_schluessel_abgewiesen(self):
        with _eingerichtet():
            self.assertIn("Schlüsseltyp", self._abgewiesen(_token(key=EC_SCHLUESSEL, alg="ES256")))

    def test_es256_mit_ec_schluessel_angenommen(self):
        jwk = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(EC_SCHLUESSEL.public_key()))
        jwk.update({"kid": "e1"})
        with _eingerichtet(keys=[jwk]):
            _run(m.pruefe_token(_token(key=EC_SCHLUESSEL, alg="ES256", kid="e1")))

    def test_kein_jwt(self):
        with _eingerichtet():
            self._abgewiesen("brain-schluessel-ohne-punkte")

    def test_erforderliche_berechtigung(self):
        with _eingerichtet(mcp_idp_required_scope=f"{AUDIENCE}/mcp.access"):
            _run(m.pruefe_token(_token()))
            self.assertIn("Berechtigung", self._abgewiesen(_token(_claims(scp="User.Read"))))


class Schalter(unittest.TestCase):
    def test_ohne_einrichtung_aus(self):
        with patch.multiple(settings, mcp_idp_oauth_enabled=False, mcp_idp_issuer=ISSUER,
                            mcp_idp_audience=AUDIENCE):
            self.assertFalse(m.aktiv())
            with self.assertRaises(m.IdpTokenFehler):
                _run(m.pruefe_token(_token()))
            self.assertEqual(m.www_authenticate("brains/x"), "Bearer")

    def test_an_aber_ohne_empfaenger_bleibt_aus(self):
        with patch.multiple(settings, mcp_idp_oauth_enabled=True, mcp_idp_issuer=ISSUER, mcp_idp_audience=""):
            self.assertFalse(m.aktiv())

    def test_aussteller_aus_der_verzeichnis_id(self):
        with patch.multiple(settings, mcp_idp_oauth_enabled=True, mcp_idp_issuer="",
                            mcp_idp_audience=AUDIENCE, oauth_microsoft_tenant_id=TENANT):
            self.assertEqual(m.konfig().issuer, ISSUER)
        with patch.multiple(settings, mcp_idp_oauth_enabled=True, mcp_idp_issuer="",
                            mcp_idp_audience=AUDIENCE, oauth_microsoft_tenant_id="common"):
            self.assertIsNone(m.konfig())

    def test_prm_und_www_authenticate(self):
        with _eingerichtet(mcp_idp_required_scope=f"{AUDIENCE}/mcp.access"):
            doc = m.prm_dokument("brains/vertrieb")
            self.assertEqual(doc["resource"], "https://ai.example.com/api/v1/mcp/brains/vertrieb")
            self.assertEqual(doc["authorization_servers"], [ISSUER])
            self.assertEqual(doc["scopes_supported"], [f"{AUDIENCE}/mcp.access"])
            kopf = m.www_authenticate("brains/vertrieb", "invalid_token")
        self.assertTrue(kopf.startswith(
            'Bearer resource_metadata="https://ai.example.com/.well-known/oauth-protected-resource/api/v1/mcp/brains/vertrieb"'))
        self.assertIn('error="invalid_token"', kopf)

    def test_discovery_muss_zum_aussteller_passen(self):
        async def hole(url):
            return {"issuer": "https://boese.example.com", "jwks_uri": JWKS_URI}

        m.cache_leeren()
        with patch.multiple(settings, mcp_idp_oauth_enabled=True, mcp_idp_issuer=ISSUER,
                            mcp_idp_audience=AUDIENCE, mcp_idp_jwks_uri=""), \
                patch.object(m, "_hole_json", hole):
            with self.assertRaises(m.IdpTokenFehler):
                _run(m.pruefe_token(_token()))
        m.cache_leeren()


def _nutzer(**kw):
    werte = {"id": "u1", "email": "person@example.com", "is_active": True, "approved": True,
             "idp_object_id": OID, "role": "member"}
    werte.update(kw)
    return SimpleNamespace(**werte)


class Zuordnung(unittest.TestCase):
    def _zuordnen(self, claims, per_oid=None, per_email=None):
        db = MagicMock(commit=AsyncMock())
        with patch.object(m, "_nutzer_per_oid", AsyncMock(return_value=per_oid)), \
                patch.object(m, "_nutzer_per_email", AsyncMock(return_value=per_email)):
            return _run(m.nutzer_zum_token(db, claims)), db

    def _abgewiesen(self, claims, **kw):
        with self.assertRaises(m.IdpTokenFehler) as ctx:
            self._zuordnen(claims, **kw)
        self.assertEqual(ctx.exception.status, 403)

    def test_bekannte_oid(self):
        with _eingerichtet():
            user, _ = self._zuordnen(_claims(), per_oid=_nutzer())
        self.assertEqual(user.id, "u1")

    def test_unbekannte_person(self):
        with _eingerichtet():
            self._abgewiesen(_claims())

    def test_deaktiviertes_konto(self):
        with _eingerichtet():
            self._abgewiesen(_claims(), per_oid=_nutzer(is_active=False))
            self._abgewiesen(_claims(), per_oid=_nutzer(approved=False))

    def test_email_rueckfall_bindet_oid(self):
        konto = _nutzer(idp_object_id=None)
        with _eingerichtet():
            user, db = self._zuordnen(_claims(), per_email=konto)
        self.assertIs(user, konto)
        self.assertEqual(konto.idp_object_id, OID)
        db.commit.assert_awaited()

    def test_email_rueckfall_nicht_bei_multitenant(self):
        """Der SSO-Login vertraut der Adresse bei ``common`` nicht — hier ebenso."""
        with _eingerichtet(oauth_microsoft_tenant_id="common"):
            self._abgewiesen(_claims(), per_email=_nutzer(idp_object_id=None))

    def test_email_rueckfall_nicht_bei_anderer_oid_am_konto(self):
        with _eingerichtet():
            self._abgewiesen(_claims(), per_email=_nutzer(idp_object_id="ffffffff-0000-0000-0000-000000000000"))

    def test_email_verified_bei_anderem_oidc_anbieter(self):
        claims = _claims(oid=None, tid=None, upn=None, email="person@example.com", email_verified=True)
        with _eingerichtet(oauth_microsoft_tenant_id=""):
            user, _ = self._zuordnen(claims, per_email=_nutzer(idp_object_id=None))
        self.assertEqual(user.id, "u1")
        claims["email_verified"] = False
        with _eingerichtet(oauth_microsoft_tenant_id=""):
            self._abgewiesen(claims, per_email=_nutzer(idp_object_id=None))


class BrainEndpunkt(unittest.IsolatedAsyncioTestCase):
    """Beide Wege enden in derselben Rechteprüfung (``brain_zugriff``)."""

    def _brain(self, **kw):
        from app.core.encryption import encrypt_token

        werte = {"slug": "vertrieb", "name": "Vertrieb", "label": "brain-vertrieb", "is_active": True, "mcp_enabled": True,
                 "mcp_token_encrypted": encrypt_token("brain-schluessel"), "default_mode": "rw"}
        werte.update(kw)
        return SimpleNamespace(**werte)

    def _db(self, brain):
        ergebnis = MagicMock(scalar_one_or_none=MagicMock(return_value=brain))
        return MagicMock(execute=AsyncMock(return_value=ergebnis), commit=AsyncMock())

    async def _auth(self, brain, header, nutzer=None, erlaubt=None, fremd=frozenset()):
        from app.api import brain_mcp

        anfrage = MagicMock(headers={"Authorization": header} if header else {})
        with patch.object(m, "_nutzer_per_oid", AsyncMock(return_value=nutzer)), \
                patch.object(m, "_nutzer_per_email", AsyncMock(return_value=None)), \
                patch("app.core.mounts.freigegebene_mounts", AsyncMock(return_value=erlaubt)), \
                patch("app.core.mounts.fremde_privatablagen", AsyncMock(return_value=set(fremd))):
            try:
                return await brain_mcp._auth_brain("vertrieb", anfrage, self._db(brain))
            except brain_mcp._Abgewiesen as a:
                return a.antwort

    async def test_brain_schluessel_funktioniert_weiter(self):
        brain = self._brain()
        with _eingerichtet():
            ergebnis = await self._auth(brain, "Bearer brain-schluessel")
        self.assertEqual(ergebnis, (brain, "rw"))

    async def test_brain_schluessel_ohne_idp_einrichtung(self):
        brain = self._brain(default_mode="ro")
        with patch.object(settings, "mcp_idp_oauth_enabled", False):
            self.assertEqual(await self._auth(brain, "Bearer brain-schluessel"), (brain, "ro"))

    async def test_ohne_token_401_mit_resource_metadata(self):
        with _eingerichtet():
            antwort = await self._auth(self._brain(), "")
        self.assertEqual(antwort.status_code, 401)
        self.assertIn("resource_metadata=", antwort.headers["WWW-Authenticate"])

    async def test_idp_token_mit_freigabe(self):
        brain = self._brain()
        with _eingerichtet():
            ergebnis = await self._auth(brain, f"Bearer {_token()}", nutzer=_nutzer(),
                                        erlaubt={"brain-vertrieb": "ro"})
        self.assertEqual(ergebnis, (brain, "ro"))

    async def test_idp_token_ohne_freigabe_404(self):
        with _eingerichtet():
            antwort = await self._auth(self._brain(), f"Bearer {_token()}", nutzer=_nutzer(),
                                       erlaubt={"brain-anderes": None})
        self.assertEqual(antwort.status_code, 404)

    async def test_idp_token_fremde_privatablage_auch_fuer_admin_404(self):
        with _eingerichtet():
            antwort = await self._auth(self._brain(), f"Bearer {_token()}", nutzer=_nutzer(role="admin"),
                                       erlaubt=None, fremd={"brain-vertrieb"})
        self.assertEqual(antwort.status_code, 404)

    async def test_idp_token_unbekannte_person_403(self):
        with _eingerichtet(oauth_microsoft_tenant_id="common"):
            antwort = await self._auth(self._brain(), f"Bearer {_token()}", nutzer=None)
        self.assertEqual(antwort.status_code, 403)

    async def test_idp_token_falsches_aud_401(self):
        with _eingerichtet():
            antwort = await self._auth(self._brain(), f"Bearer {_token(_claims(aud='api://andere'))}",
                                       nutzer=_nutzer(), erlaubt=None)
        self.assertEqual(antwort.status_code, 401)
        self.assertIn('error="invalid_token"', antwort.headers["WWW-Authenticate"])

    async def test_idp_token_bei_ausgeschalteter_funktion_401(self):
        with patch.multiple(settings, mcp_idp_oauth_enabled=False):
            antwort = await self._auth(self._brain(), f"Bearer {_token()}", nutzer=_nutzer(), erlaubt=None)
        self.assertEqual(antwort.status_code, 401)
        self.assertEqual(antwort.headers["WWW-Authenticate"], "Bearer")

    async def test_schreiben_im_lesemodus_verweigert(self):
        from app.api import brain_mcp

        res = await brain_mcp._call_tool("brain_write", {"path": "a.md", "content": "x"},
                                         self._brain(), MagicMock(), "ro")
        self.assertTrue(res["isError"])


class AgentEndpunkt(unittest.IsolatedAsyncioTestCase):
    async def _auth(self, header, nutzer=None, zugang=True, webhook_token="webhook-geheim"):
        from fastapi import HTTPException

        from app.api import mcp_agent

        agent = SimpleNamespace(id="a1", webhook_enabled=True, webhook_token=webhook_token)
        db = MagicMock(execute=AsyncMock(return_value=MagicMock(
            scalar_one_or_none=MagicMock(return_value=agent))), commit=AsyncMock())
        zugriff = AsyncMock(return_value=None) if zugang else AsyncMock(
            side_effect=HTTPException(status_code=403, detail="nein"))
        anfrage = MagicMock(headers={"Authorization": header} if header else {})
        with patch.object(m, "_nutzer_per_oid", AsyncMock(return_value=nutzer)), \
                patch("app.dependencies.require_agent_access", zugriff):
            try:
                return await mcp_agent._auth_agent("a1", anfrage, db)
            except HTTPException as e:
                return e

    async def test_webhook_token_weiter_gueltig(self):
        with _eingerichtet():
            self.assertEqual((await self._auth("Bearer webhook-geheim")).id, "a1")

    async def test_idp_token_mit_zugang(self):
        with _eingerichtet():
            self.assertEqual((await self._auth(f"Bearer {_token()}", nutzer=_nutzer())).id, "a1")

    async def test_idp_token_ohne_webhook_token_wenn_aktiv(self):
        with _eingerichtet():
            agent = await self._auth(f"Bearer {_token()}", nutzer=_nutzer(), webhook_token="")
        self.assertEqual(agent.id, "a1")

    async def test_idp_token_ohne_zugang_404(self):
        with _eingerichtet():
            e = await self._auth(f"Bearer {_token()}", nutzer=_nutzer(), zugang=False)
        self.assertEqual(e.status_code, 404)

    async def test_401_traegt_resource_metadata(self):
        with _eingerichtet():
            e = await self._auth("")
        self.assertEqual(e.status_code, 401)
        self.assertIn("/.well-known/oauth-protected-resource/api/v1/mcp/agents/a1", e.headers["WWW-Authenticate"])


class Einstellungen(unittest.TestCase):
    def test_schluessel_sind_speicherbar(self):
        from app.services.settings_service import ALLOWED_KEYS, SECRET_KEYS

        for k in ("mcp_idp_oauth_enabled", "mcp_idp_issuer", "mcp_idp_audience",
                  "mcp_idp_jwks_uri", "mcp_idp_required_scope"):
            self.assertIn(k, ALLOWED_KEYS)
            self.assertNotIn(k, SECRET_KEYS)
            self.assertTrue(hasattr(settings, k))

    def test_standard_aus(self):
        from app.config import Settings

        self.assertFalse(Settings.model_fields["mcp_idp_oauth_enabled"].default)


class SsoLoginMerktObjektId(unittest.TestCase):
    """Der Microsoft-SSO-Login hinterlegt die oid — daran erkennt der MCP-Zugang die Person."""

    def _merke(self, user, oid, anderes=None):
        from app.services.sso_service import SSOService

        db = MagicMock(scalar=AsyncMock(return_value=anderes), commit=AsyncMock())
        _run(SSOService(db=db, redis=None)._merke_objekt_id(user, oid))
        return db

    def test_setzt_wenn_leer(self):
        user = _nutzer(idp_object_id=None)
        self._merke(user, OID.upper())
        self.assertEqual(user.idp_object_id, OID)

    def test_ueberschreibt_keine_andere(self):
        user = _nutzer(idp_object_id="ffffffff-0000-0000-0000-000000000000")
        self._merke(user, OID)
        self.assertEqual(user.idp_object_id, "ffffffff-0000-0000-0000-000000000000")

    def test_nicht_wenn_ein_anderes_konto_sie_traegt(self):
        user = _nutzer(idp_object_id=None)
        self._merke(user, OID, anderes="u2")
        self.assertIsNone(user.idp_object_id)


class WellKnown(unittest.TestCase):
    def _get(self, pfad):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.api.oauth_as import wellknown_router

        app = FastAPI()
        app.include_router(wellknown_router)
        return TestClient(app).get(pfad)

    def test_pfad_variante_fuer_brains_und_agenten(self):
        with _eingerichtet(msgraph_mcp_external_enabled=False):
            r = self._get("/.well-known/oauth-protected-resource/api/v1/mcp/brains/vertrieb")
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["resource"], "https://ai.example.com/api/v1/mcp/brains/vertrieb")
            self.assertEqual(r.json()["authorization_servers"], [ISSUER])
            self.assertEqual(self._get("/.well-known/oauth-protected-resource/api/v1/mcp/agents/a1").status_code, 200)
            self.assertEqual(self._get("/.well-known/oauth-protected-resource/api/v1/mcp/sonstwas/x").status_code, 404)
            wurzel = self._get("/.well-known/oauth-protected-resource")
            self.assertEqual(wurzel.json()["resource"], "https://ai.example.com/api/v1/mcp")

    def test_aus_ohne_einrichtung(self):
        with patch.multiple(settings, mcp_idp_oauth_enabled=False, msgraph_mcp_external_enabled=False):
            self.assertEqual(self._get("/.well-known/oauth-protected-resource/api/v1/mcp/brains/x").status_code, 404)
            self.assertEqual(self._get("/.well-known/oauth-protected-resource").status_code, 404)


class EinstellungsEndpunkt(unittest.TestCase):
    """Einschalten nur vollständig eingerichtet, Adressen nur per https, wirkt sofort."""

    def _client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.api import settings as api
        from app.db.session import get_db
        from app.dependencies import require_admin

        app = FastAPI()
        app.include_router(api.router)
        app.dependency_overrides[require_admin] = lambda: SimpleNamespace(id="admin1")
        app.dependency_overrides[get_db] = lambda: MagicMock(commit=AsyncMock())
        return TestClient(app), api

    def test_validierung_und_wirkung(self):
        client, api = self._client()
        with patch.multiple(settings, mcp_idp_oauth_enabled=False, mcp_idp_issuer="", mcp_idp_audience="",
                            mcp_idp_jwks_uri="", mcp_idp_required_scope="", oauth_microsoft_tenant_id="common"), \
                patch.object(api.SettingsService, "set", AsyncMock()) as gespeichert, \
                patch.object(api, "_einstellungen_protokollieren", AsyncMock()):
            self.assertEqual(client.put("/settings/mcp-idp-oauth", json={"enabled": True}).status_code, 422)
            self.assertEqual(client.put("/settings/mcp-idp-oauth", json={
                "enabled": True, "issuer": "http://login.example.com", "audience": "x"}).status_code, 422)
            r = client.put("/settings/mcp-idp-oauth", json={
                "enabled": True, "issuer": ISSUER, "audience": f" {AUDIENCE} , "})
            self.assertEqual(r.status_code, 200)
            self.assertTrue(r.json()["active"])
            self.assertEqual(settings.mcp_idp_audience, AUDIENCE)
            self.assertTrue(m.aktiv())
            gespeichert.assert_any_await("mcp_idp_oauth_enabled", "true")


if __name__ == "__main__":
    unittest.main()
