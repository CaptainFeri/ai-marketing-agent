"""Suggested publish time (handoff section 11: "پیشنهاد زمان انتشار (اول
قاعده‌محور، بعد از روی داده تعامل)"): rule-of-thumb by default, switching to
the workspace's own historical engagement once there is enough of it, and
always avoiding a known holiday or a spacing-rule conflict.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest

from app.db.enums import Channel, PackageStatus, PublicationStatus
from app.db.models import ContentPackage, MetricSnapshot, Publication, Variant
from app.services import publish_time
from app.services.holidays import HOLIDAYS
from tests.conftest import requires_db

pytestmark = requires_db


@pytest.fixture
def package(tenant_factory, system_db):
    tenant, workspace = tenant_factory("acme")
    workspace.timezone = "UTC"
    row = ContentPackage(
        tenant_id=tenant.id, workspace_id=workspace.id, title="x", locale="fa",
        status=PackageStatus.SCHEDULED,
    )
    system_db.add(row)
    system_db.flush()
    system_db.commit()
    return workspace, row


def make_publication(session, package, *, when, channel=Channel.WORDPRESS):
    variant = Variant(
        tenant_id=package.tenant_id,
        package_id=package.id,
        channel=channel,
        body={"hook": "قلاب", "body": "متن", "hashtags": []},
        is_selected=True,
    )
    session.add(variant)
    session.flush()
    publication = Publication(
        tenant_id=package.tenant_id,
        package_id=package.id,
        variant_id=variant.id,
        channel=channel,
        status=PublicationStatus.PUBLISHED,
        scheduled_at=when,
    )
    session.add(publication)
    session.flush()
    return publication


def make_snapshot(session, package, publication, *, clicks, impressions):
    row = MetricSnapshot(
        tenant_id=package.tenant_id,
        publication_id=publication.id,
        source="search_console",
        captured_for=date(2026, 1, 15),
        impressions=impressions,
        clicks=clicks,
        position=None,
        metrics={},
    )
    session.add(row)
    session.flush()
    return row


def test_no_history_uses_the_rule_of_thumb(system_db, package) -> None:
    workspace, _pkg = package
    after = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
    suggestion = publish_time.suggest_publish_time(
        system_db, workspace, Channel.WORDPRESS, after=after
    )
    assert suggestion.basis == "rule_of_thumb"
    assert suggestion.hour_of_day == 9
    assert suggestion.sample_size == 0
    assert suggestion.scheduled_at.hour == 9


def test_the_suggestion_is_the_next_occurrence_strictly_after_the_given_time(
    system_db, package
) -> None:
    workspace, _pkg = package
    # Already past today's 09:00 -- must roll to tomorrow.
    after = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
    suggestion = publish_time.suggest_publish_time(
        system_db, workspace, Channel.WORDPRESS, after=after
    )
    assert suggestion.scheduled_at.date() == date(2026, 6, 2)

    # Still before today's 09:00 -- stays on the same day.
    after = datetime(2026, 6, 1, 8, 0, tzinfo=UTC)
    suggestion = publish_time.suggest_publish_time(
        system_db, workspace, Channel.WORDPRESS, after=after
    )
    assert suggestion.scheduled_at.date() == date(2026, 6, 1)


def test_enough_history_switches_to_the_best_performing_hour(system_db, package) -> None:
    workspace, pkg = package
    base = datetime(2026, 1, 10, tzinfo=UTC)
    # Hour 9: three posts, low CTR.
    for day in range(3):
        pub = make_publication(system_db, pkg, when=base + timedelta(days=day, hours=9))
        make_snapshot(system_db, pkg, pub, clicks=1, impressions=100)
    # Hour 14: three posts, high CTR -- should win.
    for day in range(3):
        pub = make_publication(system_db, pkg, when=base + timedelta(days=day, hours=14))
        make_snapshot(system_db, pkg, pub, clicks=20, impressions=100)

    suggestion = publish_time.suggest_publish_time(
        system_db, workspace, Channel.WORDPRESS, after=datetime(2026, 6, 1, tzinfo=UTC)
    )
    assert suggestion.basis == "historical_engagement"
    assert suggestion.hour_of_day == 14
    assert suggestion.sample_size == 3


def test_an_hour_with_too_few_samples_is_not_trusted(system_db, package) -> None:
    workspace, pkg = package
    base = datetime(2026, 1, 10, tzinfo=UTC)
    # Only two data points anywhere -- not enough to trust over the rule
    # of thumb, however high the CTR.
    for day in range(2):
        pub = make_publication(system_db, pkg, when=base + timedelta(days=day, hours=14))
        make_snapshot(system_db, pkg, pub, clicks=50, impressions=100)

    suggestion = publish_time.suggest_publish_time(
        system_db, workspace, Channel.WORDPRESS, after=datetime(2026, 6, 1, tzinfo=UTC)
    )
    assert suggestion.basis == "rule_of_thumb"
    assert suggestion.hour_of_day == 9


def test_a_channel_with_no_ctr_data_falls_back_to_summed_metrics(system_db, package) -> None:
    """Instagram never carries clicks/impressions (docs/social-insights.md)
    -- the engagement score must still work from whatever numeric metrics
    it does report."""
    workspace, pkg = package
    base = datetime(2026, 1, 10, tzinfo=UTC)
    for day in range(3):
        pub = make_publication(
            system_db, pkg, when=base + timedelta(days=day, hours=18), channel=Channel.INSTAGRAM
        )
        row = MetricSnapshot(
            tenant_id=pkg.tenant_id,
            publication_id=pub.id,
            source="instagram",
            captured_for=date(2026, 1, 15),
            impressions=None,
            clicks=None,
            position=None,
            metrics={"reach": 300, "likes": 20},
        )
        system_db.add(row)
    system_db.flush()

    suggestion = publish_time.suggest_publish_time(
        system_db, workspace, Channel.INSTAGRAM, after=datetime(2026, 6, 1, tzinfo=UTC)
    )
    assert suggestion.basis == "historical_engagement"
    assert suggestion.hour_of_day == 18


def test_a_holiday_is_skipped(system_db, package) -> None:
    workspace, _pkg = package
    nowruz = next(h for h in HOLIDAYS if h.name_en == "Nowruz")
    after = datetime.combine(nowruz.date, time(0, 0), tzinfo=UTC)

    suggestion = publish_time.suggest_publish_time(
        system_db, workspace, Channel.WORDPRESS, after=after
    )
    assert suggestion.scheduled_at.date() not in {h.date for h in HOLIDAYS}
    assert suggestion.scheduled_at.date() >= nowruz.date


def test_a_spacing_conflict_is_skipped(system_db, package) -> None:
    workspace, pkg = package
    after = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
    naive_candidate = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
    make_publication(system_db, pkg, when=naive_candidate)

    suggestion = publish_time.suggest_publish_time(
        system_db, workspace, Channel.WORDPRESS, after=after
    )
    assert suggestion.scheduled_at != naive_candidate
    assert suggestion.scheduled_at.date() > naive_candidate.date()


def test_the_spacing_rule_is_scoped_to_the_requested_channel(system_db, package) -> None:
    workspace, pkg = package
    after = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
    naive_candidate = datetime(2026, 6, 1, 19, 0, tzinfo=UTC)
    # A WordPress post at Telegram's rule-of-thumb hour must not block a
    # Telegram suggestion -- the spacing rule is per channel.
    make_publication(system_db, pkg, when=naive_candidate, channel=Channel.WORDPRESS)

    suggestion = publish_time.suggest_publish_time(
        system_db, workspace, Channel.TELEGRAM, after=after
    )
    assert suggestion.scheduled_at == naive_candidate
