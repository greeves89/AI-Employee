"""Fernet-based encryption for OAuth tokens and other secrets."""

import logging
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

logger = logging.getLogger(__name__)

# Persistent key file so auto-generated keys survive container restarts
_KEY_FILE = Path("/app/data/.encryption_key")

_cached_key: str | None = None


def _is_valid_fernet_key(key: str | None) -> bool:
    """Return True if `key` is a valid 32-byte url-safe base64 Fernet key."""
    if not key:
        return False
    try:
        Fernet(key.encode() if isinstance(key, str) else key)
        return True
    except Exception:
        return False


def _resolve_key() -> str:
    """Resolve encryption key: env var (if valid) > persisted file > auto-generate.

    An invalid ENCRYPTION_KEY in the environment is treated as missing (with a
    loud warning) instead of crashing the API on every secret operation —
    otherwise users see cryptic 500s when adding API keys.
    """
    global _cached_key
    if _cached_key:
        return _cached_key

    # 1. From environment / settings (only if it is a valid Fernet key)
    env_key = settings.encryption_key
    if env_key:
        if _is_valid_fernet_key(env_key):
            _cached_key = env_key
            return env_key
        logger.error(
            "ENCRYPTION_KEY in environment is INVALID (must be 32 url-safe base64-encoded bytes — "
            "44 characters, generated via cryptography.fernet.Fernet.generate_key()). "
            "Falling back to persisted/auto-generated key. Fix .env and restart to make this stable."
        )

    # 2. From persisted file
    if _KEY_FILE.exists():
        key = _KEY_FILE.read_text().strip()
        if _is_valid_fernet_key(key):
            logger.info("Loaded ENCRYPTION_KEY from %s", _KEY_FILE)
            _cached_key = key
            settings.encryption_key = key
            return key
        logger.warning("Persisted key at %s is invalid — regenerating.", _KEY_FILE)

    # 3. Auto-generate and persist
    key = Fernet.generate_key().decode()
    _KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    _KEY_FILE.write_text(key)
    _KEY_FILE.chmod(0o600)
    logger.warning("Auto-generated ENCRYPTION_KEY and saved to %s", _KEY_FILE)
    _cached_key = key
    settings.encryption_key = key
    return key


def get_fernet() -> Fernet:
    """Get Fernet instance using ENCRYPTION_KEY from settings or auto-generated."""
    key = _resolve_key()
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_token(plaintext: str) -> str:
    """Encrypt a token string and return base64-encoded ciphertext."""
    f = get_fernet()
    return f.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_token(ciphertext: str) -> str:
    """Decrypt a base64-encoded ciphertext and return the plaintext token."""
    f = get_fernet()
    try:
        return f.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        raise ValueError("Failed to decrypt token. ENCRYPTION_KEY may have changed.")
