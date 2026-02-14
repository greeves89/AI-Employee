"""Fernet-based encryption for OAuth tokens and other secrets."""

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings


def get_fernet() -> Fernet:
    """Get Fernet instance using ENCRYPTION_KEY from settings."""
    key = settings.encryption_key
    if not key:
        raise ValueError(
            "ENCRYPTION_KEY is not configured. "
            "Generate one with: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
        )
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
