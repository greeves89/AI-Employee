"""SSO/OIDC service - handles login via Google, Microsoft, etc.

This is separate from OAuthService which handles external integrations.
SSOService handles USER AUTHENTICATION via external identity providers.
"""

import json
import logging
import secrets
import uuid
from urllib.parse import urlencode

import httpx
import jwt as pyjwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.log_redaction import scrub_log
from app.core.sso_providers import (
    SSOProviderConfig,
    get_sso_client_id,
    get_sso_client_secret,
    get_sso_provider,
)
from app.core.permissions import role_for_new_user
from app.core.sso_group_roles import record_observed_groups, resolve_target
from app.models.user import User, UserRole
from app.services.redis_service import RedisService

logger = logging.getLogger(__name__)

# CSRF state TTL
SSO_STATE_TTL = 600  # 10 minutes

# Entra multi-tenant authority aliases — a login through any of these is NOT
# locked to one org, so the email must NOT be auto-trusted (cross-tenant takeover).
_MS_MULTITENANT_AUTHORITIES = {"common", "organizations", "consumers"}


def resolve_redirect_base_url(request_host: str | None) -> str:
    """Which base URL a login started on THIS host should redirect back to.

    Defaults to the fixed ``oauth_redirect_base_url`` — unchanged behavior for
    every deployment that hasn't opted in. Only when the deployment explicitly
    lists ``request_host`` in ``oauth_redirect_allowed_hosts`` does the login
    use that host's own URL instead, so a customer reachable under more than
    one public hostname (a vanity domain alongside their own) can land back on
    whichever one they actually started from. Not a blind Host-header trust —
    an unlisted host silently falls back to the fixed base URL, never an
    attacker-chosen one; the provider (Entra etc.) is the second gate, since
    it only accepts a redirect_uri it has registered.
    """
    fixed = settings.oauth_redirect_base_url
    allowed = {
        h.strip().lower()
        for h in settings.oauth_redirect_allowed_hosts.split(",")
        if h.strip()
    }
    if not request_host or not allowed:
        return fixed
    host_only = request_host.split(":", 1)[0].lower()
    if host_only not in allowed:
        return fixed
    scheme = "http" if host_only in ("localhost", "127.0.0.1") else "https"
    return f"{scheme}://{request_host}"



def _scopes_for(provider) -> list[str]:
    """Angeforderte Berechtigungen — bei Microsoft die vom Admin freigegebene Auswahl."""
    from app.core.oauth_providers import PROVIDERS, get_provider_scopes, microsoft_scopes
    if getattr(provider, "name", "") == "microsoft":
        return microsoft_scopes()
    integration = PROVIDERS.get(getattr(provider, "name", ""))
    return get_provider_scopes(integration) if integration else list(provider.scopes)


