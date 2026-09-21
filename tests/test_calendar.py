"""The workspace calendar (handoff section 11, phase 2): reading every
scheduled/published post plus holidays for a range, and rescheduling a
still-pending publication (what the panel's drag-and-drop calendar calls).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.db.enums import Channel, PublicationStatus
from app.db.models import ContentPackage, Publication, Variant
from app.schemas.tenant import TenantCreate
from app.services import calendar as calendar_service
from tests.conftest import requires_db

pytestmark = requires_db

PASSWORD = "correct-horse-battery"


# ---------------------------------------------------------------------------
# service (direct DB)
# ---------------------------------------------------------------------------
@pytest.fixture
def package(tenant_factory, system_db):
    tenant, workspace = tenant_factory("acme")
    row = ContentPackage(
        tenant_id=tenant.id, workspace_id=workspace.id, title="x", locale="fa"
    )
    system_db.add(row)
    system_db.flush()
    system_db.commit()
    return workspace, row


def make_publication(session, package, *, when, status=PublicationStatus.SCHEDULED):
    variant = Variant(
        tenant_id=package.tenant_id,
        package_id=package.id,
        channel=Channel.WORDPRESS,
        body={"hook": "قلاب", "body": "متن", "hashtags": []},
        is_selected=True,
    )
    session.add(variant)
    session.flush()
    publication = Publication(
        tenant_id=package.tenant_id,
        package_id=package.id,
        variant_id=variant.id,
        channel=Channel.WORDPRESS,
        status=status,
        scheduled_at=when,
    )
    session.add(publication)
    session.flush()
    return publication


def test_workspace_calendar_lists_publications_within_range(system_db, package) -> None:
    workspace, pkg = package
    inside = make_publication(system_db, pkg, when=datetime(2026, 3, 22, 10, tzinfo=UTC))
    make_publication(system_db, pkg, when=datetime(2026, 4, 5, 10, tzinfo=UTC))

    result = calendar_service.workspace_calendar(
        system_db, workspace, date(2026, 3, 20), date(2026, 3, 25)
    )
    assert [p.id for p in result.publications] == [inside.id]


def test_workspace_calendar_includes_holidays_in_range(system_db, package) -> None:
    workspace, _pkg = package
    result = calendar_service.workspace_calendar(
        system_db, workspace, date(2026, 3, 20), date(2026, 3, 25)
    )
    assert any(h.name_en == "Nowruz" for h in result.holidays)


def test_workspace_calendar_carries_the_workspace_settings(system_db, package) -> None:
    workspace, _pkg = package
    workspace.timezone = "Europe/Berlin"
    workspace.calendar = "gregorian"
    workspace.min_publish_spacing_minutes = 30
    system_db.flush()

    result = calendar_service.workspace_calendar(
        system_db, workspace, date(2026, 3, 20), date(2026, 3, 25)
    )
    assert result.timezone == "Europe/Berlin"
    assert result.calendar == "gregorian"
    assert result.min_publish_spacing_minutes == 30


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
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
            slug="acme", name="Acme", owner_email="owner@acme.example", owner_password=PASSWORD
        )
    )


def login(client: TestClient, email: str, password: str = PASSWORD) -> str:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _workspace_id(client: TestClient, token: str) -> str:
    return client.get("/api/v1/workspaces", headers=auth(token)).json()[0]["id"]


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


def test_get_calendar_returns_publications_and_holidays(client: TestClient, acme) -> None:
    token = login(client, "owner@acme.example")
    ws = _workspace_id(client, token)
    client.post(f"/api/v1/workspaces/{ws}/briefs", headers=auth(token), json=BRIEF)
    client.post(
        "/api/v1/packages",
        headers=auth(token),
        json={"workspace_id": ws, "title": "x", "locale": "fa"},
    )

    response = client.get(
        f"/api/v1/workspaces/{ws}/calendar",
        headers=auth(token),
        params={"start": "2026-03-20", "end": "2026-03-25"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert any(h["name_en"] == "Nowruz" for h in body["holidays"])
    assert body["publications"] == []


def test_get_calendar_rejects_end_before_start(client: TestClient, acme) -> None:
    token = login(client, "owner@acme.example")
    ws = _workspace_id(client, token)
    response = client.get(
        f"/api/v1/workspaces/{ws}/calendar",
        headers=auth(token),
        params={"start": "2026-03-25", "end": "2026-03-20"},
    )
    assert response.status_code == 409


def test_get_calendar_rejects_an_excessive_range(client: TestClient, acme) -> None:
    token = login(client, "owner@acme.example")
    ws = _workspace_id(client, token)
    response = client.get(
        f"/api/v1/workspaces/{ws}/calendar",
        headers=auth(token),
        params={"start": "2020-01-01", "end": "2030-01-01"},
    )
    assert response.status_code == 409


def test_rescheduling_a_publication_via_the_api(client: TestClient, acme) -> None:
    token = login(client, "owner@acme.example")
    ws = _workspace_id(client, token)
    client.post(f"/api/v1/workspaces/{ws}/briefs", headers=auth(token), json=BRIEF)
    package = client.post(
        "/api/v1/packages",
        headers=auth(token),
        json={"workspace_id": ws, "title": "x", "locale": "fa"},
    ).json()

    from app.db.tenancy import system_session

    with system_session() as session:
        variant = Variant(
            tenant_id=acme.id,
            package_id=package["id"],
            channel=Channel.WORDPRESS,
            body={"hook": "قلاب", "body": "متن", "hashtags": []},
            is_selected=True,
        )
        session.add(variant)
    variant_id = str(variant.id)

    when = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    publication = client.post(
        f"/api/v1/packages/{package['id']}/publications",
        headers=auth(token),
        json={"variant_id": variant_id, "scheduled_at": when},
    ).json()

    new_when = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    response = client.patch(
        f"/api/v1/packages/{package['id']}/publications/{publication['id']}",
        headers=auth(token),
        json={"scheduled_at": new_when},
    )
    assert response.status_code == 200, response.text
    assert response.json()["scheduled_at"] != publication["scheduled_at"]


def test_rescheduling_too_close_to_another_post_is_refused_via_the_api(
    client: TestClient, acme
) -> None:
    token = login(client, "owner@acme.example")
    ws = _workspace_id(client, token)
    client.post(f"/api/v1/workspaces/{ws}/briefs", headers=auth(token), json=BRIEF)
    package = client.post(
        "/api/v1/packages",
        headers=auth(token),
        json={"workspace_id": ws, "title": "x", "locale": "fa"},
    ).json()

    from app.db.tenancy import system_session

    variant_ids = []
    with system_session() as session:
        for _ in range(2):
            variant = Variant(
                tenant_id=acme.id,
                package_id=package["id"],
                channel=Channel.WORDPRESS,
                body={"hook": "قلاب", "body": "متن", "hashtags": []},
                is_selected=True,
            )
            session.add(variant)
            session.flush()
            variant_ids.append(str(variant.id))

    base = datetime.now(UTC) + timedelta(hours=2)
    client.post(
        f"/api/v1/packages/{package['id']}/publications",
        headers=auth(token),
        json={"variant_id": variant_ids[0], "scheduled_at": base.isoformat()},
    )
    second = client.post(
        f"/api/v1/packages/{package['id']}/publications",
        headers=auth(token),
        json={
            "variant_id": variant_ids[1],
            "scheduled_at": (base + timedelta(hours=3)).isoformat(),
        },
    ).json()

    response = client.patch(
        f"/api/v1/packages/{package['id']}/publications/{second['id']}",
        headers=auth(token),
        json={"scheduled_at": (base + timedelta(minutes=15)).isoformat()},
    )
    assert response.status_code == 409
