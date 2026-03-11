# SAML 2.0 & LDAP Integration Guide

This guide covers adding enterprise SSO via SAML 2.0 and LDAP/Active Directory user sync
to the AI Employee Platform.

---

## Overview

The platform currently supports OIDC SSO (Google, Microsoft). For enterprise customers
requiring SAML 2.0 or LDAP, the following integration points are available.

| Protocol | Use Case | Dependency |
|----------|----------|------------|
| SAML 2.0 | SSO with corporate IdP (Okta, ADFS, Azure AD) | `python3-saml` |
| LDAP | User sync from Active Directory / OpenLDAP | `ldap3` |

---

## Part 1: SAML 2.0 Integration

### 1.1 Install Dependencies

```bash
pip install python3-saml>=1.15
# Requires libxml2 and xmlsec1 system libs:
apt-get install libxml2-dev libxmlsec1-dev libxmlsec1-openssl
```

Add to `orchestrator/pyproject.toml`:
```toml
"python3-saml>=1.15",
```

### 1.2 Configuration

Add to `orchestrator/app/config.py` (Settings class):

```python
# SAML
saml_enabled: bool = False
saml_idp_entity_id: str = ""
saml_idp_sso_url: str = ""           # IdP Single Sign-On URL
saml_idp_slo_url: str = ""           # IdP Single Logout URL (optional)
saml_idp_x509_cert: str = ""         # IdP signing certificate (PEM, no headers)
saml_sp_entity_id: str = ""          # Your SP Entity ID (e.g. https://your-domain.com)
saml_sp_acs_url: str = ""            # Assertion Consumer Service URL
saml_sp_x509_cert: str = ""          # SP signing cert (optional, for signed requests)
saml_sp_private_key: str = ""        # SP private key (optional)
saml_attribute_email: str = "email"  # SAML attribute containing user email
saml_attribute_name: str = "displayName"
saml_default_role: str = "member"    # Role assigned to new SAML users
```

### 1.3 SAML Settings Builder

Create `orchestrator/app/core/saml_settings.py`:

```python
"""Build python3-saml settings dict from app config."""

from app.config import settings


def get_saml_settings() -> dict:
    """Return python3-saml settings dictionary."""
    sp_cert = settings.saml_sp_x509_cert.strip()
    sp_key = settings.saml_sp_private_key.strip()

    return {
        "strict": True,
        "debug": False,
        "sp": {
            "entityId": settings.saml_sp_entity_id,
            "assertionConsumerService": {
                "url": settings.saml_sp_acs_url,
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST",
            },
            "singleLogoutService": {
                "url": settings.saml_sp_acs_url.replace("/acs", "/slo"),
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
            },
            "x509cert": sp_cert,
            "privateKey": sp_key,
        },
        "idp": {
            "entityId": settings.saml_idp_entity_id,
            "singleSignOnService": {
                "url": settings.saml_idp_sso_url,
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
            },
            "singleLogoutService": {
                "url": settings.saml_idp_slo_url,
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
            },
            "x509cert": settings.saml_idp_x509_cert,
        },
        "security": {
            "nameIdEncrypted": False,
            "authnRequestsSigned": bool(sp_key),
            "logoutRequestSigned": bool(sp_key),
            "logoutResponseSigned": bool(sp_key),
            "signMetadata": bool(sp_cert and sp_key),
            "wantMessagesSigned": False,
            "wantAssertionsSigned": True,
            "wantAttributeStatement": True,
            "wantNameId": True,
            "wantNameIdEncrypted": False,
            "wantAssertionsEncrypted": False,
            "signatureAlgorithm": "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256",
            "digestAlgorithm": "http://www.w3.org/2001/04/xmlenc#sha256",
        },
    }
```

### 1.4 API Endpoints

Create `orchestrator/app/api/saml.py`:

