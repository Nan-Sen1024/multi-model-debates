from __future__ import annotations

import base64
import hashlib
import logging
import os
from secrets import token_bytes

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

_DEFAULT_ENCRYPTION_KEY = "multi-model-debates-dev-key"
_INSECURE_DEFAULT_FLAG = "ALLOW_INSECURE_DEFAULT_ENCRYPTION_KEY"
_RUNTIME_KEY_ENV = "ENCRYPTION_KEY"
# Keep a process-local fallback so unit tests and ephemeral local runs still work
# without forcing a weak hard-coded secret onto every deployment.
_PROCESS_LOCAL_KEY: str | None = None


def _is_probably_fernet_key(value: str) -> bool:
    try:
        decoded = base64.urlsafe_b64decode(value.encode("utf-8"))
    except Exception:
        return False
    return len(decoded) == 32


def _derive_fernet_key(raw_key: str) -> bytes:
    if _is_probably_fernet_key(raw_key):
        return raw_key.encode("utf-8")
    return base64.urlsafe_b64encode(hashlib.sha256(raw_key.encode("utf-8")).digest())


def _build_fernet() -> Fernet:
    global _PROCESS_LOCAL_KEY

    raw_key = os.getenv(_RUNTIME_KEY_ENV, "").strip()
    if not raw_key:
        # Prefer a per-process random key over a weak shared default.
        # This keeps the app functional for tests/local development while
        # preventing a deploy from silently using a predictable secret.
        if _PROCESS_LOCAL_KEY is None:
            _PROCESS_LOCAL_KEY = base64.urlsafe_b64encode(token_bytes(32)).decode("utf-8")
            logger.warning(
                "%s is not set; using a process-local ephemeral encryption key. "
                "Encrypted values will not survive process restarts.",
                _RUNTIME_KEY_ENV,
            )
        raw_key = _PROCESS_LOCAL_KEY
    elif os.getenv(_INSECURE_DEFAULT_FLAG, "").strip().lower() in {"1", "true", "yes"}:
        raw_key = _DEFAULT_ENCRYPTION_KEY

    return Fernet(_derive_fernet_key(raw_key))


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
