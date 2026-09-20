"""The analytics API: credential CRUD and the per-package metrics view, end
to end through HTTP — the same posture ``tests/test_publishing_api.py``
takes toward channel credentials.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.db.enums import Channel, PublicationStatus
from app.db.models import ContentPackage, MetricSnapshot, Publication
from app.db.tenancy import system_session
from app.schemas.tenant import TenantCreate
from tests.conftest import requires_db

pytestmark = requires_db

PASSWORD = "correct-horse-battery"

SEARCH_CONSOLE_PAYLOAD = {
    "site_url": "https://acme.example/",
    "service_account": {
        "client_email": "svc@acme.iam.gserviceaccount.com",
        "private_key": "fake-key-content",
    },
}


@pytest.fixture
def client(clean_database) -> Iterator[TestClient]:
    from app.main import create_app

    with TestClient(create_app(), raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture
def acme(clean_database):
    from app.services.auth import create_tenant_with_owner

    return create_tenant_with_owner(
        TenantCreate(
            slug="acme",
            name="Acme",
            owner_email="owner@acme.example",
            owner_password=PASSWORD,
        )
    )


def login(client: TestClient, email: str, password: str = PASSWORD) -> str:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def owner_token(client, acme) -> str:
    return login(client, "owner@acme.example")


@pytest.fixture
def workspace_id(client, owner_token) -> str:
    return client.get("/api/v1/workspaces", headers=auth(owner_token)).json()[0]["id"]


# ---------------------------------------------------------------------------
# credentials
# ---------------------------------------------------------------------------
def test_creating_a_credential_never_echoes_the_service_account_key(
    client, owner_token, workspace_id
) -> None:
    response = client.post(
        f"/api/v1/workspaces/{workspace_id}/analytics-credentials",
        headers=auth(owner_token),
        json={
            "provider": "search_console",
            "payload": SEARCH_CONSOLE_PAYLOAD,
            "public_metadata": {"site_url": "https://acme.example/"},
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert "fake-key-content" not in response.text
    assert "service_account" not in body
    assert body["public_metadata"] == {"site_url": "https://acme.example/"}


def test_creating_a_credential_needs_admin(client, acme, workspace_id, owner_token) -> None:
    client.post(
        "/api/v1/tenants/current/members",
        headers=auth(owner_token),
        json={"email": "editor@acme.example", "password": PASSWORD, "role": "editor"},
    )
    editor_token = login(client, "editor@acme.example")

    response = client.post(
        f"/api/v1/workspaces/{workspace_id}/analytics-credentials",
        headers=auth(editor_token),
        json={"provider": "search_console", "payload": SEARCH_CONSOLE_PAYLOAD},
    )
    assert response.status_code == 403


def test_listing_credentials_shows_only_public_metadata(
    client, owner_token, workspace_id
) -> None:
    client.post(
        f"/api/v1/workspaces/{workspace_id}/analytics-credentials",
        headers=auth(owner_token),
        json={
            "provider": "ga4",
            "payload": {
                "property_id": "123",
                "service_account": SEARCH_CONSOLE_PAYLOAD["service_account"],
            },
            "public_metadata": {"property_id": "123"},
        },
    )
    response = client.get(
        f"/api/v1/workspaces/{workspace_id}/analytics-credentials", headers=auth(owner_token)
    )
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["provider"] == "ga4"
    assert rows[0]["public_metadata"] == {"property_id": "123"}
    assert "service_account" not in str(rows[0])


def test_deactivating_a_credential(client, owner_token, workspace_id) -> None:
    created = client.post(
        f"/api/v1/workspaces/{workspace_id}/analytics-credentials",
        headers=auth(owner_token),
        json={"provider": "search_console", "payload": SEARCH_CONSOLE_PAYLOAD},
    ).json()

    response = client.delete(
        f"/api/v1/workspaces/{workspace_id}/analytics-credentials/{created['id']}",
        headers=auth(owner_token),
    )
    assert response.status_code == 204

    rows = client.get(
        f"/api/v1/workspaces/{workspace_id}/analytics-credentials", headers=auth(owner_token)
    ).json()
    assert rows[0]["is_active"] is False


def test_an_invalid_payload_is_rejected_with_422(client, owner_token, workspace_id) -> None:
    response = client.post(
        f"/api/v1/workspaces/{workspace_id}/analytics-credentials",
        headers=auth(owner_token),
        json={"provider": "ga4", "payload": {"property_id": "123"}},
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# package metrics
# ---------------------------------------------------------------------------
@pytest.fixture
def package_with_snapshot(acme, workspace_id):
    with system_session() as session:
        package = ContentPackage(
            tenant_id=acme.id,
            workspace_id=uuid.UUID(workspace_id),
            title="راهنمای خرید دریل برقی",
            locale="fa",
        )
        session.add(package)
        session.flush()
        publication = Publication(
            tenant_id=acme.id,
            package_id=package.id,
            channel=Channel.WORDPRESS,
            status=PublicationStatus.PUBLISHED,
            scheduled_at=datetime.now(UTC),
            external_url="https://acme.example/drill-guide",
        )
        session.add(publication)
        session.flush()
        snapshot = MetricSnapshot(
            tenant_id=acme.id,
            publication_id=publication.id,
            source="search_console",
            captured_for=datetime(2026, 1, 15, tzinfo=UTC),
            impressions=80,
            clicks=5,
            position=12.3,
            metrics={"ctr": 0.0625},
        )
        session.add(snapshot)
        session.flush()
        package_id = package.id

    return str(package_id)


def test_package_metrics_are_visible_to_a_viewer(
    client, owner_token, package_with_snapshot
) -> None:
    response = client.get(
        f"/api/v1/packages/{package_with_snapshot}/metrics", headers=auth(owner_token)
    )
    assert response.status_code == 200
    body = response.json()
    assert body["package_id"] == package_with_snapshot
    assert len(body["snapshots"]) == 1
    assert body["snapshots"][0]["source"] == "search_console"
    assert body["snapshots"][0]["clicks"] == 5


def test_a_package_with_no_snapshots_yet_is_an_empty_list(
    client, owner_token, workspace_id
) -> None:
    with system_session() as session:
        package = ContentPackage(
            tenant_id=uuid.UUID(
                client.get("/api/v1/tenants/current", headers=auth(owner_token)).json()["id"]
            ),
            workspace_id=uuid.UUID(workspace_id),
            title="بدون بازدید",
            locale="fa",
        )
        session.add(package)
        session.flush()
        package_id = str(package.id)

    response = client.get(f"/api/v1/packages/{package_id}/metrics", headers=auth(owner_token))
    assert response.status_code == 200
    assert response.json()["snapshots"] == []
