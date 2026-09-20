"""The scheduling policy from handoff section 6.

``select_batch`` is a pure function, so the interesting behaviour — window
switching, batching by model, fair rotation between tenants, night-only work —
is tested without a database or a GPU.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from app.db.enums import GpuJobKind, GpuWindow
from app.worker.gpu_scheduler import (
    Batch,
    SchedulableJob,
    SchedulerConfig,
    in_night_window,
    select_batch,
)

NOON = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
CONFIG = SchedulerConfig(
    max_batch_size=4,
    max_batch_seconds=600,
    window_starvation_seconds=1800,
    night_start_hour=1,
    night_end_hour=7,
)

ALPHA = uuid.UUID("11111111-1111-1111-1111-111111111111")
BETA = uuid.UUID("22222222-2222-2222-2222-222222222222")


def job(
    kind: GpuJobKind,
    tenant=ALPHA,
    *,
    age_seconds: float = 0,
    priority: int = 100,
    seconds: float = 10.0,
) -> SchedulableJob:
    return SchedulableJob(
        id=uuid.uuid4(),
        tenant_id=tenant,
        kind=kind,
        window=kind.window,
        priority=priority,
        nightly_only=kind.nightly_only,
        estimated_seconds=seconds,
        created_at=NOON - timedelta(seconds=age_seconds),
    )


def test_empty_queue_yields_nothing() -> None:
    assert select_batch([], now=NOON, config=CONFIG) is None


def test_batches_only_jobs_that_share_a_model() -> None:
    """One model load per batch is the whole point of batching."""
    jobs = [
        job(GpuJobKind.IMAGE_FLUX, age_seconds=30),
        job(GpuJobKind.IMAGE_FLUX, age_seconds=20),
        job(GpuJobKind.TTS, age_seconds=25),
    ]
    batch = select_batch(jobs, now=NOON, config=CONFIG)
    assert batch is not None
    assert batch.kind is GpuJobKind.IMAGE_FLUX
    assert len(batch) == 2


def test_stays_in_the_current_window_while_it_has_work() -> None:
    """Switching costs a model reload, so it is not done for free."""
    jobs = [
        job(GpuJobKind.LLM_TEXT, age_seconds=10),
        job(GpuJobKind.IMAGE_FLUX, age_seconds=60),
    ]
    batch = select_batch(
        jobs,
        now=NOON,
        current_window=GpuWindow.TEXT,
        current_kind=GpuJobKind.LLM_TEXT,
        config=CONFIG,
    )
    assert batch is not None
    assert batch.window is GpuWindow.TEXT
    assert batch.switch_required is False


def test_switches_when_the_other_window_is_starving() -> None:
    jobs = [
        job(GpuJobKind.LLM_TEXT, age_seconds=10),
        job(GpuJobKind.IMAGE_FLUX, age_seconds=3600),
    ]
    batch = select_batch(
        jobs,
        now=NOON,
        current_window=GpuWindow.TEXT,
        current_kind=GpuJobKind.LLM_TEXT,
        config=CONFIG,
    )
    assert batch is not None
    assert batch.window is GpuWindow.MEDIA
    assert batch.switch_required is True
    assert "starving" in batch.reason


def test_switches_when_the_current_window_is_empty() -> None:
    jobs = [job(GpuJobKind.IMAGE_FLUX, age_seconds=5)]
    batch = select_batch(
        jobs,
        now=NOON,
        current_window=GpuWindow.TEXT,
        current_kind=GpuJobKind.LLM_TEXT,
        config=CONFIG,
    )
    assert batch is not None
    assert batch.window is GpuWindow.MEDIA
    assert batch.switch_required is True


def test_changing_model_inside_one_window_still_counts_as_a_switch() -> None:
    jobs = [job(GpuJobKind.TTS, age_seconds=5)]
    batch = select_batch(
        jobs,
        now=NOON,
        current_window=GpuWindow.MEDIA,
        current_kind=GpuJobKind.IMAGE_FLUX,
        config=CONFIG,
    )
    assert batch is not None
    assert batch.window is GpuWindow.MEDIA
    assert batch.switch_required is True


def test_nightly_jobs_wait_for_the_night_window() -> None:
    jobs = [job(GpuJobKind.VIDEO_WAN, age_seconds=7200, seconds=780)]
    assert select_batch(jobs, now=NOON, config=CONFIG) is None

    at_night = NOON.replace(hour=3)
    batch = select_batch(jobs, now=at_night, config=CONFIG)
    assert batch is not None
    assert batch.kind is GpuJobKind.VIDEO_WAN


def test_night_window_handles_a_range_that_wraps_midnight() -> None:
    wrapping = SchedulerConfig(night_start_hour=22, night_end_hour=6)
    assert in_night_window(NOON.replace(hour=23), wrapping)
    assert in_night_window(NOON.replace(hour=2), wrapping)
    assert not in_night_window(NOON.replace(hour=12), wrapping)


def test_the_tenant_furthest_behind_its_share_goes_first() -> None:
    """Weighted deficit, not arrival order, decides whose work runs."""
    jobs = [
        job(GpuJobKind.IMAGE_FLUX, ALPHA, age_seconds=100),
        job(GpuJobKind.IMAGE_FLUX, BETA, age_seconds=10),
    ]
    batch = select_batch(
        jobs,
        now=NOON,
        tenant_weights={ALPHA: 1, BETA: 1},
        # Alpha has already had the card all morning.
        consumed_today={ALPHA: 5000.0, BETA: 0.0},
        config=CONFIG,
    )
    assert batch is not None
    lead = next(j for j in jobs if j.id == batch.job_ids[0])
    assert lead.tenant_id == BETA


def test_a_heavier_plan_earns_a_larger_share() -> None:
    jobs = [
        job(GpuJobKind.IMAGE_FLUX, ALPHA, age_seconds=50),
        job(GpuJobKind.IMAGE_FLUX, BETA, age_seconds=50),
    ]
    batch = select_batch(
        jobs,
        now=NOON,
        tenant_weights={ALPHA: 1, BETA: 8},
        consumed_today={ALPHA: 100.0, BETA: 200.0},
        config=CONFIG,
    )
    assert batch is not None
    lead = next(j for j in jobs if j.id == batch.job_ids[0])
    # Beta used twice as much but is entitled to eight times the share.
    assert lead.tenant_id == BETA


def test_one_tenant_cannot_fill_the_whole_batch() -> None:
    """Round-robin inside a batch keeps a bulk queue from crowding others out."""
    jobs = [job(GpuJobKind.IMAGE_FLUX, ALPHA, age_seconds=100 - i) for i in range(10)]
    jobs.append(job(GpuJobKind.IMAGE_FLUX, BETA, age_seconds=1))

    batch = select_batch(
        jobs,
        now=NOON,
        tenant_weights={ALPHA: 1, BETA: 1},
        consumed_today={},
        config=CONFIG,
    )
    assert batch is not None
    by_id = {j.id: j for j in jobs}
    tenants = [by_id[jid].tenant_id for jid in batch.job_ids]
    assert BETA in tenants


def test_batch_respects_the_time_cap_but_never_drops_the_lead() -> None:
    """A single job longer than the cap must still be runnable."""
    long_job = job(GpuJobKind.VIDEO_WAN, age_seconds=10, seconds=900)
    batch = select_batch(
        [long_job, job(GpuJobKind.VIDEO_WAN, age_seconds=5, seconds=900)],
        now=NOON.replace(hour=3),
        config=CONFIG,
    )
    assert batch is not None
    assert len(batch) == 1
    assert batch.estimated_seconds == 900


def test_priority_orders_jobs_within_one_tenant() -> None:
    urgent = job(GpuJobKind.IMAGE_FLUX, age_seconds=5, priority=1)
    normal = job(GpuJobKind.IMAGE_FLUX, age_seconds=500, priority=100)
    batch = select_batch([normal, urgent], now=NOON, config=CONFIG)
    assert batch is not None
    assert batch.job_ids[0] == urgent.id


def test_batch_is_capped_by_size() -> None:
    jobs = [job(GpuJobKind.IMAGE_FLUX, age_seconds=i, seconds=1.0) for i in range(20)]
    batch = select_batch(jobs, now=NOON, config=CONFIG)
    assert batch is not None
    assert len(batch) == CONFIG.max_batch_size


def test_batch_ids_are_unique() -> None:
    jobs = [job(GpuJobKind.IMAGE_FLUX, age_seconds=5)]
    first = select_batch(jobs, now=NOON, config=CONFIG)
    second = select_batch(jobs, now=NOON, config=CONFIG)
    assert isinstance(first, Batch) and isinstance(second, Batch)
    assert first.batch_id != second.batch_id
