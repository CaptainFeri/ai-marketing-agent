"""GPU capacity accounting — handoff section 7."""

from __future__ import annotations

from datetime import date

import pytest

from app.core.errors import QuotaExceededError
from app.db.enums import GpuJobKind
from app.services import quota
from tests.conftest import requires_db

pytestmark = requires_db

DAY = date(2026, 9, 20)


def test_capacity_is_split_by_plan_weight(tenant_factory, system_db) -> None:
    small, _ = tenant_factory("small", weight=1)
    large, _ = tenant_factory("large", weight=3)

    quota.allocate_day(system_db, DAY, capacity_seconds=8000)
    system_db.commit()

    small_status = quota.quota_status(system_db, small.id, DAY)
    large_status = quota.quota_status(system_db, large.id, DAY)

    assert small_status.allocated_seconds == pytest.approx(2000)
    assert large_status.allocated_seconds == pytest.approx(6000)
    assert small_status.capacity_seconds == 8000


def test_inactive_tenants_do_not_take_a_share(tenant_factory, system_db) -> None:
    active, _ = tenant_factory("active", weight=1)
    dormant, _ = tenant_factory("dormant", weight=1)
    dormant.is_active = False
    system_db.flush()

    quota.allocate_day(system_db, DAY, capacity_seconds=1000)
    system_db.commit()

    assert quota.quota_status(system_db, active.id, DAY).allocated_seconds == pytest.approx(1000)


def test_reserving_reduces_what_remains(tenant_factory, system_db) -> None:
    tenant, _ = tenant_factory("acme", weight=1)
    quota.allocate_day(system_db, DAY, capacity_seconds=1000)

    quota.reserve(system_db, tenant.id, 400, DAY)
    status = quota.quota_status(system_db, tenant.id, DAY)

    assert status.reserved_seconds == pytest.approx(400)
    assert status.remaining_seconds == pytest.approx(600)


def test_reserving_past_the_share_is_refused(tenant_factory, system_db) -> None:
    """Over-quota work is deferred, not failed — the caller catches this."""
    tenant, _ = tenant_factory("acme", weight=1)
    quota.allocate_day(system_db, DAY, capacity_seconds=1000)
    quota.reserve(system_db, tenant.id, 900, DAY)

    with pytest.raises(QuotaExceededError) as excinfo:
        quota.reserve(system_db, tenant.id, 200, DAY)
    assert excinfo.value.details["remaining_seconds"] == pytest.approx(100)


def test_consuming_settles_the_reservation(tenant_factory, system_db) -> None:
    tenant, _ = tenant_factory("acme", weight=1)
    quota.allocate_day(system_db, DAY, capacity_seconds=1000)
    quota.reserve(system_db, tenant.id, 100, DAY)

    # The job took longer than estimated; the GPU really was busy that long.
    quota.consume(system_db, tenant.id, reserved_seconds=100, actual_seconds=130, day=DAY)
    status = quota.quota_status(system_db, tenant.id, DAY)

    assert status.reserved_seconds == pytest.approx(0)
    assert status.consumed_seconds == pytest.approx(130)
    assert status.remaining_seconds == pytest.approx(870)


def test_releasing_a_cancelled_job_gives_the_time_back(tenant_factory, system_db) -> None:
    tenant, _ = tenant_factory("acme", weight=1)
    quota.allocate_day(system_db, DAY, capacity_seconds=1000)
    quota.reserve(system_db, tenant.id, 250, DAY)

    quota.release(system_db, tenant.id, 250, DAY)
    assert quota.quota_status(system_db, tenant.id, DAY).remaining_seconds == pytest.approx(1000)


def test_reallocating_preserves_consumption(tenant_factory, system_db) -> None:
    """A mid-day plan change must not erase what the tenant already used."""
    tenant, _ = tenant_factory("acme", weight=1)
    quota.allocate_day(system_db, DAY, capacity_seconds=1000)
    quota.consume(system_db, tenant.id, 0, 300, DAY)

    tenant.quota_weight = 4
    system_db.flush()
    quota.allocate_day(system_db, DAY, capacity_seconds=1000)

    status = quota.quota_status(system_db, tenant.id, DAY)
    assert status.consumed_seconds == pytest.approx(300)
    assert status.allocated_seconds == pytest.approx(1000)
    assert status.weight == 4


def test_estimates_start_from_the_documented_defaults(system_db) -> None:
    assert quota.estimate_seconds(system_db, GpuJobKind.IMAGE_FLUX) == pytest.approx(
        quota.DEFAULT_ESTIMATES[GpuJobKind.IMAGE_FLUX]
    )


def test_measurements_move_the_estimate_towards_reality(system_db) -> None:
    """Section 7, step 2: the cost of a job kind is learned, not guessed."""
    default = quota.DEFAULT_ESTIMATES[GpuJobKind.IMAGE_FLUX]
    for _ in range(40):
        quota.record_actual(system_db, GpuJobKind.IMAGE_FLUX, 30.0)
    system_db.flush()

    learned = quota.estimate_seconds(system_db, GpuJobKind.IMAGE_FLUX)
    assert learned > default
    assert learned == pytest.approx(30.0, abs=0.5)


def test_estimates_are_kept_per_locale(system_db) -> None:
    """Persian articles take longer than English ones, so they are tracked apart."""
    for _ in range(40):
        quota.record_actual(system_db, GpuJobKind.LLM_TEXT, 120.0, locale="fa")
        quota.record_actual(system_db, GpuJobKind.LLM_TEXT, 60.0, locale="en")
    system_db.flush()

    assert quota.estimate_seconds(system_db, GpuJobKind.LLM_TEXT, "fa") == pytest.approx(
        120.0, abs=1.0
    )
    assert quota.estimate_seconds(system_db, GpuJobKind.LLM_TEXT, "en") == pytest.approx(
        60.0, abs=1.0
    )


def test_zero_and_negative_measurements_are_ignored(system_db) -> None:
    quota.record_actual(system_db, GpuJobKind.TTS, 0.0)
    quota.record_actual(system_db, GpuJobKind.TTS, -5.0)
    system_db.flush()
    assert quota.estimate_seconds(system_db, GpuJobKind.TTS) == pytest.approx(
        quota.DEFAULT_ESTIMATES[GpuJobKind.TTS]
    )


def test_panel_shows_how_many_packages_today_still_buys(tenant_factory, system_db) -> None:
    """Section 7, step 4 — the number the customer actually sees."""
    tenant, _ = tenant_factory("acme", weight=1)
    quota.allocate_day(system_db, DAY, capacity_seconds=20 * 3600)

    status = quota.quota_status(system_db, tenant.id, DAY)
    by_label = {a.label: a for a in status.affordances}

    assert set(by_label) == set(quota.PACKAGE_RECIPES)
    # Text-only is the cheapest shape, face video the most expensive.
    assert by_label["text_only"].affordable_today > by_label["video_face"].affordable_today
    assert by_label["video_face"].affordable_today > by_label["video_ai_clip"].affordable_today


def test_affordances_fall_to_zero_when_the_day_is_spent(tenant_factory, system_db) -> None:
    tenant, _ = tenant_factory("acme", weight=1)
    quota.allocate_day(system_db, DAY, capacity_seconds=1000)
    quota.consume(system_db, tenant.id, 0, 1000, DAY)

    status = quota.quota_status(system_db, tenant.id, DAY)
    assert status.remaining_seconds == 0
    assert all(a.affordable_today == 0 for a in status.affordances)
