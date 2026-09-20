"""End-to-end API behaviour: login, roles, briefs, packages and the gates."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.db.enums import Role
from app.schemas.tenant import TenantCreate
from tests.conftest import requires_db

pytestmark = requires_db

PASSWORD = "correct-horse-battery"


@pytest.fixture
def client(clean_database) -> Iterator[TestClient]:
    from app.main import create_app

    with TestClient(create_app(), raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture
def acme(clean_database):
    """A tenant with an owner, created the way an operator would create one."""
    from app.services.auth import create_tenant_with_owner

    return create_tenant_with_owner(
        TenantCreate(
            slug="acme",
            name="Acme",
            owner_email="owner@acme.example",
            owner_password=PASSWORD,
            owner_full_name="Acme Owner",
        )
    )


def login(client: TestClient, email: str, password: str = PASSWORD) -> str:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------------
# health and auth
# --------------------------------------------------------------------------
def test_healthz_needs_no_token(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_login_returns_a_token_pair(client: TestClient, acme) -> None:
    response = client.post(
        "/api/v1/auth/login", json={"email": "owner@acme.example", "password": PASSWORD}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["tenant_id"] == str(acme.id)


def test_a_wrong_password_is_rejected(client: TestClient, acme) -> None:
    response = client.post(
        "/api/v1/auth/login", json={"email": "owner@acme.example", "password": "wrong-password"}
    )
    assert response.status_code == 401
    # The message must not reveal whether the address exists.
    assert response.json()["error"]["message"] == "incorrect email or password"


def test_an_unknown_address_gets_the_same_answer(client: TestClient, acme) -> None:
    response = client.post(
        "/api/v1/auth/login", json={"email": "nobody@acme.example", "password": PASSWORD}
    )
    assert response.status_code == 401
    assert response.json()["error"]["message"] == "incorrect email or password"


def test_protected_routes_require_a_token(client: TestClient, acme) -> None:
    assert client.get("/api/v1/workspaces").status_code == 401


def test_a_refresh_token_is_not_accepted_as_an_access_token(client: TestClient, acme) -> None:
    response = client.post(
        "/api/v1/auth/login", json={"email": "owner@acme.example", "password": PASSWORD}
    )
    refresh_token = response.json()["refresh_token"]
    assert client.get("/api/v1/workspaces", headers=auth(refresh_token)).status_code == 401


def test_refresh_issues_a_new_pair(client: TestClient, acme) -> None:
    login_body = client.post(
        "/api/v1/auth/login", json={"email": "owner@acme.example", "password": PASSWORD}
    ).json()
    response = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": login_body["refresh_token"]}
    )
    assert response.status_code == 200
    new_token = response.json()["access_token"]
    assert client.get("/api/v1/workspaces", headers=auth(new_token)).status_code == 200


def test_me_describes_the_session(client: TestClient, acme) -> None:
    token = login(client, "owner@acme.example")
    body = client.get("/api/v1/auth/me", headers=auth(token)).json()
    assert body["user"]["email"] == "owner@acme.example"
    assert body["tenant"]["slug"] == "acme"
    assert body["memberships"][0]["role"] == Role.OWNER.value


# --------------------------------------------------------------------------
# tenancy
# --------------------------------------------------------------------------
def test_a_tenant_only_sees_its_own_workspaces(client: TestClient, clean_database) -> None:
    from app.services.auth import create_tenant_with_owner

    create_tenant_with_owner(
        TenantCreate(slug="alpha", name="Alpha", owner_email="a@x.example", owner_password=PASSWORD)
    )
    create_tenant_with_owner(
        TenantCreate(slug="beta", name="Beta", owner_email="b@x.example", owner_password=PASSWORD)
    )

    alpha_workspaces = client.get(
        "/api/v1/workspaces", headers=auth(login(client, "a@x.example"))
    ).json()
    beta_workspaces = client.get(
        "/api/v1/workspaces", headers=auth(login(client, "b@x.example"))
    ).json()

    assert len(alpha_workspaces) == 1
    assert len(beta_workspaces) == 1
    assert alpha_workspaces[0]["id"] != beta_workspaces[0]["id"]


def test_one_tenants_workspace_is_a_404_for_another(client: TestClient, clean_database) -> None:
    from app.services.auth import create_tenant_with_owner

    create_tenant_with_owner(
        TenantCreate(slug="alpha", name="Alpha", owner_email="a@x.example", owner_password=PASSWORD)
    )
    create_tenant_with_owner(
        TenantCreate(slug="beta", name="Beta", owner_email="b@x.example", owner_password=PASSWORD)
    )

    alpha_token = login(client, "a@x.example")
    beta_token = login(client, "b@x.example")
    alpha_ws = client.get("/api/v1/workspaces", headers=auth(alpha_token)).json()[0]["id"]

    response = client.get(f"/api/v1/workspaces/{alpha_ws}", headers=auth(beta_token))
    assert response.status_code == 404


def test_creating_a_tenant_needs_a_platform_operator(client: TestClient, acme) -> None:
    token = login(client, "owner@acme.example")
    response = client.post(
        "/api/v1/tenants",
        headers=auth(token),
        json={
            "slug": "sneaky",
            "name": "Sneaky",
            "owner_email": "s@x.example",
            "owner_password": PASSWORD,
        },
    )
    assert response.status_code == 403


def test_a_viewer_cannot_create_a_workspace(client: TestClient, acme) -> None:
    owner_token = login(client, "owner@acme.example")
    created = client.post(
        "/api/v1/tenants/current/members",
        headers=auth(owner_token),
        json={"email": "viewer@acme.example", "password": PASSWORD, "role": "viewer"},
    )
    assert created.status_code == 201

    viewer_token = login(client, "viewer@acme.example")
    response = client.post(
        "/api/v1/workspaces",
        headers=auth(viewer_token),
        json={"slug": "new", "name": "New"},
    )
    assert response.status_code == 403


def test_only_an_owner_may_grant_the_owner_role(client: TestClient, acme) -> None:
    owner_token = login(client, "owner@acme.example")
    client.post(
        "/api/v1/tenants/current/members",
        headers=auth(owner_token),
        json={"email": "admin@acme.example", "password": PASSWORD, "role": "admin"},
    )
    admin_token = login(client, "admin@acme.example")

    response = client.post(
        "/api/v1/tenants/current/members",
        headers=auth(admin_token),
        json={"email": "usurper@acme.example", "password": PASSWORD, "role": "owner"},
    )
    assert response.status_code == 403


# --------------------------------------------------------------------------
# brief and packages
# --------------------------------------------------------------------------
BRIEF = {
    "data": {
        "brand": "Acme",
        "description": "ابزارهای صنعتی",
        "voice": {"tone": ["حرفه‌ای", "صمیمی"]},
        "seed_keywords": {"fa": ["دریل برقی"], "en": ["power drill"]},
        "pillars": [{"name": "راهنمای خرید"}],
        "channels": ["wordpress", "telegram"],
        "video_mode": "voice",
        "locales": ["fa", "en"],
    }
}


def _workspace_id(client: TestClient, token: str) -> str:
    return client.get("/api/v1/workspaces", headers=auth(token)).json()[0]["id"]


def test_brief_versions_increment_and_only_one_stays_active(client: TestClient, acme) -> None:
    token = login(client, "owner@acme.example")
    ws = _workspace_id(client, token)

    first = client.post(f"/api/v1/workspaces/{ws}/briefs", headers=auth(token), json=BRIEF)
    second = client.post(f"/api/v1/workspaces/{ws}/briefs", headers=auth(token), json=BRIEF)
    assert first.json()["version"] == 1
    assert second.json()["version"] == 2

    briefs = client.get(f"/api/v1/workspaces/{ws}/briefs", headers=auth(token)).json()
    assert [b["is_active"] for b in briefs] == [True, False]

    active = client.get(f"/api/v1/workspaces/{ws}/briefs/active", headers=auth(token))
    assert active.json()["version"] == 2


def test_an_invalid_brief_is_refused(client: TestClient, acme) -> None:
    token = login(client, "owner@acme.example")
    ws = _workspace_id(client, token)
    bad = {"data": {"brand": "Acme", "locales": ["fr"]}}
    response = client.post(f"/api/v1/workspaces/{ws}/briefs", headers=auth(token), json=bad)
    assert response.status_code == 422


def test_a_package_needs_an_active_brief(client: TestClient, acme) -> None:
    token = login(client, "owner@acme.example")
    ws = _workspace_id(client, token)
    response = client.post(
        "/api/v1/packages",
        headers=auth(token),
        json={"workspace_id": ws, "title": "مقاله", "locale": "fa"},
    )
    assert response.status_code == 409
    assert "brand brief" in response.json()["error"]["message"]


def test_a_package_inherits_the_briefs_video_mode(client: TestClient, acme) -> None:
    token = login(client, "owner@acme.example")
    ws = _workspace_id(client, token)
    client.post(f"/api/v1/workspaces/{ws}/briefs", headers=auth(token), json=BRIEF)

    response = client.post(
        "/api/v1/packages",
        headers=auth(token),
        json={"workspace_id": ws, "title": "مقاله", "locale": "fa"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["video_mode"] == "voice"
    assert body["status"] == "planned"


def test_an_unconfigured_locale_is_refused(client: TestClient, acme) -> None:
    token = login(client, "owner@acme.example")
    ws = _workspace_id(client, token)
    client.post(f"/api/v1/workspaces/{ws}/briefs", headers=auth(token), json=BRIEF)

    response = client.post(
        "/api/v1/packages",
        headers=auth(token),
        json={"workspace_id": ws, "title": "مقال", "locale": "ar"},
    )
    assert response.status_code == 409


def test_gate_one_rejects_a_package_that_is_not_in_review(client: TestClient, acme) -> None:
    """A package cannot be waved through a gate it has not reached."""
    token = login(client, "owner@acme.example")
    ws = _workspace_id(client, token)
    client.post(f"/api/v1/workspaces/{ws}/briefs", headers=auth(token), json=BRIEF)
    package = client.post(
        "/api/v1/packages",
        headers=auth(token),
        json={"workspace_id": ws, "title": "مقاله", "locale": "fa"},
    ).json()

    response = client.post(
        f"/api/v1/packages/{package['id']}/gates/text",
        headers=auth(token),
        json={"decision": "approved"},
    )
    assert response.status_code == 409


def test_quota_endpoint_reports_what_today_still_buys(client: TestClient, acme) -> None:
    token = login(client, "owner@acme.example")
    body = client.get("/api/v1/gpu/quota", headers=auth(token)).json()

    assert body["tenant_id"] == str(acme.id)
    assert body["remaining_seconds"] > 0
    labels = {a["label"] for a in body["affordances"]}
    assert "text_only" in labels and "video_face" in labels


def test_unknown_routes_return_json(client: TestClient, acme) -> None:
    assert client.get("/api/v1/nope").status_code == 404
