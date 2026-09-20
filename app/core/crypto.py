"""Envelope encryption for channel credentials (decision D8).

Tokens for WordPress, Telegram, Instagram ... never touch the database in
clear text.  The key lives in the environment or a secret file, the ciphertext
lives in ``channel_credential.encrypted_payload`` together with the key version
so a future rotation can decrypt old rows.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


class CredentialEncryptionError(RuntimeError):
    """Raised when a credential cannot be encrypted or decrypted."""


@lru_cache
def _fernet() -> Fernet:
    key = settings.credential_encryption_key
    if not key:
        raise CredentialEncryptionError(
            "CREDENTIAL_ENCRYPTION_KEY is not set; channel credentials cannot be stored. "
            'Generate one with: python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"'
        )
    try:
        return Fernet(key.encode("ascii") if isinstance(key, str) else key)
    except (ValueError, TypeError) as exc:  # malformed key
        raise CredentialEncryptionError(f"CREDENTIAL_ENCRYPTION_KEY is invalid: {exc}") from exc


def encrypt_payload(payload: dict[str, Any]) -> bytes:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return _fernet().encrypt(raw)


def decrypt_payload(blob: bytes) -> dict[str, Any]:
    try:
        raw = _fernet().decrypt(bytes(blob))
    except InvalidToken as exc:
        raise CredentialEncryptionError(
            "credential could not be decrypted — wrong or rotated key"
        ) from exc
    return json.loads(raw.decode("utf-8"))
