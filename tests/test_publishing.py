"""Scheduling and attempting a publication: content gathering, and the
three-attempts-then-alert-the-operator state machine (handoff section 3,
step 6).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.connectors import ConnectorError, PublishResult
from app.core.errors import InvalidStateError
from app.db.enums import Channel, MediaKind, PackageStatus, PublicationStatus
from app.db.models import ContentPackage, MediaAsset, Variant
from app.services import channel_credentials as creds
from app.services import publishing, storage
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
def package(tenant_factory, system_db):
    tenant, workspace = tenant_factory("acme")
    row = ContentPackage(
        tenant_id=tenant.id,
        workspace_id=workspace.id,
        title="راهنمای خرید دریل برقی",
        locale="fa",
        status=PackageStatus.SELECTION,
        article={
            "title": "راهنمای خرید دریل برقی",
            "slug": "drill-guide",
            "sections": [{"heading": "مقدمه", "level": 2, "body": "متن."}],
        },
    )
    system_db.add(row)
    system_db.flush()
    system_db.commit()
    return row


@pytest.fixture
def variant(system_db, package):
    row = Variant(
        tenant_id=package.tenant_id,
        package_id=package.id,
        channel=Channel.WORDPRESS,
        body={"hook": "قلاب", "body": "متن پست", "hashtags": []},
        is_selected=True,
    )
    system_db.add(row)
    system_db.flush()
    return row


def make_credential(session, package):
    return creds.create_credential(
        session, package.tenant_id, package.workspace_id, Channel.WORDPRESS, WORDPRESS_PAYLOAD
    )


# ---------------------------------------------------------------------------
# scheduling
# ---------------------------------------------------------------------------
def test_scheduling_a_selected_variant_creates_a_publication(system_db, package, variant) -> None:
    when = datetime.now(UTC) + timedelta(hours=1)
    publication = publishing.schedule_publication(system_db, package, variant, when)
    assert publication.status is PublicationStatus.SCHEDULED
    assert publication.channel is Channel.WORDPRESS
    assert publication.scheduled_at == when


def test_an_unselected_variant_cannot_be_scheduled(system_db, package, variant) -> None:
    variant.is_selected = False
    system_db.flush()
    with pytest.raises(InvalidStateError):
        publishing.schedule_publication(system_db, package, variant, datetime.now(UTC))


def test_an_x_variant_cannot_be_scheduled(system_db, package) -> None:
    """X has no publish connector (handoff section 11) —
    app.services.x_export is how it gets published, not scheduling."""
    x_variant = Variant(
        tenant_id=package.tenant_id,
        package_id=package.id,
        channel=Channel.X,
        body={"hook": "قلاب", "body": "متن پست", "hashtags": []},
        is_selected=True,
    )
    system_db.add(x_variant)
    system_db.flush()
    with pytest.raises(InvalidStateError, match="x-export"):
        publishing.schedule_publication(system_db, package, x_variant, datetime.now(UTC))


def test_a_variant_from_another_package_cannot_be_scheduled(
    system_db, package, variant, tenant_factory
) -> None:
    _, other_workspace = tenant_factory("globex")
    other_package = ContentPackage(
        tenant_id=package.tenant_id, workspace_id=other_workspace.id, title="x", locale="fa"
    )
    system_db.add(other_package)
    system_db.flush()
    with pytest.raises(InvalidStateError):
        publishing.schedule_publication(system_db, other_package, variant, datetime.now(UTC))


# ---------------------------------------------------------------------------
# what is due
# ---------------------------------------------------------------------------
def test_due_publications_finds_what_is_scheduled_and_past_due(system_db, package, variant) -> None:
    past = publishing.schedule_publication(
        system_db, package, variant, datetime.now(UTC) - timedelta(minutes=5)
    )
    publishing.schedule_publication(
        system_db, package, variant, datetime.now(UTC) + timedelta(hours=1)
    )
    system_db.commit()

    due = publishing.due_publications(system_db)
    assert [p.id for p in due] == [past.id]


def test_due_publications_excludes_exhausted_attempts(system_db, package, variant) -> None:
    publication = publishing.schedule_publication(
        system_db, package, variant, datetime.now(UTC) - timedelta(minutes=5)
    )
    publication.attempt_count = publishing.MAX_ATTEMPTS
    system_db.commit()

    assert publishing.due_publications(system_db) == []


# ---------------------------------------------------------------------------
# gathering content
# ---------------------------------------------------------------------------
def test_gather_content_uses_the_assembled_article(system_db, package, variant) -> None:
    publication = publishing.schedule_publication(system_db, package, variant, datetime.now(UTC))
    content = publishing.gather_content(system_db, publication)
    assert content.article["slug"] == "drill-guide"
    assert content.hook == "قلاب"


def test_gather_content_only_includes_selected_images(system_db, package, variant) -> None:
    backend = storage.get_backend()
    selected = MediaAsset(
        tenant_id=package.tenant_id,
        package_id=package.id,
        kind=MediaKind.IMAGE,
        storage_key="acme/selected.png",
        mime_type="image/png",
        is_selected=True,
    )
    unselected = MediaAsset(
        tenant_id=package.tenant_id,
        package_id=package.id,
        kind=MediaKind.IMAGE,
        storage_key="acme/unselected.png",
        mime_type="image/png",
        is_selected=False,
    )
    system_db.add_all([selected, unselected])
    system_db.flush()
    backend.put("acme/selected.png", b"selected-bytes", "image/png")
    backend.put("acme/unselected.png", b"unselected-bytes", "image/png")

    publication = publishing.schedule_publication(system_db, package, variant, datetime.now(UTC))
    content = publishing.gather_content(system_db, publication)

    assert len(content.media) == 1
    assert content.media[0].data == b"selected-bytes"


def test_gather_content_skips_a_selected_asset_missing_from_storage(
    system_db, package, variant
) -> None:
    """Storage and the database can disagree; a missing object must not
    crash the whole publish attempt."""
    asset = MediaAsset(
        tenant_id=package.tenant_id,
        package_id=package.id,
        kind=MediaKind.IMAGE,
        storage_key="acme/gone.png",
        mime_type="image/png",
        is_selected=True,
    )
    system_db.add(asset)
    system_db.flush()

    publication = publishing.schedule_publication(system_db, package, variant, datetime.now(UTC))
    content = publishing.gather_content(system_db, publication)
    assert content.media == ()


# ---------------------------------------------------------------------------
# attempting a publish
# ---------------------------------------------------------------------------
def test_a_successful_attempt_marks_the_publication_published(
    system_db, package, variant, monkeypatch
) -> None:
    make_credential(system_db, package)
    publication = publishing.schedule_publication(system_db, package, variant, datetime.now(UTC))
    system_db.commit()

    monkeypatch.setattr(
        "app.connectors.wordpress.WordPressConnector.publish",
        lambda self, content, credential: PublishResult(
            external_id="7", external_url="https://acme.example/?p=7"
        ),
    )

    publishing.attempt(system_db, publication)

    assert publication.status is PublicationStatus.PUBLISHED
    assert publication.external_id == "7"
    assert publication.published_at is not None
    assert publication.attempt_count == 1


def test_a_successful_attempt_moves_a_scheduled_package_to_published(
    system_db, package, variant, monkeypatch
) -> None:
    """The package-wide status only tracks "has this gone out anywhere
    yet" — the first publication to succeed is what starts the measuring
    stage (handoff section 3, step 7; see app.services.analytics)."""
    make_credential(system_db, package)
    publication = publishing.schedule_publication(system_db, package, variant, datetime.now(UTC))
    package.status = PackageStatus.SCHEDULED
    system_db.commit()

    monkeypatch.setattr(
        "app.connectors.wordpress.WordPressConnector.publish",
        lambda self, content, credential: PublishResult(external_id="7", external_url=None),
    )
    publishing.attempt(system_db, publication)

    assert package.status is PackageStatus.PUBLISHED


def test_a_package_not_yet_scheduled_is_left_alone_by_a_publish_attempt(
    system_db, package, variant, monkeypatch
) -> None:
    """A package can carry more than one channel variant; a second
    publication's own success must not re-trigger (or fight) a transition
    the first one already made."""
    make_credential(system_db, package)
    publication = publishing.schedule_publication(system_db, package, variant, datetime.now(UTC))
    package.status = PackageStatus.PUBLISHED  # as if an earlier publication already moved it
    system_db.commit()

    monkeypatch.setattr(
        "app.connectors.wordpress.WordPressConnector.publish",
        lambda self, content, credential: PublishResult(external_id="7", external_url=None),
    )
    publishing.attempt(system_db, publication)

    assert package.status is PackageStatus.PUBLISHED  # unchanged, not re-transitioned


def test_a_failed_attempt_stays_scheduled_below_the_limit(
    system_db, package, variant, monkeypatch
) -> None:
    make_credential(system_db, package)
    publication = publishing.schedule_publication(system_db, package, variant, datetime.now(UTC))
    system_db.commit()

    monkeypatch.setattr(
        "app.connectors.wordpress.WordPressConnector.publish",
        lambda self, content, credential: (_ for _ in ()).throw(ConnectorError("site unreachable")),
    )

    publishing.attempt(system_db, publication)

    assert publication.status is PublicationStatus.SCHEDULED
    assert publication.attempt_count == 1
    assert "site unreachable" in publication.last_error
    assert publication.operator_alerted is False


def test_the_third_failed_attempt_alerts_the_operator(
    system_db, package, variant, monkeypatch
) -> None:
    make_credential(system_db, package)
    publication = publishing.schedule_publication(system_db, package, variant, datetime.now(UTC))
    system_db.commit()

    monkeypatch.setattr(
        "app.connectors.wordpress.WordPressConnector.publish",
        lambda self, content, credential: (_ for _ in ()).throw(ConnectorError("still down")),
    )

    for _ in range(publishing.MAX_ATTEMPTS):
        publishing.attempt(system_db, publication)

    assert publication.attempt_count == publishing.MAX_ATTEMPTS
    assert publication.status is PublicationStatus.FAILED
    assert publication.operator_alerted is True


def test_a_publish_with_no_credential_configured_is_a_retryable_failure(
    system_db, package, variant
) -> None:
    """No credential yet is the customer's to fix, same as a network error —
    it should not immediately exhaust the attempt budget with a crash."""
    publication = publishing.schedule_publication(system_db, package, variant, datetime.now(UTC))
    system_db.commit()

    publishing.attempt(system_db, publication)

    assert publication.status is PublicationStatus.SCHEDULED
    assert "no active" in publication.last_error


def test_recovering_after_a_failed_attempt_still_publishes(
    system_db, package, variant, monkeypatch
) -> None:
    make_credential(system_db, package)
    publication = publishing.schedule_publication(system_db, package, variant, datetime.now(UTC))
    system_db.commit()

    calls = {"n": 0}

    def flaky(self, content, credential):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectorError("temporary hiccup")
        return PublishResult(external_id="9", external_url=None)

    monkeypatch.setattr("app.connectors.wordpress.WordPressConnector.publish", flaky)

    publishing.attempt(system_db, publication)
    assert publication.status is PublicationStatus.SCHEDULED
    publishing.attempt(system_db, publication)
    assert publication.status is PublicationStatus.PUBLISHED
    assert publication.attempt_count == 2


# ---------------------------------------------------------------------------
# cancelling
# ---------------------------------------------------------------------------
def test_cancelling_a_scheduled_publication(system_db, package, variant) -> None:
    publication = publishing.schedule_publication(system_db, package, variant, datetime.now(UTC))
    publishing.cancel_publication(system_db, publication)
    assert publication.status is PublicationStatus.CANCELLED


def test_a_published_publication_cannot_be_cancelled(system_db, package, variant) -> None:
    publication = publishing.schedule_publication(system_db, package, variant, datetime.now(UTC))
    publication.status = PublicationStatus.PUBLISHED
    with pytest.raises(InvalidStateError):
        publishing.cancel_publication(system_db, publication)


def test_a_cancelled_publication_does_not_show_up_as_due(system_db, package, variant) -> None:
    publication = publishing.schedule_publication(
        system_db, package, variant, datetime.now(UTC) - timedelta(minutes=1)
    )
    publishing.cancel_publication(system_db, publication)
    system_db.commit()
    assert publishing.due_publications(system_db) == []
