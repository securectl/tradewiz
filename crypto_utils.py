"""
Encryption utilities for user API key storage.
Uses Fernet symmetric encryption with ENCRYPTION_KEY env var.
"""

import os
from cryptography.fernet import Fernet

_ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY", "")

# Auto-generate a key for local development if not set.
# In production, ENCRYPTION_KEY must be set in environment variables.
if not _ENCRYPTION_KEY:
    if os.getenv("DATABASE_URL"):
        raise RuntimeError(
            "FATAL: ENCRYPTION_KEY is not set. "
            "Generate one with: python crypto_utils.py"
        )
    import logging as _log
    _ENCRYPTION_KEY = Fernet.generate_key().decode()
    os.environ["ENCRYPTION_KEY"] = _ENCRYPTION_KEY
    _log.getLogger(__name__).warning(
        "ENCRYPTION_KEY not set — auto-generated for dev. Set it in .env for persistence."
    )


def _get_fernet():
    return Fernet(_ENCRYPTION_KEY.encode() if isinstance(_ENCRYPTION_KEY, str) else _ENCRYPTION_KEY)


def encrypt(plaintext: str) -> str:
    """Encrypt a string and return the base64-encoded ciphertext."""
    f = _get_fernet()
    return f.encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    """Decrypt a base64-encoded ciphertext and return the plaintext."""
    f = _get_fernet()
    return f.decrypt(ciphertext.encode()).decode()


def generate_key() -> str:
    """Generate a new Fernet key (for initial setup)."""
    return Fernet.generate_key().decode()


if __name__ == "__main__":
    print("New ENCRYPTION_KEY:", generate_key())
