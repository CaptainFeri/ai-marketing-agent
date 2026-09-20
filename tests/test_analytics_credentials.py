"""Storing analytics credentials: validated before encryption, never
decrypted except by the one function a metrics pull calls — the same
posture ``tests/test_channel_credentials.py`` takes for publish credentials.
"""

from __future__ import annotations

import pytest

from app.connectors.analytics_credentials import AnalyticsCredentialValidationError
from app.core.errors import ConflictError, NotFoundError
from app.db.enums import AnalyticsProvider
from app.services import analytics_credentials as creds
from tests.conftest import requires_db

pytestmark = requires_db

SEARCH_CONSOLE_PAYLOAD = {
    "site_url": "https://acme.example/",
    "service_account": {
        "client_email": "svc@acme-project.iam.gserviceaccount.com",
        "private_key": "-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----\n",
    },
}
GA4_PAYLOAD = {
    "property_id": "123456789",
    "service_account": {
        "client_email": "svc@acme-project.iam.gserviceaccount.com",
        "private_key": "-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----\n",
    },
}


def test_creating_a_credential_encrypts_the_payload(tenant_factory, system_db) -> None:
    tenant, workspace = tenant_factory("acme")
    row = creds.create_credential(
        system_db, tenant.id, workspace.id, AnalyticsProvider.SEARCH_CONSOLE, SEARCH_CONSOLE_PAYLOAD
    )
    assert row.encrypted_payload != str(SEARCH_CONSOLE_PAYLOAD).encode()
    assert b"acme-project" not in row.encrypted_payload


def test_an_invalid_payload_is_rejected_before_it_touches_the_database(
    tenant_factory, system_db
) -> None:
    tenant, workspace = tenant_factory("acme")
    with pytest.raises(AnalyticsCredentialValidationError):
        creds.create_credential(
            system_db, tenant.id, workspace.id, AnalyticsProvider.GA4, {"property_id": "123"}
        )


def test_a_second_credential_with_the_same_label_is_refused(tenant_factory, system_db) -> None:
    tenant, workspace = tenant_factory("acme")
    creds.create_credential(
        system_db, tenant.id, workspace.id, AnalyticsProvider.GA4, GA4_PAYLOAD
    )
    with pytest.raises(ConflictError):
        creds.create_credential(
            system_db, tenant.id, workspace.id, AnalyticsProvider.GA4, GA4_PAYLOAD
        )


def test_search_console_and_ga4_coexist_for_one_workspace(tenant_factory, system_db) -> None:
    tenant, workspace = tenant_factory("acme")
    creds.create_credential(
        system_db, tenant.id, workspace.id, AnalyticsProvider.SEARCH_CONSOLE, SEARCH_CONSOLE_PAYLOAD
    )
    ga4 = creds.create_credential(
        system_db, tenant.id, workspace.id, AnalyticsProvider.GA4, GA4_PAYLOAD
    )
    assert ga4.provider is AnalyticsProvider.GA4


def test_a_deactivated_credential_is_not_offered_for_a_pull(tenant_factory, system_db) -> None:
    """Unlike a publish credential, a missing analytics credential is a
    normal, silent "skip this provider" — not every workspace configures
    both."""
    tenant, workspace = tenant_factory("acme")
    row = creds.create_credential(
        system_db, tenant.id, workspace.id, AnalyticsProvider.GA4, GA4_PAYLOAD
    )
    creds.deactivate_credential(system_db, row)
    system_db.flush()

    assert creds.active_credential_for(system_db, workspace.id, AnalyticsProvider.GA4) is None


def test_no_credential_configured_is_also_none_not_an_error(tenant_factory, system_db) -> None:
    tenant, workspace = tenant_factory("acme")
    assert creds.active_credential_for(system_db, workspace.id, AnalyticsProvider.GA4) is None


def test_decrypt_for_read_returns_the_original_payload(tenant_factory, system_db) -> None:
    tenant, workspace = tenant_factory("acme")
    row = creds.create_credential(
        system_db, tenant.id, workspace.id, AnalyticsProvider.SEARCH_CONSOLE, SEARCH_CONSOLE_PAYLOAD
    )
    payload = creds.decrypt_for_read(row)
    assert payload.site_url == "https://acme.example/"
    assert payload.service_account.client_email == "svc@acme-project.iam.gserviceaccount.com"


def test_decrypting_records_when_it_was_last_used(tenant_factory, system_db) -> None:
    tenant, workspace = tenant_factory("acme")
    row = creds.create_credential(
        system_db, tenant.id, workspace.id, AnalyticsProvider.GA4, GA4_PAYLOAD
    )
    assert row.last_used_at is None
    creds.decrypt_for_read(row)
    assert row.last_used_at is not None


def test_public_metadata_never_carries_the_service_account_key(tenant_factory, system_db) -> None:
    tenant, workspace = tenant_factory("acme")
    row = creds.create_credential(
        system_db,
        tenant.id,
        workspace.id,
        AnalyticsProvider.SEARCH_CONSOLE,
        SEARCH_CONSOLE_PAYLOAD,
        public_metadata={"site_url": "https://acme.example/"},
    )
    assert row.public_metadata == {"site_url": "https://acme.example/"}
    assert "service_account" not in row.public_metadata


def test_a_credential_belongs_to_one_workspace(tenant_factory, system_db) -> None:
    tenant_a, workspace_a = tenant_factory("acme")
    _, workspace_b = tenant_factory("globex")
    row = creds.create_credential(
        system_db, tenant_a.id, workspace_a.id, AnalyticsProvider.GA4, GA4_PAYLOAD
    )
    with pytest.raises(NotFoundError):
        creds.get_credential(system_db, workspace_b.id, row.id)