class SSOService:
    def __init__(self, db: AsyncSession, redis: RedisService):
        self.db = db
        self.redis = redis
        # Cache for JWKS keys
        self._jwks_cache: dict[str, dict] = {}

    async def generate_login_url(
        self, provider_name: str, return_to: str | None = None, request_host: str | None = None
    ) -> str:
        """Generate OIDC authorization URL for user login.

        ``return_to`` is an internal path the callback should land on instead of the
        dashboard — used by the MCP authorization endpoint so an OpenWebUI user can
        sign in with Microsoft and be handed straight back to the pending consent,
        without ever seeing an AI-Employee login form. It travels INSIDE the
        server-side state record (never as a query parameter the caller controls),
        and the callback re-validates it before redirecting.

        ``request_host`` is the Host header the login was STARTED on — resolved
        against ``oauth_redirect_allowed_hosts`` (see resolve_redirect_base_url) and
        stored in the state record so the callback rebuilds the EXACT same
        redirect_uri for the token exchange, not a freshly-derived one (OAuth2
        requires the two to match byte-for-byte).
        """
        provider = get_sso_provider(provider_name)
        client_id = get_sso_client_id(provider)
        if not client_id:
            raise ValueError(f"SSO not configured for {provider_name}")

        base_url = resolve_redirect_base_url(request_host)

        # Generate and store CSRF state (+ the optional return target)
        state = secrets.token_urlsafe(32)
        state_key = f"sso:state:{state}"
        payload = json.dumps({
            "provider": provider_name, "return_to": return_to or "", "base_url": base_url,
        })
        await self.redis.client.setex(state_key, SSO_STATE_TTL, payload)

        redirect_uri = f"{base_url}/api/v1/auth/sso/{provider_name}/callback"

        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            # Zur LAUFZEIT auflösen: Die Auswahl des Admins kann sich ändern,
            # ohne dass der Prozess neu startet.
            "scope": " ".join(_scopes_for(provider)),
            "state": state,
            **provider.auth_extra_params,
        }

        from app.core.oauth_providers import apply_tenant
        return f"{apply_tenant(provider.authorization_url)}?{urlencode(params)}"

    async def handle_callback(
        self, provider_name: str, code: str, state: str
    ) -> tuple[User, str]:
        """Handle OIDC callback: verify state, exchange code, find/create user.

        Returns the user plus the ``return_to`` path stored with the state (empty
        string when the login started from the normal login page).
        """
        # Verify CSRF state
        state_key = f"sso:state:{state}"
        stored = await self.redis.client.get(state_key)
        if not stored:
            raise ValueError("Invalid or expired SSO state")
        if isinstance(stored, bytes):
            stored = stored.decode()
        # State records are JSON since the MCP login passthrough; tolerate the bare
        # provider name so logins started before an update still complete.
        try:
            record = json.loads(stored)
            stored_provider = record.get("provider", "")
            return_to = str(record.get("return_to") or "")
            base_url = str(record.get("base_url") or settings.oauth_redirect_base_url)
        except (ValueError, AttributeError):
            stored_provider, return_to = stored, ""
            base_url = settings.oauth_redirect_base_url
        if stored_provider != provider_name:
            raise ValueError("SSO state mismatch")
        await self.redis.client.delete(state_key)

        provider = get_sso_provider(provider_name)

        # Exchange code for tokens — redirect_uri MUST be byte-for-byte identical
        # to the one sent in generate_login_url's authorize request (OAuth2 spec),
        # so this uses the base_url carried in the state, never re-derives it.
        token_data = await self._exchange_code(provider, code, base_url)
        id_token_raw = token_data.get("id_token")
        access_token = token_data.get("access_token")

        # Get user info - prefer userinfo endpoint for reliability
        user_info = await self._get_userinfo(provider, access_token)

        # Also try to decode ID token for the subject identifier
        sub = user_info.get("sub")
        email = user_info.get("email")
        name = user_info.get("name", "")
        email_verified = user_info.get("email_verified", False)

        # For Microsoft, email might be in 'mail' or 'userPrincipalName'
        if not email and provider_name == "microsoft":
            email = user_info.get("mail") or user_info.get("userPrincipalName", "")

        if not email:
            raise ValueError("SSO provider did not return an email address")
        if not sub:
            # Fallback: use email as subject if no sub claim
            sub = email

        # Google always returns verified emails; Microsoft depends on tenant
        # For security, we require email verification for account linking.
        if provider_name == "google":
            email_verified = True
        elif provider_name == "microsoft":
            # Graph /me returns no email_verified claim. Only trust the email when
            # the app is locked to a SPECIFIC tenant (GUID or verified domain):
            # then only that org's authoritative accounts can authenticate, so the
            # email is effectively verified. For ANY of Entra's multi-tenant
            # authority aliases we do NOT trust it — that would allow cross-tenant
            # account-takeover by email match.
            tenant = (settings.oauth_microsoft_tenant_id or "common").strip().lower()
            if tenant and tenant not in _MS_MULTITENANT_AUTHORITIES:
                email_verified = True

        # Find or create user
        user = await self._find_or_create_user(
            provider_name=provider_name,
            subject=sub,
            email=email,
            name=name or email.split("@")[0],
            email_verified=email_verified,
        )

        # Gruppen des Nutzers lesen und die Rolle danach ausrichten — bei jedem
        # Login neu, nicht nur beim ersten (wechselt jemand in Entra die Abteilung,
        # zieht die Berechtigung hier nach). Nur Microsoft: Google-Gruppen brauchen
        # Workspace-Admin-SDK-Rechte, die nichts mit diesem Login zu tun haben.
        if provider_name == "microsoft" and access_token:
            groups = await self._fetch_microsoft_groups(access_token)
            await self.apply_group_role(user, provider_name, groups)

        # Unified login: when the provider also returned Graph tokens (login now
        # requests the full Graph scopes + offline_access), persist them so MS
        # Graph is usable right after login — no separate "connect M365" step.
        # Reuses OAuthService storage so tokens live in exactly one place.
        from app.models.oauth_integration import PER_USER_PROVIDERS
        if provider_name in PER_USER_PROVIDERS and token_data.get("refresh_token"):
            try:
                from app.services.oauth_service import OAuthService
                await OAuthService(self.db, self.redis).persist_tokens(
                    provider_name, user.id, token_data
                )
                logger.info("Stored %s Graph tokens for user %s during login",
                            scrub_log(provider_name), scrub_log(email))
            except Exception as e:
                logger.warning("Could not persist %s Graph tokens during login: %s",
                               scrub_log(provider_name), scrub_log(e))

        return user, return_to

    async def _exchange_code(
        self, provider: SSOProviderConfig, code: str, base_url: str | None = None
    ) -> dict:
        """Exchange authorization code for tokens.

        ``base_url`` must be the SAME one sent in the original authorize request
        (generate_login_url) — the provider rejects a mismatched redirect_uri.
        Defaults to the fixed setting so any other caller keeps working unchanged.
        """
        from app.core.oauth_providers import apply_tenant
        client_id = get_sso_client_id(provider)
        client_secret = get_sso_client_secret(provider)
        redirect_uri = f"{base_url or settings.oauth_redirect_base_url}/api/v1/auth/sso/{provider.name}/callback"

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                apply_tenant(provider.token_url),
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": redirect_uri,
                },
                headers={"Accept": "application/json"},
            )

            if resp.status_code != 200:
                logger.error(f"SSO token exchange failed: {resp.status_code} {resp.text}")
                raise ValueError(f"Token exchange failed: {resp.status_code}")

            return resp.json()

    async def _get_userinfo(
        self, provider: SSOProviderConfig, access_token: str
    ) -> dict:
        """Fetch user info from the provider's userinfo endpoint."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                provider.userinfo_url,
                headers={"Authorization": f"Bearer {access_token}"},
            )

            if resp.status_code != 200:
                logger.error(f"SSO userinfo failed: {resp.status_code} {resp.text}")
                raise ValueError("Failed to fetch user info from SSO provider")

            return resp.json()

    async def apply_group_role(self, user: User, provider: str, groups: list[str]) -> bool:
        """Rolle (oder CustomRole) aus den Gruppen des Identitaetsanbieters setzen.

        Gibt zurueck, ob sich etwas geaendert hat. Zuerst wird IMMER festgehalten,
        welche Gruppen gesehen wurden (``record_observed_groups``) — unabhaengig
        davon, ob eine Zuordnung dafuer existiert. Das ist die Grundlage, auf der die
        Verwaltung Gruppen zum Anklicken statt zum Abtippen anbietet.

        Drei bewusste Entscheidungen, unveraendert aus dem SAML-only Vorgaenger:

        * **Kein Treffer aendert nichts.** Eine leere oder unpassende Zuordnung darf
          niemandem Rechte wegnehmen, die ein Mensch von Hand vergeben hat.
        * **Die hoechst priorisierte Zuordnung gewinnt** (siehe
          ``sso_group_roles.resolve_target``).
        * **Der letzte Administrator bleibt Administrator.** Ihn ueber eine
          Gruppenzuordnung herabzustufen wuerde die Plattform aussperren — egal ob
          das neue Ziel eine feste Rolle oder eine CustomRole ist.
        """
        await record_observed_groups(self.db, provider, groups or [])

        target = await resolve_target(self.db, provider, groups or [])
        if target is None:
            await self.db.commit()  # die Beobachtung soll trotzdem stehen bleiben
            return False
        kind, value = target

        if kind == "custom_role":
            try:
                new_custom_role_id = int(value)
            except (TypeError, ValueError):
                logger.warning("SSO-Gruppenzuordnung zeigt auf eine ungueltige CustomRole-ID: %s", value)
                await self.db.commit()
                return False
            from app.models.custom_role import CustomRole
            if not await self.db.get(CustomRole, new_custom_role_id):
                # Die Zuordnung zeigt auf eine inzwischen geloeschte CustomRole. Faellt
                # sonst still auf die Mitglied-Vorgaben zurueck (get_effective_permissions
                # tut das ohnehin) — hier wird es wenigstens sichtbar geloggt, statt dass
                # ein Administrator raetselt, warum jemand ploetzlich weniger darf.
                logger.warning(
                    "SSO-Gruppenzuordnung zeigt auf eine geloeschte CustomRole (%s) — uebersprungen",
                    new_custom_role_id,
                )
                await self.db.commit()
                return False
            new_role = UserRole.MEMBER  # Basis-Rolle; die CustomRole traegt die eigentlichen Rechte
            unchanged = user.role == new_role and user.custom_role_id == new_custom_role_id
        else:
            new_role = {"admin": UserRole.ADMIN, "manager": UserRole.MANAGER,
                        "member": UserRole.MEMBER}.get(value)
            new_custom_role_id = None
            if new_role is None:
                await self.db.commit()
                return False
            unchanged = user.role == new_role and user.custom_role_id is None

        if unchanged:
            await self.db.commit()
            return False

        if user.role == UserRole.ADMIN and new_role != UserRole.ADMIN:
            # FOR UPDATE sperrt die aktiven Administrator-Zeilen: zwei gleichzeitige
            # Logins, die durch dieselbe IdP-Gruppenaenderung beide den letzten
            # verbliebenen Administrator herabstufen wollen, duerfen sich nicht beide
            # auf denselben (dann veralteten) Zaehlerstand verlassen — der zweite
            # muss auf den ersten warten und sieht danach die aktuelle Zahl. Ohne die
            # Sperre koennten zwei parallele Anmeldungen beide durchkommen und die
            # Plattform waere ohne jeden Administrator (TOCTOU).
            #
            # Nur AKTIVE Administratoren zaehlen: ein deaktiviertes Admin-Konto (z.B.
            # beim Offboarding abgeschaltet, aber nie auf eine andere Rolle gesetzt)
            # kann sich nicht mehr anmelden und faengt niemanden auf — es als Schutz
            # mitzuzaehlen waere ein Lockout, der sich als Schutz tarnt.
            active_admin_ids = (await self.db.execute(
                select(User.id)
                .where(User.role == UserRole.ADMIN, User.is_active.is_(True))
                .with_for_update()
            )).scalars().all()
            if len(active_admin_ids) <= 1:
                logger.warning(
                    "Gruppenzuordnung wuerde den letzten aktiven Administrator herabstufen — uebersprungen"
                )
                await self.db.commit()
                return False

        previous = user.role
        user.role = new_role
        user.custom_role_id = new_custom_role_id
        await self.db.commit()
        logger.info(
            "Rolle aus IdP-Gruppen gesetzt: %s %s -> %s%s",
            scrub_log(user.email), previous.value, new_role.value,
            f" (CustomRole {new_custom_role_id})" if new_custom_role_id else "",
        )
        return True

    async def _fetch_microsoft_groups(self, access_token: str) -> list[str]:
        """Die eigenen Gruppen des angemeldeten Nutzers via Microsoft Graph.

        ``User.Read`` — das ohnehin schon angeforderte Pflicht-Scope des
        Microsoft-Logins (siehe ``MICROSOFT_REQUIRED_SCOPES``) — ist laut
        Microsofts eigener Referenz die geringste noetige Berechtigung fuer
        ``GET /me/memberOf``. Es braucht also KEINE zusaetzliche, admin-
        genehmigungspflichtige Berechtigung (``GroupMember.Read.All`` o.ae.) — genau
        das war bisher der Grund, Gruppen NICHT anzufragen.

        Schlaegt der Aufruf fehl, bleibt die Rolle einfach unveraendert. Ein
        SSO-Login darf nicht daran scheitern, dass Graph gerade nicht antwortet.
        """
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    "https://graph.microsoft.com/v1.0/me/memberOf?$select=displayName",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                if resp.status_code != 200:
                    logger.warning(
                        "Microsoft-Gruppen konnten nicht gelesen werden: %s", resp.status_code
                    )
                    return []
                data = resp.json()
        except Exception as e:  # noqa: BLE001
            logger.warning("Microsoft-Gruppen konnten nicht gelesen werden: %s", e)
            return []
        return [g["displayName"] for g in data.get("value", []) if g.get("displayName")]

    async def _find_or_create_user(
        self,
        provider_name: str,
        subject: str,
        email: str,
        name: str,
        email_verified: bool,
    ) -> User:
        """Find existing user by SSO identity or email, or create new user."""
        # 1. Try to find by SSO identity (provider + subject)
        user = await self.db.scalar(
            select(User).where(
                User.sso_provider == provider_name,
                User.sso_subject == subject,
            )
        )
        if user:
            if not user.is_active:
                raise ValueError("Account is deactivated")
            return user

        # 2. Try to find by email (account linking)
        user = await self.db.scalar(
            select(User).where(User.email == email)
        )
        if user:
            if not user.is_active:
                raise ValueError("Account is deactivated")
            # Link SSO identity to existing account
            # Only if email is verified by the provider (prevents account takeover)
            if email_verified:
                user.sso_provider = provider_name
                user.sso_subject = subject
                await self.db.commit()
                logger.info(f"SSO linked {scrub_log(provider_name)} to existing user {scrub_log(email)}")
            else:
                logger.warning(
                    f"SSO email not verified for {email}, skipping account link"
                )
            return user

        # 3. Check if registration is open
        from sqlalchemy import func
        user_count = await self.db.scalar(select(func.count()).select_from(User))
        is_first = user_count == 0

        if not is_first and not settings.registration_open:
            raise ValueError("Registration is closed. Contact an admin for access.")

        # 4. Create new user. When admin-approval is required, non-first users land
        # in pending (approved=False) and must be unlocked by an admin. The first user
        # (auto-admin) is always approved so the platform is never locked out.
        approved = is_first or not settings.require_user_approval
        user = User(
            id=uuid.uuid4().hex[:12],
            email=email,
            name=name,
            password_hash=None,  # SSO users don't have a password
            role=role_for_new_user(is_first),
            approved=approved,
            sso_provider=provider_name,
            sso_subject=subject,
        )
        self.db.add(user)
        await self.db.commit()
        await self.db.refresh(user)

        logger.info(
            f"SSO user created: {scrub_log(email)} via {scrub_log(provider_name)} "
            f"(role: {user.role.value}, first: {is_first}, approved: {approved})"
        )
        return user
