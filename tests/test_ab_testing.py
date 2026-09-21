"""A/B hook result tracking (handoff section 11): comparing paired "a"/"b"
variants by click-through rate, not raw clicks -- and only once both arms
actually have measurable data.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select

from app.db.enums import Channel, PackageStatus, PublicationStatus
from app.db.models import ABTestResult, ContentPackage, MetricSnapshot, Publication, Variant
from app.services import ab_testing
from tests.conftest import requires_db

pytestmark = requires_db


@pytest.fixture
def package(tenant_factory, system_db):
    tenant, workspace = tenant_factory("acme")
    row = ContentPackage(
        tenant_id=tenant.id,
        workspace_id=workspace.id,
        title="راهنمای خرید دریل برقی",
        locale="fa",
        status=PackageStatus.MEASURING,
    )
    system_db.add(row)
    system_db.flush()
    system_db.commit()
    return row


def make_variant(session, package, *, channel=Channel.WORDPRESS, ab_label=None):
    row = Variant(
        tenant_id=package.tenant_id,
        package_id=package.id,
        channel=channel,
        ab_label=ab_label,
        body={"hook": f"قلاب {ab_label}", "body": "متن", "hashtags": []},
        is_selected=True,
    )
    session.add(row)
    session.flush()
    return row


def make_publication(session, package, variant, *, external_url):
    row = Publication(
        tenant_id=package.tenant_id,
        package_id=package.id,
        variant_id=variant.id,
        channel=variant.channel,
        status=PublicationStatus.PUBLISHED,
        scheduled_at=datetime.now(UTC),
        published_at=datetime.now(UTC),
        external_url=external_url,
    )
    session.add(row)
    session.flush()
    return row


def make_snapshot(session, package, publication, *, clicks, impressions, day=date(2026, 1, 15)):
    row = MetricSnapshot(
        tenant_id=package.tenant_id,
        publication_id=publication.id,
        source="search_console",
        captured_for=day,
        impressions=impressions,
        clicks=clicks,
        position=None,
        metrics={},
    )
    session.add(row)
    session.flush()
    return row


def test_no_data_on_either_arm_produces_no_result(system_db, package):
    a = make_variant(system_db, package, ab_label="a")
    make_variant(system_db, package, ab_label="b")
    make_publication(system_db, package, a, external_url="https://acme.example/a")

    results = ab_testing.evaluate_package(system_db, package)
    assert results == []
    assert system_db.scalars(select(ABTestResult)).all() == []


def test_data_on_only_one_arm_is_not_enough(system_db, package):
    a = make_variant(system_db, package, ab_label="a")
    b = make_variant(system_db, package, ab_label="b")
    pub_a = make_publication(system_db, package, a, external_url="https://acme.example/a")
    make_publication(system_db, package, b, external_url="https://acme.example/b")
    make_snapshot(system_db, package, pub_a, clicks=10, impressions=500)

    results = ab_testing.evaluate_package(system_db, package)
    assert results == []


def test_a_channel_with_no_ab_pair_is_skipped(system_db, package):
    make_variant(system_db, package, channel=Channel.TELEGRAM, ab_label=None)

    results = ab_testing.evaluate_package(system_db, package)
    assert results == []


def test_a_clear_winner_is_decided_by_ctr_not_raw_clicks(system_db, package):
    """A gets more raw clicks but a much lower CTR (drowned in impressions);
    B should still win, since CTR -- not volume -- is what a hook changes."""
    a = make_variant(system_db, package, ab_label="a")
    b = make_variant(system_db, package, ab_label="b")
    pub_a = make_publication(system_db, package, a, external_url="https://acme.example/a")
    pub_b = make_publication(system_db, package, b, external_url="https://acme.example/b")
    make_snapshot(system_db, package, pub_a, clicks=10, impressions=1000)  # 1% CTR
    make_snapshot(system_db, package, pub_b, clicks=5, impressions=100)  # 5% CTR

    results = ab_testing.evaluate_package(system_db, package)
    assert len(results) == 1
    result = results[0]
    assert result.channel == Channel.WORDPRESS
    assert result.winner_variant_id == b.id
    assert result.a_clicks == 10 and result.a_impressions == 1000
    assert result.b_clicks == 5 and result.b_impressions == 100


def test_a_tie_records_no_winner(system_db, package):
    a = make_variant(system_db, package, ab_label="a")
    b = make_variant(system_db, package, ab_label="b")
    pub_a = make_publication(system_db, package, a, external_url="https://acme.example/a")
    pub_b = make_publication(system_db, package, b, external_url="https://acme.example/b")
    make_snapshot(system_db, package, pub_a, clicks=10, impressions=200)
    make_snapshot(system_db, package, pub_b, clicks=10, impressions=200)

    results = ab_testing.evaluate_package(system_db, package)
    assert len(results) == 1
    assert results[0].winner_variant_id is None


def test_re_evaluating_upserts_rather_than_duplicates(system_db, package):
    a = make_variant(system_db, package, ab_label="a")
    b = make_variant(system_db, package, ab_label="b")
    pub_a = make_publication(system_db, package, a, external_url="https://acme.example/a")
    pub_b = make_publication(system_db, package, b, external_url="https://acme.example/b")
    make_snapshot(system_db, package, pub_a, clicks=1, impressions=100, day=date(2026, 1, 15))
    make_snapshot(system_db, package, pub_b, clicks=1, impressions=100, day=date(2026, 1, 15))

    ab_testing.evaluate_package(system_db, package)
    assert system_db.scalars(select(ABTestResult)).all()[0].winner_variant_id is None

    # A second day's data tips the balance -- the same row is updated, not
    # a second one added.
    make_snapshot(system_db, package, pub_a, clicks=50, impressions=100, day=date(2026, 1, 16))
    ab_testing.evaluate_package(system_db, package)

    rows = system_db.scalars(select(ABTestResult)).all()
    assert len(rows) == 1
    assert rows[0].winner_variant_id == a.id
    assert rows[0].a_clicks == 51 and rows[0].a_impressions == 200


def test_multiple_channels_each_get_their_own_result(system_db, package):
    wp_a = make_variant(system_db, package, channel=Channel.WORDPRESS, ab_label="a")
    wp_b = make_variant(system_db, package, channel=Channel.WORDPRESS, ab_label="b")
    tg_a = make_variant(system_db, package, channel=Channel.TELEGRAM, ab_label="a")
    tg_b = make_variant(system_db, package, channel=Channel.TELEGRAM, ab_label="b")
    for variant, url in (
        (wp_a, "https://acme.example/a"),
        (wp_b, "https://acme.example/b"),
        (tg_a, "https://acme.example/tg-a"),
        (tg_b, "https://acme.example/tg-b"),
    ):
        pub = make_publication(system_db, package, variant, external_url=url)
        make_snapshot(system_db, package, pub, clicks=1, impressions=100)

    results = ab_testing.evaluate_package(system_db, package)
    channels = {result.channel for result in results}
    assert channels == {Channel.WORDPRESS, Channel.TELEGRAM}