```python
"""SAML 2.0 SSO endpoints."""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.auth import create_access_token, create_refresh_token
from app.db.session import get_db
from app.models.user import User, UserRole

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/saml", tags=["saml"])

COOKIE_OPTS = {"httponly": True, "samesite": "lax", "secure": True, "path": "/"}


def _get_auth():
    """Lazy import to avoid import errors when python3-saml is not installed."""
    try:
        from onelogin.saml2.auth import OneLogin_Saml2_Auth
        return OneLogin_Saml2_Auth
    except ImportError:
        raise HTTPException(
            status_code=501,
            detail="SAML not available. Install python3-saml: pip install python3-saml",
        )


def _prepare_request(request: Request, body: bytes = b"") -> dict:
    """Convert FastAPI request to python3-saml format."""
    return {
        "https": "on" if request.url.scheme == "https" else "off",
        "http_host": request.headers.get("host", request.url.netloc),
        "script_name": request.url.path,
        "server_port": str(request.url.port or (443 if request.url.scheme == "https" else 80)),
        "get_data": dict(request.query_params),
        "post_data": {},  # Populated from body in ACS endpoint
    }


@router.get("/metadata")
async def saml_metadata(request: Request):
    """Serve SP metadata XML for IdP registration."""
    if not settings.saml_enabled:
        raise HTTPException(status_code=404, detail="SAML not enabled")

    from onelogin.saml2.settings import OneLogin_Saml2_Settings
    from app.core.saml_settings import get_saml_settings

    saml_settings = OneLogin_Saml2_Settings(get_saml_settings(), sp_validation_only=True)
    metadata = saml_settings.get_sp_metadata()
    errors = saml_settings.validate_metadata(metadata)

    if errors:
        raise HTTPException(status_code=500, detail=f"Metadata errors: {errors}")

    return Response(content=metadata, media_type="application/xml")


@router.get("/login")
async def saml_login(request: Request):
    """Redirect user to IdP for SAML authentication."""
    if not settings.saml_enabled:
        raise HTTPException(status_code=404, detail="SAML not enabled")

    OneLogin_Saml2_Auth = _get_auth()
    from app.core.saml_settings import get_saml_settings

    req = _prepare_request(request)
    auth = OneLogin_Saml2_Auth(req, get_saml_settings())
    login_url = auth.login()
    return RedirectResponse(url=login_url, status_code=302)


@router.post("/acs")
async def saml_acs(request: Request, db: AsyncSession = Depends(get_db)):
    """Assertion Consumer Service - handle IdP POST response."""
    if not settings.saml_enabled:
        raise HTTPException(status_code=404, detail="SAML not enabled")

    OneLogin_Saml2_Auth = _get_auth()
    from app.core.saml_settings import get_saml_settings

    body = await request.body()
    req = _prepare_request(request)

    # Parse POST form data
    import urllib.parse
    post_data = dict(urllib.parse.parse_qsl(body.decode("utf-8")))
    req["post_data"] = post_data

    auth = OneLogin_Saml2_Auth(req, get_saml_settings())
    auth.process_response()
    errors = auth.get_errors()

    if errors:
        logger.warning("SAML ACS errors: %s - %s", errors, auth.get_last_error_reason())
        frontend_url = settings.oauth_redirect_base_url
        return RedirectResponse(
            url=f"{frontend_url}/login?error=saml_auth_failed",
            status_code=302,
        )

    if not auth.is_authenticated():
        raise HTTPException(status_code=401, detail="SAML authentication failed")

    # Extract user attributes
    attrs = auth.get_attributes()
    name_id = auth.get_nameid()

    email = _extract_attr(attrs, settings.saml_attribute_email) or name_id
    name = _extract_attr(attrs, settings.saml_attribute_name) or email.split("@")[0]

    if not email:
        raise HTTPException(status_code=400, detail="SAML response missing email attribute")

    # Find or create user
    user = await db.scalar(select(User).where(User.email == email))
    if not user:
        user = User(
            id=uuid.uuid4().hex[:12],
            email=email,
            name=name,
            role=UserRole(settings.saml_default_role),
            sso_provider="saml",
            sso_subject=name_id,
        )
        db.add(user)
        logger.info("SAML: created new user %s", email)
    elif not user.is_active:
        raise HTTPException(status_code=403, detail="Account is deactivated")

    await db.commit()

    # Issue JWT cookies and redirect
    frontend_url = settings.oauth_redirect_base_url
    redirect = RedirectResponse(url=f"{frontend_url}/dashboard", status_code=302)
    access = create_access_token(user.id, user.role.value)
    refresh = create_refresh_token(user.id)
    redirect.set_cookie("access_token", access, max_age=1800, **COOKIE_OPTS)
    redirect.set_cookie("refresh_token", refresh, max_age=604800, **COOKIE_OPTS)

    logger.info("SAML login: %s", email)
    return redirect


def _extract_attr(attrs: dict, key: str) -> str | None:
    """Extract first value of a SAML attribute."""
    values = attrs.get(key, [])
    return values[0] if values else None
```

Register in `orchestrator/app/api/router.py`:
```python
from app.api import saml
api_router.include_router(saml.router)
```

### 1.5 IdP Configuration Examples

