"""JWT authentication and password hashing utilities."""

import hashlib
import hmac
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.config import settings

# JWT config
ALGORITHM = "HS256"
ACCESS_TOKEN_TTL = timedelta(minutes=30)
REFRESH_TOKEN_TTL = timedelta(days=7)


def internal_service_secret() -> str:
    """Shared secret for in-process service calls (e.g. the Telegram bot calling
    its own API over loopback). Domain-separated from ``api_secret_key`` via HMAC
    so the value transmitted in the ``X-Internal-Secret`` header is NOT the JWT
    signing key — a leak of this header therefore cannot be used to forge tokens.
    Mirrors the derivation in ``core.mcp_oauth``."""
    return hmac.new(
        settings.api_secret_key.encode(), b"internal-service-auth-v1", hashlib.sha256
    ).hexdigest()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def create_access_token(user_id: str, role: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "role": role,
        "type": "access",
        "iat": now,
        "exp": now + ACCESS_TOKEN_TTL,
        "jti": uuid.uuid4().hex[:12],
    }
    return jwt.encode(payload, settings.api_secret_key, algorithm=ALGORITHM)


def create_refresh_token(user_id: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "type": "refresh",
        "iat": now,
        "exp": now + REFRESH_TOKEN_TTL,
        "jti": uuid.uuid4().hex[:12],
    }
    return jwt.encode(payload, settings.api_secret_key, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    """Decode and validate a JWT token. Raises jwt.PyJWTError on failure."""
    return jwt.decode(token, settings.api_secret_key, algorithms=[ALGORITHM])
