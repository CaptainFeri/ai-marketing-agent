"""The publishing API: channel credentials, variant/media selection, and
scheduling — end to end through HTTP, the way the panel will call it.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.db.enums import Channel, MediaKind, PackageStatus
from app.db.models import ContentPackage, MediaAsset, Variant
from app.db.tenancy import system_session
from app.schemas.tenant import TenantCreate
from app.services import storage
from tests.conftest import requires_db

pytestmark = requires_db

PASSWORD = "correct-horse-battery"


@pytest.fixture(autouse=True)
def _memory_storage():
    storage.set_backend(storage.InMemoryStorageBackend())
    yield
    storage.set_backend(None)


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


@pytest.fixture
def package_and_variant(acme, workspace_id):
    """A package with one selected image and one selected WordPress variant
    — set up directly, the way a real one would arrive at this point only
    after the text pipeline and both gates."""
    import uuid

    with system_session() as session:
        package = ContentPackage(
            tenant_id=acme.id,
            workspace_id=uuid.UUID(workspace_id),
            title="راهنمای خرید دریل برقی",
            locale="fa",
            status=PackageStatus.SELECTION,
            article={"title": "راهنمای خرید دریل برقی", "slug": "drill-guide"},
        )
        session.add(package)
        session.flush()

        variant = Variant(
            tenant_id=acme.id,
            package_id=package.id,
            channel=Channel.WORDPRESS,
            body={"hook": "قلاب", "body": "متن پست"},
        )
        session.add(variant)

        asset = MediaAsset(
            tenant_id=acme.id,
            package_id=package.id,
            kind=MediaKind.IMAGE,
            storage_key="acme/x.png",
            mime_type="image/png",
        )
        session.add(asset)
        session.flush()
        storage.get_backend().put("acme/x.png", b"\x89PNG...", "image/png")

        package_id, variant_id, asset_id = package.id, variant.id, asset.id

    return str(package_id), str(variant_id), str(asset_id)


WORDPRESS_PAYLOAD = {
    "site_url": "https://acme.example",
    "username": "bot",
    "application_password": "abcd efgh",
}


# ---------------------------------------------------------------------------
# channel credentials
# ---------------------------------------------------------------------------
def test_creating_a_credential_never_echoes_the_secret_back(
    client, owner_token, workspace_id
) -> None:
    response = client.post(
        f"/api/v1/workspaces/{workspace_id}/credentials",
        headers=auth(owner_token),
        json={
            "channel": "wordpress",
            "payload": WORDPRESS_PAYLOAD,
            "public_metadata": {"site_url": "https://acme.example"},
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert "abcd efgh" not in response.text
    assert "application_password" not in body
    assert body["public_metadata"] == {"site_url": "https://acme.example"}


def test_creating_a_credential_needs_admin(client, acme, workspace_id, owner_token) -> None:
    client.post(
        "/api/v1/tenants/current/members",
        headers=auth(owner_token),
        json={"email": "editor@acme.example", "password": PASSWORD, "role": "editor"},
    )
    editor_token = login(client, "editor@acme.example")

    response = client.post(
        f"/api/v1/workspaces/{workspace_id}/credentials",
        headers=auth(editor_token),
        json={"channel": "wordpress", "payload": WORDPRESS_PAYLOAD},
    )
    assert response.status_code == 403


def test_an_invalid_credential_payload_is_rejected(client, owner_token, workspace_id) -> None:
    response = client.post(
        f"/api/v1/workspaces/{workspace_id}/credentials",
        headers=auth(owner_token),
        json={"channel": "wordpress", "payload": {"site_url": "not a url"}},
    )
    assert response.status_code == 422


def test_listing_credentials_shows_only_public_metadata(client, owner_token, workspace_id) -> None:
    client.post(
        f"/api/v1/workspaces/{workspace_id}/credentials",
        headers=auth(owner_token),
        json={"channel": "telegram", "payload": {"bot_token": "1:a", "chat_id": "@x"}},
    )
    response = client.get(
        f"/api/v1/workspaces/{workspace_id}/credentials", headers=auth(owner_token)
    )
    assert response.status_code == 200
    assert "1:a" not in response.text
    assert response.json()[0]["channel"] == "telegram"


def test_deactivating_a_credential(client, owner_token, workspace_id) -> None:
    created = client.post(
        f"/api/v1/workspaces/{workspace_id}/credentials",
        headers=auth(owner_token),
        json={"channel": "wordpress", "payload": WORDPRESS_PAYLOAD},
    ).json()

    response = client.delete(
        f"/api/v1/workspaces/{workspace_id}/credentials/{created['id']}",
        headers=auth(owner_token),
    )
    assert response.status_code == 204

    listed = client.get(
        f"/api/v1/workspaces/{workspace_id}/credentials", headers=auth(owner_token)
    ).json()
    assert listed[0]["is_active"] is False


# ---------------------------------------------------------------------------
# variant and media selection
# ---------------------------------------------------------------------------
def test_selecting_a_variant(client, owner_token, package_and_variant) -> None:
    package_id, variant_id, _ = package_and_variant
    response = client.patch(
        f"/api/v1/packages/{package_id}/variants/{variant_id}",
        headers=auth(owner_token),
        json={"is_selected": True},
    )
    assert response.status_code == 200
    assert response.json()["is_selected"] is True


def test_selecting_a_variant_from_another_package_is_a_404(
    client, owner_token, package_and_variant
) -> None:
    package_id, _, _ = package_and_variant
    response = client.patch(
        f"/api/v1/packages/{package_id}/variants/00000000-0000-0000-0000-000000000000",
        headers=auth(owner_token),
        json={"is_selected": True},
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# scheduling
# ---------------------------------------------------------------------------
def select_variant(client, token, package_id, variant_id):
    return client.patch(
        f"/api/v1/packages/{package_id}/variants/{variant_id}",
        headers=auth(token),
        json={"is_selected": True},
    )


def test_scheduling_a_selected_variant(client, owner_token, package_and_variant) -> None:
    package_id, variant_id, _ = package_and_variant
    select_variant(client, owner_token, package_id, variant_id)

    when = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    response = client.post(
        f"/api/v1/packages/{package_id}/publications",
        headers=auth(owner_token),
        json={"variant_id": variant_id, "scheduled_at": when},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "scheduled"
    assert body["channel"] == "wordpress"


def test_scheduling_an_unselected_variant_is_refused(
    client, owner_token, package_and_variant
) -> None:
    package_id, variant_id, _ = package_and_variant
    response = client.post(
        f"/api/v1/packages/{package_id}/publications",
        headers=auth(owner_token),
        json={
            "variant_id": variant_id,
            "scheduled_at": datetime.now(UTC).isoformat(),
        },
    )
    assert response.status_code == 409


def test_listing_and_cancelling_a_publication(client, owner_token, package_and_variant) -> None:
    package_id, variant_id, _ = package_and_variant
    select_variant(client, owner_token, package_id, variant_id)
    created = client.post(
        f"/api/v1/packages/{package_id}/publications",
        headers=auth(owner_token),
        json={
            "variant_id": variant_id,
            "scheduled_at": datetime.now(UTC).isoformat(),
        },
    ).json()

    listed = client.get(
        f"/api/v1/packages/{package_id}/publications", headers=auth(owner_token)
    ).json()
    assert len(listed) == 1

    cancelled = client.post(
        f"/api/v1/packages/{package_id}/publications/{created['id']}/cancel",
        headers=auth(owner_token),
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"


def test_a_viewer_cannot_schedule_a_publication(
    client, acme, owner_token, package_and_variant
) -> None:
    package_id, variant_id, _ = package_and_variant
    select_variant(client, owner_token, package_id, variant_id)

    client.post(
        "/api/v1/tenants/current/members",
        headers=auth(owner_token),
        json={"email": "viewer@acme.example", "password": PASSWORD, "role": "viewer"},
    )
    viewer_token = login(client, "viewer@acme.example")

    response = client.post(
        f"/api/v1/packages/{package_id}/publications",
        headers=auth(viewer_token),
        json={
            "variant_id": variant_id,
            "scheduled_at": datetime.now(UTC).isoformat(),
        },
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# isolation
# ---------------------------------------------------------------------------
def test_a_publication_is_invisible_to_another_tenant(
    client, package_and_variant, owner_token, clean_database
) -> None:
    from app.services.auth import create_tenant_with_owner

    package_id, variant_id, _ = package_and_variant
    select_variant(client, owner_token, package_id, variant_id)
    client.post(
        f"/api/v1/packages/{package_id}/publications",
        headers=auth(owner_token),
        json={
            "variant_id": variant_id,
            "scheduled_at": datetime.now(UTC).isoformat(),
        },
    )

    create_tenant_with_owner(
        TenantCreate(slug="other", name="Other", owner_email="o@x.example", owner_password=PASSWORD)
    )
    other_token = login(client, "o@x.example")

    response = client.get(f"/api/v1/packages/{package_id}/publications", headers=auth(other_token))
    assert response.status_code in {403, 404}