#### Okta
1. Create a new SAML 2.0 app in Okta Admin Console
2. Set **Single sign on URL**: `https://your-domain.com/api/v1/saml/acs`
3. Set **Audience URI (SP Entity ID)**: `https://your-domain.com`
4. Attribute statements: `email` → `user.email`, `displayName` → `user.displayName`
5. Download IdP metadata, extract `entityId`, `SSO URL`, and `x509cert`

#### Azure AD / Entra ID
1. Enterprise Applications → New application → Create your own
2. Single sign-on → SAML
3. Basic SAML Configuration:
   - Identifier: `https://your-domain.com`
   - Reply URL: `https://your-domain.com/api/v1/saml/acs`
4. Copy `Login URL`, `Azure AD Identifier`, and `Certificate (Base64)`

#### ADFS
```
Relying Party Trust Identifier: https://your-domain.com
SAML 2.0 SSO service URL: https://your-domain.com/api/v1/saml/acs
```

---

## Part 2: LDAP / Active Directory Integration

### 2.1 Install Dependencies

```bash
pip install ldap3>=2.9
```

Add to `orchestrator/pyproject.toml`:
```toml
"ldap3>=2.9",
```

### 2.2 Configuration

Add to `orchestrator/app/config.py`:

```python
# LDAP
ldap_enabled: bool = False
ldap_server: str = ""                     # e.g. "ldap://dc.example.com:389"
ldap_use_ssl: bool = False                # Use LDAPS (port 636)
ldap_bind_dn: str = ""                    # Service account DN
ldap_bind_password: str = ""             # Service account password
ldap_base_dn: str = ""                    # e.g. "DC=example,DC=com"
ldap_user_filter: str = "(objectClass=person)"
ldap_attr_email: str = "mail"
ldap_attr_name: str = "displayName"
ldap_attr_groups: str = "memberOf"
ldap_admin_group_dn: str = ""            # DN of group that gets admin role
ldap_sync_interval_hours: int = 24       # How often to sync users
```

### 2.3 LDAP Service

Create `orchestrator/app/services/ldap_service.py`:

```python
"""LDAP/Active Directory user sync service."""

import logging

from app.config import settings

logger = logging.getLogger(__name__)


class LDAPService:
    """Sync users from LDAP/Active Directory."""

    def get_connection(self):
        """Create an ldap3 connection."""
        try:
            import ldap3
        except ImportError:
            raise RuntimeError("ldap3 not installed. Run: pip install ldap3")

        server = ldap3.Server(
            settings.ldap_server,
            use_ssl=settings.ldap_use_ssl,
            get_info=ldap3.ALL,
        )
        conn = ldap3.Connection(
            server,
            user=settings.ldap_bind_dn,
            password=settings.ldap_bind_password,
            auto_bind=True,
            authentication=ldap3.SIMPLE,
        )
        return conn

    def authenticate(self, username: str, password: str) -> dict | None:
        """
        Authenticate a user against LDAP.
        Returns user attributes dict on success, None on failure.
        """
        import ldap3

        conn = self.get_connection()

        # Search for the user
        conn.search(
            settings.ldap_base_dn,
            f"({settings.ldap_attr_email}={username})",
            attributes=[
                settings.ldap_attr_email,
                settings.ldap_attr_name,
                settings.ldap_attr_groups,
            ],
        )

        if not conn.entries:
            logger.debug("LDAP: user not found: %s", username)
            return None

        entry = conn.entries[0]
        user_dn = entry.entry_dn

        # Verify password by attempting to bind as the user
        try:
            user_conn = ldap3.Connection(
                conn.server,
                user=user_dn,
                password=password,
                auto_bind=True,
            )
            user_conn.unbind()
        except ldap3.core.exceptions.LDAPBindError:
            logger.debug("LDAP: invalid password for %s", username)
            return None

        groups = list(entry[settings.ldap_attr_groups].values) if settings.ldap_attr_groups in entry else []

        return {
            "email": str(entry[settings.ldap_attr_email]),
            "name": str(entry[settings.ldap_attr_name]),
            "dn": user_dn,
            "groups": groups,
            "is_admin": settings.ldap_admin_group_dn in groups if settings.ldap_admin_group_dn else False,
        }

    def sync_users(self) -> list[dict]:
        """
        Fetch all users from LDAP for bulk sync.
        Returns list of user attribute dicts.
        """
        conn = self.get_connection()
        conn.search(
            settings.ldap_base_dn,
            settings.ldap_user_filter,
            attributes=[
                settings.ldap_attr_email,
                settings.ldap_attr_name,
                settings.ldap_attr_groups,
            ],
        )

        users = []
        for entry in conn.entries:
            email = str(entry[settings.ldap_attr_email]) if settings.ldap_attr_email in entry else None
            if not email:
                continue
            groups = list(entry[settings.ldap_attr_groups].values) if settings.ldap_attr_groups in entry else []
            users.append({
                "email": email,
                "name": str(entry[settings.ldap_attr_name]) if settings.ldap_attr_name in entry else email,
                "dn": entry.entry_dn,
                "groups": groups,
                "is_admin": settings.ldap_admin_group_dn in groups if settings.ldap_admin_group_dn else False,
            })

        conn.unbind()
        logger.info("LDAP sync: found %d users", len(users))
        return users
```

