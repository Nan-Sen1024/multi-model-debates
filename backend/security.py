from __future__ import annotations

import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken


def _build_fernet() -> Fernet:
    raw_key = os.getenv("ENCRYPTION_KEY", "multi-model-debates-dev-key")
    try:
        decoded = base64.urlsafe_b64decode(raw_key.encode("utf-8"))
        if len(decoded) == 32:
            key = raw_key.encode("utf-8")
        else:
            raise ValueError
    except Exception:
        key = base64.urlsafe_b64encode(hashlib.sha256(raw_key.encode("utf-8")).digest())
    return Fernet(key)


_FERNET = _build_fernet()


def encrypt_value(value: str) -> str:
    if not value:
        return ""
    return _FERNET.encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_value(value: str) -> str:
    if not value:
        return ""
    try:
        return _FERNET.decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        try:
            return base64.b64decode(value.encode("utf-8")).decode("utf-8")
        except Exception:
            return value
