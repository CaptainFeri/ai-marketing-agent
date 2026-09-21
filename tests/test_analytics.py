"""Pulling metrics for a workspace: matching provider rows to publications
by URL (Search Console) or path (GA4), writing snapshots, and advancing a
package from ``published`` to ``measuring`` once one of its own
publications actually gets measured.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)
from sqlalchemy import select

from app.db.enums import AnalyticsProvider, Channel, PackageStatus, PublicationStatus
from app.db.models import ContentPackage, MetricSnapshot, Publication
from app.services import analytics, analytics_credentials, channel_credentials
from tests.conftest import requires_db

pytestmark = requires_db

DAY = date(2026, 1, 15)

#: A real RSA key — the service account's key must actually be signable
#: (app.connectors.google_auth builds a genuine JWT assertion from it), not
#: just present as a string the pydantic schema is happy with.
_PRIVATE_KEY_PEM = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
    encoding=Encoding.PEM, format=PrivateFormat.PKCS8, encryption_algorithm=NoEncryption()
).decode()

SEARCH_CONSOLE_PAYLOAD = {
    "site_url": "https://acme.example/",
    "service_account": {
        "client_email": "svc@acme.iam.gserviceaccount.com",
        "private_key": _PRIVATE_KEY_PEM,
    },
}
GA4_PAYLOAD = {
    "property_id": "999",
    "service_account": {
        "client_email": "svc@acme.iam.gserviceaccount.com",
        "private_key": _PRIVATE_KEY_PEM,
    },
}


def mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def token_response() -> httpx.Response:
    return httpx.Response(200, json={"access_token": "fake", "expires_in": 3600})


def search_console_client(rows: list[dict]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if "searchAnalytics" not in request.url.path:
            return token_response()
        return httpx.Response(200, json={"rows": rows})

    return mock_client(handler)


def ga4_client(rows: list[dict]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if "runReport" not in request.url.path:
            return token_response()
        return httpx.Response(200, json={"rows": rows})

    return mock_client(handler)


def instagram_client(insights_by_media_id: dict[str, list[dict]]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        media_id = request.url.path.rsplit("/", 2)[-2]
        return httpx.Response(200, json={"data": insights_by_media_id.get(media_id, [])})

    return mock_client(handler)


def linkedin_client(stats_by_urn: dict[str, dict]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        urn = request.url.params.get("shares[0]")
        stats = stats_by_urn.get(urn or "")
        elements = [{"totalShareStatistics": stats}] if stats else []
        return httpx.Response(200, json={"elements": elements})

    return mock_client(handler)


@pytest.fixture
def package_and_publication(tenant_factory, system_db):
    tenant, workspace = tenant_factory("acme")
    package = ContentPackage(
        tenant_id=tenant.id,
        workspace_id=workspace.id,
        title="راهنمای خرید دریل برقی",
        locale="fa",
        status=PackageStatus.SCHEDULED,
    )
    system_db.add(package)
    system_db.flush()
    publication = Publication(
        tenant_id=tenant.id,
        package_id=package.id,
        channel=Channel.WORDPRESS,
        status=PublicationStatus.PUBLISHED,
        scheduled_at=datetime.now(UTC),
        published_at=datetime.now(UTC),
        external_url="https://acme.example/drill-guide",
    )
    system_db.add(publication)
    system_db.flush()
    system_db.commit()
    return tenant, workspace, package, publication


def test_no_configured_provider_writes_nothing(package_and_publication, system_db) -> None:
    _, workspace, _, _ = package_and_publication
    written = analytics.pull_metrics_for_workspace(system_db, workspace.id, DAY)
    assert written == 0


def test_search_console_metrics_are_matched_by_exact_url(
    package_and_publication, system_db
) -> None:
    tenant, workspace, package, publication = package_and_publication
    analytics_credentials.create_credential(
        system_db, tenant.id, workspace.id, AnalyticsProvider.SEARCH_CONSOLE, SEARCH_CONSOLE_PAYLOAD
    )
    system_db.commit()

    rows = [
        {
            "keys": ["https://acme.example/drill-guide"],
            "clicks": 5,
            "impressions": 80,
            "ctr": 0.0625,
            "position": 12.3,
        },
        {"keys": ["https://acme.example/unrelated-page"], "clicks": 99, "impressions": 999},
    ]
    written = analytics.pull_metrics_for_workspace(
        system_db, workspace.id, DAY, search_console_client=search_console_client(rows)
    )
    assert written == 1

    snapshot = system_db.scalars(
        select(MetricSnapshot).where(MetricSnapshot.publication_id == publication.id)
    ).one()
    assert snapshot.source == "search_console"
    assert snapshot.clicks == 5
    assert snapshot.impressions == 80
    assert snapshot.position == pytest.approx(12.3)
    assert snapshot.metrics == {"ctr": 0.0625}


def test_ga4_metrics_are_matched_by_url_path(package_and_publication, system_db) -> None:
    tenant, workspace, package, publication = package_and_publication
    analytics_credentials.create_credential(
        system_db, tenant.id, workspace.id, AnalyticsProvider.GA4, GA4_PAYLOAD
    )
    system_db.commit()

    rows = [
        {
            "dimensionValues": [{"value": "/drill-guide"}],
            "metricValues": [{"value": "40"}, {"value": "30"}, {"value": "22"}, {"value": "55.5"}],
        }
    ]
    written = analytics.pull_metrics_for_workspace(
        system_db, workspace.id, DAY, ga4_client=ga4_client(rows)
    )
    assert written == 1

    snapshot = system_db.scalars(
        select(MetricSnapshot).where(MetricSnapshot.publication_id == publication.id)
    ).one()
    assert snapshot.source == "ga4"
    assert snapshot.metrics["screen_page_views"] == 40
    assert snapshot.metrics["sessions"] == 30


def test_both_providers_write_two_snapshots_for_one_publication(
    package_and_publication, system_db
) -> None:
    tenant, workspace, package, publication = package_and_publication
    analytics_credentials.create_credential(
        system_db, tenant.id, workspace.id, AnalyticsProvider.SEARCH_CONSOLE, SEARCH_CONSOLE_PAYLOAD
    )
    analytics_credentials.create_credential(
        system_db, tenant.id, workspace.id, AnalyticsProvider.GA4, GA4_PAYLOAD
    )
    system_db.commit()

    sc_rows = [{"keys": ["https://acme.example/drill-guide"], "clicks": 1, "impressions": 2}]
    ga_rows = [
        {
            "dimensionValues": [{"value": "/drill-guide"}],
            "metricValues": [{"value": "1"}, {"value": "1"}, {"value": "1"}, {"value": "1"}],
        }
    ]
    written = analytics.pull_metrics_for_workspace(
        system_db,
        workspace.id,
        DAY,
        search_console_client=search_console_client(sc_rows),
        ga4_client=ga4_client(ga_rows),
    )
    assert written == 2
    sources = set(
        system_db.scalars(
            select(MetricSnapshot.source).where(MetricSnapshot.publication_id == publication.id)
        ).all()
    )
    assert sources == {"search_console", "ga4"}


def test_a_second_pull_for_the_same_day_updates_rather_than_duplicates(
    package_and_publication, system_db
) -> None:
    tenant, workspace, package, publication = package_and_publication
    analytics_credentials.create_credential(
        system_db, tenant.id, workspace.id, AnalyticsProvider.SEARCH_CONSOLE, SEARCH_CONSOLE_PAYLOAD
    )
    system_db.commit()

    first_rows = [{"keys": ["https://acme.example/drill-guide"], "clicks": 1, "impressions": 2}]
    analytics.pull_metrics_for_workspace(
        system_db, workspace.id, DAY, search_console_client=search_console_client(first_rows)
    )
    system_db.commit()

    second_rows = [{"keys": ["https://acme.example/drill-guide"], "clicks": 9, "impressions": 90}]
    analytics.pull_metrics_for_workspace(
        system_db, workspace.id, DAY, search_console_client=search_console_client(second_rows)
    )
    system_db.commit()

    snapshots = system_db.scalars(
        select(MetricSnapshot).where(MetricSnapshot.publication_id == publication.id)
    ).all()
    assert len(snapshots) == 1
    assert snapshots[0].clicks == 9


def test_a_measured_publication_moves_its_package_to_measuring(
    package_and_publication, system_db
) -> None:
    tenant, workspace, package, publication = package_and_publication
    package.status = PackageStatus.PUBLISHED
    system_db.commit()

    analytics_credentials.create_credential(
        system_db, tenant.id, workspace.id, AnalyticsProvider.SEARCH_CONSOLE, SEARCH_CONSOLE_PAYLOAD
    )
    system_db.commit()
    rows = [{"keys": ["https://acme.example/drill-guide"], "clicks": 1, "impressions": 2}]
    analytics.pull_metrics_for_workspace(
        system_db, workspace.id, DAY, search_console_client=search_console_client(rows)
    )

    # `package` is already the object pull_metrics_for_workspace mutated
    # (same session, same identity map) — a refresh() here would just
    # reload it from the database before the pending change is flushed and
    # silently undo the very thing this test is checking.
    assert package.status is PackageStatus.MEASURING


def test_an_unmatched_publication_does_not_advance_its_package(
    package_and_publication, system_db
) -> None:
    """Some other publication in the workspace getting measured must not
    move a package whose own publication had no matching row."""
    tenant, workspace, package, publication = package_and_publication
    package.status = PackageStatus.PUBLISHED
    system_db.commit()

    analytics_credentials.create_credential(
        system_db, tenant.id, workspace.id, AnalyticsProvider.SEARCH_CONSOLE, SEARCH_CONSOLE_PAYLOAD
    )
    system_db.commit()
    rows = [{"keys": ["https://acme.example/some-other-page"], "clicks": 1, "impressions": 2}]
    written = analytics.pull_metrics_for_workspace(
        system_db, workspace.id, DAY, search_console_client=search_console_client(rows)
    )

    assert written == 0
    assert package.status is PackageStatus.PUBLISHED


def test_one_providers_failure_does_not_block_the_other(
    package_and_publication, system_db
) -> None:
    tenant, workspace, package, publication = package_and_publication
    analytics_credentials.create_credential(
        system_db, tenant.id, workspace.id, AnalyticsProvider.SEARCH_CONSOLE, SEARCH_CONSOLE_PAYLOAD
    )
    analytics_credentials.create_credential(
        system_db, tenant.id, workspace.id, AnalyticsProvider.GA4, GA4_PAYLOAD
    )
    system_db.commit()

    def broken_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    ga_rows = [
        {
            "dimensionValues": [{"value": "/drill-guide"}],
            "metricValues": [{"value": "1"}, {"value": "1"}, {"value": "1"}, {"value": "1"}],
        }
    ]
    written = analytics.pull_metrics_for_workspace(
        system_db,
        workspace.id,
        DAY,
        search_console_client=mock_client(broken_handler),
        ga4_client=ga4_client(ga_rows),
    )
    assert written == 1
    sources = set(
        system_db.scalars(
            select(MetricSnapshot.source).where(MetricSnapshot.publication_id == publication.id)
        ).all()
    )
    assert sources == {"ga4"}


def test_a_publication_with_no_external_url_is_never_queried(
    tenant_factory, system_db
) -> None:
    tenant, workspace = tenant_factory("acme")
    package = ContentPackage(
        tenant_id=tenant.id, workspace_id=workspace.id, title="x", locale="fa"
    )
    system_db.add(package)
    system_db.flush()
    publication = Publication(
        tenant_id=tenant.id,
        package_id=package.id,
        channel=Channel.TELEGRAM,
        status=PublicationStatus.PUBLISHED,
        scheduled_at=datetime.now(UTC),
        external_url=None,
    )
    system_db.add(publication)
    system_db.commit()

    analytics_credentials.create_credential(
        system_db, tenant.id, workspace.id, AnalyticsProvider.SEARCH_CONSOLE, SEARCH_CONSOLE_PAYLOAD
    )
    system_db.commit()

    written = analytics.pull_metrics_for_workspace(
        system_db, workspace.id, DAY, search_console_client=search_console_client([])
    )
    assert written == 0


def test_yesterday_defaults_correctly() -> None:
    assert analytics.yesterday(date(2026, 3, 2)) == date(2026, 3, 1)


def test_a_pull_that_measures_both_ab_arms_records_a_result(tenant_factory, system_db) -> None:
    """The daily metrics sweep is what actually keeps an A/B result fresh
    (``app.services.ab_testing`` is a pure comparison; this is its wiring)."""
    from app.db.models import ABTestResult, Variant

    tenant, workspace = tenant_factory("acme")
    package = ContentPackage(
        tenant_id=tenant.id,
        workspace_id=workspace.id,
        title="راهنمای خرید دریل برقی",
        locale="fa",
        status=PackageStatus.MEASURING,
    )
    system_db.add(package)
    system_db.flush()

    variant_a = Variant(
        tenant_id=tenant.id,
        package_id=package.id,
        channel=Channel.WORDPRESS,
        ab_label="a",
        body={"hook": "قلاب الف", "body": "متن", "hashtags": []},
        is_selected=True,
    )
    variant_b = Variant(
        tenant_id=tenant.id,
        package_id=package.id,
        channel=Channel.WORDPRESS,
        ab_label="b",
        body={"hook": "قلاب ب", "body": "متن", "hashtags": []},
        is_selected=True,
    )
    system_db.add_all([variant_a, variant_b])
    system_db.flush()

    pub_a = Publication(
        tenant_id=tenant.id,
        package_id=package.id,
        variant_id=variant_a.id,
        channel=Channel.WORDPRESS,
        status=PublicationStatus.PUBLISHED,
        scheduled_at=datetime.now(UTC),
        published_at=datetime.now(UTC),
        external_url="https://acme.example/drill-guide-a",
    )
    pub_b = Publication(
        tenant_id=tenant.id,
        package_id=package.id,
        variant_id=variant_b.id,
        channel=Channel.WORDPRESS,
        status=PublicationStatus.PUBLISHED,
        scheduled_at=datetime.now(UTC),
        published_at=datetime.now(UTC),
        external_url="https://acme.example/drill-guide-b",
    )
    system_db.add_all([pub_a, pub_b])
    system_db.flush()

    analytics_credentials.create_credential(
        system_db, tenant.id, workspace.id, AnalyticsProvider.SEARCH_CONSOLE, SEARCH_CONSOLE_PAYLOAD
    )
    system_db.commit()

    rows = [
        {"keys": ["https://acme.example/drill-guide-a"], "clicks": 2, "impressions": 200},
        {"keys": ["https://acme.example/drill-guide-b"], "clicks": 20, "impressions": 200},
    ]
    analytics.pull_metrics_for_workspace(
        system_db, workspace.id, DAY, search_console_client=search_console_client(rows)
    )

    result = system_db.scalars(
        select(ABTestResult).where(ABTestResult.package_id == package.id)
    ).one()
    assert result.winner_variant_id == variant_b.id


# --------------------------------------------------------------------------
# Instagram/LinkedIn social insights (handoff section 11, phase 2)
# --------------------------------------------------------------------------
@pytest.fixture
def instagram_package_and_publication(tenant_factory, system_db):
    tenant, workspace = tenant_factory("acme")
    package = ContentPackage(
        tenant_id=tenant.id,
        workspace_id=workspace.id,
        title="x",
        locale="fa",
        status=PackageStatus.SCHEDULED,
    )
    system_db.add(package)
    system_db.flush()
    publication = Publication(
        tenant_id=tenant.id,
        package_id=package.id,
        channel=Channel.INSTAGRAM,
        status=PublicationStatus.PUBLISHED,
        scheduled_at=datetime.now(UTC),
        published_at=datetime.now(UTC),
        external_id="media-1",
        external_url="https://www.instagram.com/p/media-1/",
    )
    system_db.add(publication)
    system_db.flush()
    system_db.commit()
    return tenant, workspace, package, publication


def test_instagram_insights_are_matched_by_external_id(
    instagram_package_and_publication, system_db
) -> None:
    tenant, workspace, package, publication = instagram_package_and_publication
    channel_credentials.create_credential(
        system_db,
        tenant.id,
        workspace.id,
        Channel.INSTAGRAM,
        {"access_token": "IGQ...token", "ig_user_id": "17841400000000000"},
    )
    system_db.commit()

    insights = {
        "media-1": [
            {"name": "reach", "values": [{"value": 300}]},
            {"name": "likes", "values": [{"value": 25}]},
        ]
    }
    written = analytics.pull_metrics_for_workspace(
        system_db, workspace.id, DAY, instagram_client=instagram_client(insights)
    )
    assert written == 1

    snapshot = system_db.scalars(
        select(MetricSnapshot).where(MetricSnapshot.publication_id == publication.id)
    ).one()
    assert snapshot.source == "instagram"
    assert snapshot.impressions is None and snapshot.clicks is None
    assert snapshot.metrics == {"reach": 300, "likes": 25}


def test_instagram_insights_with_no_credential_writes_nothing(
    instagram_package_and_publication, system_db
) -> None:
    _, workspace, _, _ = instagram_package_and_publication
    written = analytics.pull_metrics_for_workspace(
        system_db, workspace.id, DAY, instagram_client=instagram_client({})
    )
    assert written == 0


@pytest.fixture
def linkedin_package_and_publication(tenant_factory, system_db):
    tenant, workspace = tenant_factory("acme")
    package = ContentPackage(
        tenant_id=tenant.id,
        workspace_id=workspace.id,
        title="x",
        locale="fa",
        status=PackageStatus.SCHEDULED,
    )
    system_db.add(package)
    system_db.flush()
    publication = Publication(
        tenant_id=tenant.id,
        package_id=package.id,
        channel=Channel.LINKEDIN,
        status=PublicationStatus.PUBLISHED,
        scheduled_at=datetime.now(UTC),
        published_at=datetime.now(UTC),
        external_id="urn:li:share:1",
        external_url="https://www.linkedin.com/feed/update/urn:li:share:1/",
    )
    system_db.add(publication)
    system_db.flush()
    system_db.commit()
    return tenant, workspace, package, publication


def test_linkedin_insights_carry_real_impressions_and_clicks(
    linkedin_package_and_publication, system_db
) -> None:
    """Unlike Instagram (reach/engagement only), LinkedIn's own statistics
    API carries real impressions and clicks -- so a LinkedIn hook A/B pair
    becomes CTR-comparable (``app.services.ab_testing``) the moment both
    arms have this data, with no further wiring needed."""
    tenant, workspace, package, publication = linkedin_package_and_publication
    channel_credentials.create_credential(
        system_db,
        tenant.id,
        workspace.id,
        Channel.LINKEDIN,
        {"access_token": "AQV...token", "organization_urn": "urn:li:organization:12345"},
    )
    system_db.commit()

    stats = {
        "urn:li:share:1": {
            "impressionCount": 500,
            "clickCount": 15,
            "likeCount": 20,
            "commentCount": 3,
            "shareCount": 2,
            "engagement": 0.08,
        }
    }
    written = analytics.pull_metrics_for_workspace(
        system_db, workspace.id, DAY, linkedin_client=linkedin_client(stats)
    )
    assert written == 1

    snapshot = system_db.scalars(
        select(MetricSnapshot).where(MetricSnapshot.publication_id == publication.id)
    ).one()
    assert snapshot.source == "linkedin"
    assert snapshot.impressions == 500
    assert snapshot.clicks == 15
    assert snapshot.metrics == {"likes": 20, "comments": 3, "shares": 2, "engagement": 0.08}