### 2.4 LDAP Login Endpoint

Add to `orchestrator/app/api/auth.py`:

```python
@router.post("/ldap/login")
async def ldap_login(body: LoginRequest, response: Response, db: AsyncSession = Depends(get_db)):
    """Authenticate via LDAP/Active Directory."""
    if not settings.ldap_enabled:
        raise HTTPException(status_code=404, detail="LDAP authentication not enabled")

    from app.services.ldap_service import LDAPService
    import uuid as _uuid

    ldap = LDAPService()
    try:
        ldap_user = ldap.authenticate(body.email, body.password)
    except Exception as e:
        logger.error("LDAP error: %s", e)
        raise HTTPException(status_code=503, detail="LDAP server unavailable")

    if not ldap_user:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # Find or create user in local DB
    user = await db.scalar(select(User).where(User.email == ldap_user["email"]))
    if not user:
        user = User(
            id=_uuid.uuid4().hex[:12],
            email=ldap_user["email"],
            name=ldap_user["name"],
            role=UserRole.ADMIN if ldap_user["is_admin"] else UserRole.MEMBER,
            sso_provider="ldap",
            sso_subject=ldap_user["dn"],
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
    elif not user.is_active:
        raise HTTPException(status_code=403, detail="Account is deactivated")

    tokens = _set_auth_cookies(response, user)
    return {"user": UserResponse.model_validate(user).model_dump(), **tokens}
```

### 2.5 LDAP User Sync (Scheduled)

Add an admin endpoint or background task to periodically sync LDAP users:

```python
# In orchestrator/app/api/admin.py
@router.post("/ldap/sync")
async def sync_ldap_users(_admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Manually trigger LDAP user sync."""
    if not settings.ldap_enabled:
        raise HTTPException(status_code=404, detail="LDAP not enabled")

    from app.services.ldap_service import LDAPService
    import uuid as _uuid

    ldap = LDAPService()
    ldap_users = ldap.sync_users()
    created = 0
    updated = 0

    for lu in ldap_users:
        user = await db.scalar(select(User).where(User.email == lu["email"]))
        if not user:
            user = User(
                id=_uuid.uuid4().hex[:12],
                email=lu["email"],
                name=lu["name"],
                role=UserRole.ADMIN if lu["is_admin"] else UserRole.MEMBER,
                sso_provider="ldap",
                sso_subject=lu["dn"],
            )
            db.add(user)
            created += 1
        else:
            user.name = lu["name"]
            updated += 1

    await db.commit()
    return {"synced": len(ldap_users), "created": created, "updated": updated}
```

---

## Environment Variables Summary

```bash
# SAML
SAML_ENABLED=true
SAML_IDP_ENTITY_ID=https://idp.example.com
SAML_IDP_SSO_URL=https://idp.example.com/sso/saml
SAML_IDP_X509_CERT=MIICxDCCAaygAwIBAgIBATAN...
SAML_SP_ENTITY_ID=https://your-domain.com
SAML_SP_ACS_URL=https://your-domain.com/api/v1/saml/acs
SAML_DEFAULT_ROLE=member

# LDAP
LDAP_ENABLED=true
LDAP_SERVER=ldap://dc.example.com:389
LDAP_BIND_DN=CN=svc-ai-employee,OU=ServiceAccounts,DC=example,DC=com
LDAP_BIND_PASSWORD=your-service-account-password
LDAP_BASE_DN=DC=example,DC=com
LDAP_ADMIN_GROUP_DN=CN=AI-Admins,OU=Groups,DC=example,DC=com
```

---

## Testing

```bash
# Test SAML metadata endpoint
curl https://your-domain.com/api/v1/saml/metadata

# Test LDAP connectivity
python3 -c "
from app.services.ldap_service import LDAPService
svc = LDAPService()
conn = svc.get_connection()
print('LDAP connected:', conn.bound)
conn.unbind()
"

# Test LDAP auth
curl -X POST https://your-domain.com/api/v1/auth/ldap/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"user@example.com","password":"pass"}'
```
