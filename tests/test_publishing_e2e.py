"""Publishing end to end: schedule → the beat sweep finds it due → the
connector is called against mocked HTTP → the row reflects what happened.

The queue, the leases and the retry state machine are all the production
code path; only the HTTP the connector speaks to is mocked, the same as
``tests/test_connectors.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.db.enums import Channel, PackageStatus, PublicationStatus
from app.db.models import ContentPackage, Variant
from app.services import channel_credentials as creds
from app.services import publishing, storage
from app.worker.tasks.publish import dispatch, run
from tests.conftest import requires_db

pytestmark = requires_db

WORDPRESS_PAYLOAD = {
    "site_url": "https://acme.example",
    "username": "bot",
    "application_password": "abcd efgh",
}


@pytest.fixture(autouse=True)
def _memory_storage():
    storage.set_backend(storage.InMemoryStorageBackend())
    yield
    storage.set_backend(None)


@pytest.fixture
def package_and_variant(tenant_factory, system_db):
    tenant, workspace = tenant_factory("acme")
    package = ContentPackage(
        tenant_id=tenant.id,
        workspace_id=workspace.id,
        title="راهنمای خرید دریل برقی",
        locale="fa",
        status=PackageStatus.SELECTION,
        article={"title": "راهنمای خرید دریل برقی", "slug": "drill-guide"},
    )
    system_db.add(package)
    system_db.flush()

    variant = Variant(
        tenant_id=tenant.id,
        package_id=package.id,
        channel=Channel.WORDPRESS,
        body={"hook": "قلاب", "body": "متن پست"},
        is_selected=True,
    )
    system_db.add(variant)
    system_db.flush()

    creds.create_credential(
        system_db, tenant.id, workspace.id, Channel.WORDPRESS, WORDPRESS_PAYLOAD
    )
    system_db.commit()
    return package, variant


#: Captured once, before any test patches ``httpx.Client`` — a second call
#: to ``mock_wordpress`` within one test must swap the handler, not wrap the
#: previous mock and recurse into whatever it returned.
_REAL_HTTPX_CLIENT = httpx.Client


def mock_wordpress(monkeypatch, response: httpx.Response) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return response

    def patched_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return _REAL_HTTPX_CLIENT(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", patched_client)


def test_dispatch_finds_a_due_publication_and_runs_it(
    package_and_variant, system_db, monkeypatch
) -> None:
    package, variant = package_and_variant
    mock_wordpress(
        monkeypatch, httpx.Response(201, json={"id": 7, "link": "https://acme.example/?p=7"})
    )

    publication = publishing.schedule_publication(
        system_db, package, variant, datetime.now(UTC) - timedelta(minutes=1)
    )
    system_db.commit()

    result = dispatch()
    assert result["queued"] == 1

    run(str(publication.id))

    system_db.refresh(publication)
    assert publication.status is PublicationStatus.PUBLISHED
    assert publication.external_id == "7"
    assert publication.external_url == "https://acme.example/?p=7"


def test_dispatch_ignores_what_is_not_due_yet(package_and_variant, system_db) -> None:
    package, variant = package_and_variant
    publishing.schedule_publication(
        system_db, package, variant, datetime.now(UTC) + timedelta(hours=1)
    )
    system_db.commit()

    assert dispatch()["queued"] == 0


def test_a_failing_site_is_retried_across_two_sweeps_then_succeeds(
    package_and_variant, system_db, monkeypatch
) -> None:
    package, variant = package_and_variant
    publication = publishing.schedule_publication(
        system_db, package, variant, datetime.now(UTC) - timedelta(minutes=1)
    )
    system_db.commit()

    mock_wordpress(monkeypatch, httpx.Response(503, text="Service Unavailable"))
    run(str(publication.id))
    system_db.refresh(publication)
    assert publication.status is PublicationStatus.SCHEDULED
    assert publication.attempt_count == 1

    # The site recovers before the next sweep.
    mock_wordpress(
        monkeypatch, httpx.Response(201, json={"id": 3, "link": "https://acme.example/?p=3"})
    )
    still_due = dispatch()
    assert still_due["queued"] == 1
    run(str(publication.id))

    system_db.refresh(publication)
    assert publication.status is PublicationStatus.PUBLISHED
    assert publication.attempt_count == 2


def test_a_permanently_down_site_alerts_the_operator_after_three_tries(
    package_and_variant, system_db, monkeypatch
) -> None:
    package, variant = package_and_variant
    publication = publishing.schedule_publication(
        system_db, package, variant, datetime.now(UTC) - timedelta(minutes=1)
    )
    system_db.commit()
    mock_wordpress(monkeypatch, httpx.Response(500, text="Internal Server Error"))

    for _ in range(publishing.MAX_ATTEMPTS):
        run(str(publication.id))

    system_db.refresh(publication)
    assert publication.status is PublicationStatus.FAILED
    assert publication.operator_alerted is True
    assert dispatch()["queued"] == 0  # exhausted, no longer swept


def test_publishing_the_selected_image_reaches_wordpress(
    package_and_variant, system_db, monkeypatch
) -> None:
    from app.db.enums import MediaKind
    from app.db.models import MediaAsset

    package, variant = package_and_variant
    asset = MediaAsset(
        tenant_id=package.tenant_id,
        package_id=package.id,
        kind=MediaKind.IMAGE,
        storage_key="acme/hero.png",
        mime_type="image/png",
        is_selected=True,
    )
    system_db.add(asset)
    system_db.flush()
    storage.get_backend().put("acme/hero.png", b"\x89PNG\r\n\x1a\n...", "image/png")
    system_db.commit()

    seen_media_upload = {"happened": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/wp-json/wp/v2/media":
            seen_media_upload["happened"] = True
            assert request.content == b"\x89PNG\r\n\x1a\n..."
            return httpx.Response(201, json={"id": 9})
        return httpx.Response(201, json={"id": 1, "link": None})

    def patched_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return _REAL_HTTPX_CLIENT(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", patched_client)

    publication = publishing.schedule_publication(
        system_db, package, variant, datetime.now(UTC) - timedelta(minutes=1)
    )
    system_db.commit()
    run(str(publication.id))

    assert seen_media_upload["happened"] is True
    system_db.refresh(publication)
    assert publication.status is PublicationStatus.PUBLISHED
