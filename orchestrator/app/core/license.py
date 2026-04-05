"""License verification for Enterprise features.

Uses Ed25519 signed licenses issued by the AI-Employee License Server.
Public key is embedded here — the corresponding private key lives ONLY on
the license server operated by the copyright holder.

A license is a JSON payload with:
  - tier: "community" | "team" | "business" | "enterprise"
  - features: list of enabled feature flags
  - issued_to: customer identifier (email/company)
  - issued_at: ISO timestamp
  - expires_at: ISO timestamp (or null for perpetual)
  - license_id: unique identifier
  - instance_limit: max concurrent instances (0 = unlimited)

The signature is appended as ".base64signature" using Ed25519.
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


# Public key of the AI-Employee License Server.
# The corresponding private key is held ONLY by the copyright holder.
# Licenses that do not verify against this key are rejected.
LICENSE_SERVER_PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAcOG4DQqoWlo1D+/U7SFiuDjm5/KUVKJYFGci3r8haEI=
-----END PUBLIC KEY-----
"""


# Features available in each tier. The community tier is always active.
COMMUNITY_FEATURES = frozenset({
    "multi_agent",
    "docker_isolation",
    "memory",
    "knowledge_base",
    "approval_rules",
    "meeting_rooms",
    "telegram_bot",
    "templates",
    "scheduler",
    "webhooks",
    "mcp_servers",
    "self_improvement",
    "rbac_basic",  # 4 roles, single-tenant
})

TEAM_FEATURES = COMMUNITY_FEATURES | frozenset({
    "priority_support",
})

BUSINESS_FEATURES = TEAM_FEATURES | frozenset({
    "sso_google",
    "sso_microsoft",
    "sso_apple",
    "advanced_analytics",
    "custom_branding",
})

ENTERPRISE_FEATURES = BUSINESS_FEATURES | frozenset({
    "sso_saml",
    "sso_okta",
    "sso_ldap",
    "scim_provisioning",
    "audit_log_immutable",
    "high_availability",
    "multi_tenant",
    "api_rate_limit_per_org",
    "data_residency_eu",
    "sla_support",
    "white_label",
})


@dataclass
class License:
    tier: str = "community"
    features: frozenset[str] = field(default_factory=lambda: COMMUNITY_FEATURES)
    issued_to: str = "community"
    issued_at: str | None = None
    expires_at: str | None = None
    license_id: str | None = None
    instance_limit: int = 0
    valid: bool = True
    error: str | None = None

    @property
    def is_expired(self) -> bool:
        if not self.expires_at:
            return False
        try:
            exp = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
            return exp < datetime.now(timezone.utc)
        except Exception:
            return True

    def has_feature(self, feature: str) -> bool:
        """Check if a feature is enabled by this license."""
        if not self.valid or self.is_expired:
            # Fall back to community features if license is invalid/expired
            return feature in COMMUNITY_FEATURES
        return feature in self.features

    def to_dict(self) -> dict:
        return {
            "tier": self.tier,
            "issued_to": self.issued_to,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "license_id": self.license_id,
            "instance_limit": self.instance_limit,
            "valid": self.valid,
            "is_expired": self.is_expired,
            "error": self.error,
            "features": sorted(self.features),
        }


def _get_features_for_tier(tier: str) -> frozenset[str]:
    return {
        "community": COMMUNITY_FEATURES,
        "team": TEAM_FEATURES,
        "business": BUSINESS_FEATURES,
        "enterprise": ENTERPRISE_FEATURES,
    }.get(tier, COMMUNITY_FEATURES)


def _community_license() -> License:
    """Default license — everyone gets the community tier, always."""
    return License(
        tier="community",
        features=COMMUNITY_FEATURES,
        issued_to="community",
        license_id="community-default",
    )


def verify_license(license_string: str) -> License:
    """Verify a license token and return a License object.

    Format: `<base64url(payload_json)>.<base64url(signature)>`

    Returns a License with valid=False if verification fails — but never throws.
    Callers must check `license.valid` and `license.has_feature()`.
    """
    if not license_string or not license_string.strip():
        return _community_license()

    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        from cryptography.hazmat.primitives import serialization
        from cryptography.exceptions import InvalidSignature
    except ImportError:
        logger.warning("cryptography library not installed — using community tier")
        return _community_license()

    try:
        parts = license_string.strip().split(".")
        if len(parts) != 2:
            return License(valid=False, error="Malformed license token")

        payload_b64, signature_b64 = parts
        # Pad base64 for decoding
        payload_bytes = base64.urlsafe_b64decode(payload_b64 + "=" * (4 - len(payload_b64) % 4))
        signature_bytes = base64.urlsafe_b64decode(signature_b64 + "=" * (4 - len(signature_b64) % 4))

        # Verify signature against the embedded public key
        public_key = serialization.load_pem_public_key(LICENSE_SERVER_PUBLIC_KEY.encode())
        if not isinstance(public_key, Ed25519PublicKey):
            return License(valid=False, error="Public key is not Ed25519")

        try:
            public_key.verify(signature_bytes, payload_bytes)
        except InvalidSignature:
            return License(valid=False, error="Invalid signature — license tampered or not issued by official server")

        # Parse payload
        payload = json.loads(payload_bytes.decode())
        tier = payload.get("tier", "community")
        features_from_payload = payload.get("features")
        if features_from_payload is not None:
            features = frozenset(features_from_payload)
        else:
            features = _get_features_for_tier(tier)

        lic = License(
            tier=tier,
            features=features,
            issued_to=payload.get("issued_to", "unknown"),
            issued_at=payload.get("issued_at"),
            expires_at=payload.get("expires_at"),
            license_id=payload.get("license_id"),
            instance_limit=payload.get("instance_limit", 0),
            valid=True,
        )

        if lic.is_expired:
            lic.valid = False
            lic.error = f"License expired at {lic.expires_at}"

        return lic

    except Exception as e:
        logger.warning(f"License verification error: {e}")
        return License(valid=False, error=str(e))


# Module-level cached license (loaded at startup)
_current_license: License | None = None


def get_current_license() -> License:
    """Return the currently active license. Defaults to community."""
    global _current_license
    if _current_license is None:
        _current_license = _community_license()
    return _current_license


def load_license_from_string(license_string: str) -> License:
    """Load and verify a license, update the module-level cache."""
    global _current_license
    _current_license = verify_license(license_string)
    if _current_license.valid:
        logger.info(
            f"License loaded: tier={_current_license.tier}, "
            f"issued_to={_current_license.issued_to}, "
            f"expires_at={_current_license.expires_at}"
        )
    else:
        logger.warning(
            f"License invalid ({_current_license.error}) — "
            f"falling back to community tier"
        )
        _current_license = _community_license()
    return _current_license


def require_feature(feature: str) -> None:
    """Raise if the current license does not include the given feature.

    Use this at the top of Enterprise-only API endpoints/services.
    """
    lic = get_current_license()
    if not lic.has_feature(feature):
        from fastapi import HTTPException
        raise HTTPException(
            status_code=402,  # Payment Required
            detail={
                "error": "feature_not_licensed",
                "feature": feature,
                "current_tier": lic.tier,
                "message": (
                    f"The feature '{feature}' requires a higher license tier. "
                    f"You are on '{lic.tier}'. "
                    f"See https://github.com/greeves89/AI-Employee for upgrade options."
                ),
            },
        )


def has_feature(feature: str) -> bool:
    """Non-raising feature check."""
    return get_current_license().has_feature(feature)
