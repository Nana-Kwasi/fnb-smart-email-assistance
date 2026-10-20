"""Encryption for secrets the dashboard stores (database passwords)."""

from __future__ import annotations

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


class SecretUnreadable(Exception):
    """A stored secret cannot be decrypted, usually because FNB_ENCRYPTION_KEY changed."""


@lru_cache
def _fernet() -> Fernet:
    settings = get_settings()
    key = settings.encryption_key
    if not key:
        # Only reachable outside production (production refuses to start without a key).
        key = base64.urlsafe_b64encode(hashlib.sha256(settings.session_secret.encode()).digest()).decode()
    return Fernet(key.encode())


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise SecretUnreadable(
            "A saved password cannot be read. Re-enter it on the Data Sources page."
        ) from exc
