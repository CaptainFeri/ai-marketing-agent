"""Storing channel credentials: validated before encryption, never decrypted
except by the one function whose whole job is to hand a connector its secret.
"""

from __future__ import annotations

import pytest

from app.connectors import CredentialValidationError
from app.core.errors import ConflictError, NotFoundError
from app.db.enums import Channel
from app.services import channel_credentials as creds
from tests.conftest import requires_db

pytestmark = requires_db

WORDPRESS_PAYLOAD = {
    "site_url": "https://acme.example",
    "username": "bot",
    "application_password": "abcd efgh ijkl",
}


def test_creating_a_credential_encrypts_the_payload(tenant_factory, system_db) -> None:
    tenant, workspace = tenant_factory("acme")
    row = creds.create_credential(
        system_db, tenant.id, workspace.id, Channel.WORDPRESS, WORDPRESS_PAYLOAD
    )
    assert row.encrypted_payload != str(WORDPRESS_PAYLOAD).encode()
    assert b"abcd efgh ijkl" not in row.encrypted_payload


def test_an_invalid_payload_is_rejected_before_it_touches_the_database(
    tenant_factory, system_db
) -> None:
    tenant, workspace = tenant_factory("acme")
    with pytest.raises(CredentialValidationError):
        creds.create_credential(
            system_db, tenant.id, workspace.id, Channel.WORDPRESS, {"site_url": "not a url"}
        )


def test_a_second_credential_with_the_same_label_is_refused(tenant_factory, system_db) -> None:
    tenant, workspace = tenant_factory("acme")
    creds.create_credential(
        system_db, tenant.id, workspace.id, Channel.WORDPRESS, WORDPRESS_PAYLOAD
    )
    with pytest.raises(ConflictError):
        creds.create_credential(
            system_db, tenant.id, workspace.id, Channel.WORDPRESS, WORDPRESS_PAYLOAD
        )


def test_a_second_label_is_allowed_for_a_second_account(tenant_factory, system_db) -> None:
    """One workspace, two Telegram channels."""
    tenant, workspace = tenant_factory("acme")
    creds.create_credential(
        system_db,
        tenant.id,
        workspace.id,
        Channel.TELEGRAM,
        {"bot_token": "1:a", "chat_id": "@one"},
        label="main",
    )
    second = creds.create_credential(
        system_db,
        tenant.id,
        workspace.id,
        Channel.TELEGRAM,
        {"bot_token": "2:b", "chat_id": "@two"},
        label="backup",
    )
    assert second.label == "backup"


def test_only_an_active_credential_is_returned_for_publishing(tenant_factory, system_db) -> None:
    tenant, workspace = tenant_factory("acme")
    row = creds.create_credential(
        system_db, tenant.id, workspace.id, Channel.WORDPRESS, WORDPRESS_PAYLOAD
    )
    creds.deactivate_credential(system_db, row)
    system_db.flush()

    with pytest.raises(NotFoundError):
        creds.active_credential_for(system_db, workspace.id, Channel.WORDPRESS)


def test_decrypt_for_publish_returns_the_original_payload(tenant_factory, system_db) -> None:
    tenant, workspace = tenant_factory("acme")
    row = creds.create_credential(
        system_db, tenant.id, workspace.id, Channel.WORDPRESS, WORDPRESS_PAYLOAD
    )
    payload = creds.decrypt_for_publish(row)
    assert payload.username == "bot"
    assert payload.application_password == "abcd efgh ijkl"


def test_decrypting_records_when_it_was_last_used(tenant_factory, system_db) -> None:
    tenant, workspace = tenant_factory("acme")
    row = creds.create_credential(
        system_db, tenant.id, workspace.id, Channel.WORDPRESS, WORDPRESS_PAYLOAD
    )
    assert row.last_used_at is None
    creds.decrypt_for_publish(row)
    assert row.last_used_at is not None


def test_public_metadata_never_carries_the_secret(tenant_factory, system_db) -> None:
    """What the panel is allowed to show back to the customer."""
    tenant, workspace = tenant_factory("acme")
    row = creds.create_credential(
        system_db,
        tenant.id,
        workspace.id,
        Channel.WORDPRESS,
        WORDPRESS_PAYLOAD,
        public_metadata={"site_url": "https://acme.example"},
    )
    assert row.public_metadata == {"site_url": "https://acme.example"}
    assert "application_password" not in row.public_metadata


def test_a_credential_belongs_to_one_workspace(tenant_factory, system_db) -> None:
    tenant_a, workspace_a = tenant_factory("acme")
    _, workspace_b = tenant_factory("globex")
    row = creds.create_credential(
        system_db, tenant_a.id, workspace_a.id, Channel.WORDPRESS, WORDPRESS_PAYLOAD
    )
    with pytest.raises(NotFoundError):
        creds.get_credential(system_db, workspace_b.id, row.id)
