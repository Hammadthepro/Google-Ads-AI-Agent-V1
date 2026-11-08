"""Token encryption (Fernet) and PKCE helpers used by the integrations service.

Secrets are encrypted at rest; only fingerprints are ever displayed.
"""
from __future__ import annotations

import base64
import hashlib
import secrets

from cryptography.fernet import Fernet

from app.core.config import get_settings



def _fernet() -> Fernet:
    s = get_settings()
    key = s.TOKEN_ENCRYPTION_KEY
    if not key:
        if s.APP_ENV == "production":
            raise RuntimeError("TOKEN_ENCRYPTION_KEY is required in production")
        key = base64.urlsafe_b64encode(hashlib.sha256(("token-key|" + s.APP_SECRET_KEY).encode()).digest()).decode()
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_token(token: str) -> str:
    return _fernet().encrypt(token.encode()).decode()


def decrypt_token(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode()).decode()


def new_pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge
