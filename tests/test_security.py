"""Password hashing, tokens and credential encryption."""

from __future__ import annotations

import uuid
from datetime import timedelta

import jwt
import pytest

from app.core.crypto import CredentialEncryptionError, decrypt_payload, encrypt_payload
from app.core.security import create_token, decode_token, hash_password, verify_password


def test_a_password_round_trips() -> None:
    hashed = hash_password("correct-horse-battery")
    assert hashed != "correct-horse-battery"
    assert verify_password("correct-horse-battery", hashed)
    assert not verify_password("wrong-horse-battery", hashed)


def test_the_same_password_hashes_differently_each_time() -> None:
    assert hash_password("same-password") != hash_password("same-password")


def test_a_long_passphrase_is_not_silently_truncated() -> None:
    """bcrypt ignores everything past 72 bytes; pre-hashing keeps it meaningful."""
    base = "x" * 80
    hashed = hash_password(base)
    assert verify_password(base, hashed)
    assert not verify_password("x" * 79 + "y", hashed)


def test_a_malformed_hash_fails_closed() -> None:
    assert not verify_password("anything", "not-a-bcrypt-hash")


def test_a_token_carries_the_subject_and_tenant() -> None:
    user_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    token = create_token(user_id, "access", tenant_id=tenant_id)
    payload = decode_token(token, expected_type="access")
    assert payload["sub"] == str(user_id)
    assert payload["tid"] == str(tenant_id)


def test_a_refresh_token_is_refused_where_an_access_token_is_required() -> None:
    token = create_token(uuid.uuid4(), "refresh")
    with pytest.raises(jwt.InvalidTokenError):
        decode_token(token, expected_type="access")


def test_an_expired_token_is_refused() -> None:
    token = create_token(uuid.uuid4(), "access", expires_delta=timedelta(seconds=-10))
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_token(token)


def test_a_tampered_token_is_refused() -> None:
    token = create_token(uuid.uuid4(), "access")
    head, payload, signature = token.split(".")
    with pytest.raises(jwt.InvalidSignatureError):
        decode_token(f"{head}.{payload}.{signature[:-3]}abc")


def test_two_tokens_for_one_user_are_distinguishable() -> None:
    user_id = uuid.uuid4()
    first = decode_token(create_token(user_id, "access"))
    second = decode_token(create_token(user_id, "access"))
    assert first["jti"] != second["jti"]


# --------------------------------------------------------------------------
# channel credentials (decision D8)
# --------------------------------------------------------------------------
@pytest.fixture
def encryption_key(monkeypatch) -> None:
    from cryptography.fernet import Fernet

    from app.core import crypto
    from app.core.config import settings

    monkeypatch.setattr(settings, "credential_encryption_key", Fernet.generate_key().decode())
    crypto._fernet.cache_clear()
    yield
    crypto._fernet.cache_clear()


def test_a_credential_round_trips(encryption_key) -> None:
    payload = {"site": "https://example.com", "user": "bot", "app_password": "s3cret"}
    blob = encrypt_payload(payload)
    assert b"s3cret" not in blob
    assert decrypt_payload(blob) == payload


def test_a_credential_from_another_key_cannot_be_read(encryption_key, monkeypatch) -> None:
    from cryptography.fernet import Fernet

    from app.core import crypto
    from app.core.config import settings

    blob = encrypt_payload({"token": "abc"})

    monkeypatch.setattr(settings, "credential_encryption_key", Fernet.generate_key().decode())
    crypto._fernet.cache_clear()
    with pytest.raises(CredentialEncryptionError):
        decrypt_payload(blob)


def test_a_missing_key_is_reported_clearly(monkeypatch) -> None:
    from app.core import crypto
    from app.core.config import settings

    monkeypatch.setattr(settings, "credential_encryption_key", None)
    crypto._fernet.cache_clear()
    with pytest.raises(CredentialEncryptionError) as excinfo:
        encrypt_payload({"token": "abc"})
    assert "CREDENTIAL_ENCRYPTION_KEY" in str(excinfo.value)
    crypto._fernet.cache_clear()
