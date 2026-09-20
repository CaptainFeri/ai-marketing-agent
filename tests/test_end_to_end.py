"""The queue, the scheduler and the accounting, driven end to end.

Uses :class:`~app.worker.gpu_runtime.SimulatedGpuRuntime` in place of the real
models — phase 0 produces those.  Everything else in the loop is the code that
will run in production: real queue rows, real leases, real quota bookkeeping.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.db.enums import GpuJobKind, GpuJobStatus, GpuWindow
from app.db.models import ContentPackage, GpuJob
from app.services import quota
from app.worker import dispatcher
from app.worker.gpu_runtime import SimulatedGpuRuntime
from app.worker.gpu_scheduler import SchedulerConfig
from tests.conftest import requires_db

pytestmark = requires_db

# Night hours, so heavy video jobs are eligible too.
AT_NIGHT = datetime.now(UTC).replace(hour=3, minute=0, second=0, microsecond=0)


def drain(session, runtime, *, max_batches: int = 50, now=None) -> list[str]:
    """Run batches until the queue is empty, returning the kinds in order."""
    kinds: list[str] = []
    config = SchedulerConfig(max_batch_size=4, max_batch_seconds=3000)
    for _ in range(max_batches):
        batch = dispatcher.claim_next_batch(session, "test-worker", now=now, config=config)
        if batch is None:
            return kinds
        kinds.append(batch.kind.value)
        runtime.ensure_window(batch.window, batch.kind)
        for job_id in batch.job_ids:
            dispatcher.start_job(session, job_id)
            result = runtime.run(batch.kind, {})
            dispatcher.complete_job(session, job_id, gpu_seconds=result.gpu_seconds)
        session.flush()
    raise AssertionError("queue did not drain")


@pytest.fixture
def runtime() -> SimulatedGpuRuntime:
    return SimulatedGpuRuntime(speedup=1_000_000, seed=7)


def test_a_full_package_worth_of_jobs_drains(tenant_factory, system_db, runtime) -> None:
    tenant, workspace = tenant_factory("acme")
    quota.allocate_day(system_db, AT_NIGHT.date(), capacity_seconds=20 * 3600)
    package = ContentPackage(
        tenant_id=tenant.id, workspace_id=workspace.id, title="مقاله", locale="fa"
    )
    system_db.add(package)
    system_db.flush()

    # What a `voice` package needs: seven agent turns, four images, speech.
    for _ in range(7):
        dispatcher.enqueue_job(
            system_db, tenant_id=tenant.id, kind=GpuJobKind.LLM_TEXT, package_id=package.id
        )
    for _ in range(4):
        dispatcher.enqueue_job(
            system_db, tenant_id=tenant.id, kind=GpuJobKind.IMAGE_FLUX, package_id=package.id
        )
    dispatcher.enqueue_job(
        system_db, tenant_id=tenant.id, kind=GpuJobKind.TTS, package_id=package.id
    )
    dispatcher.enqueue_job(
        system_db,
        tenant_id=tenant.id,
        kind=GpuJobKind.TRANSCRIBE_WHISPER,
        package_id=package.id,
    )
    system_db.flush()

    kinds = drain(system_db, runtime, now=AT_NIGHT)

    remaining = system_db.query(GpuJob).filter(GpuJob.status != GpuJobStatus.SUCCEEDED).count()
    assert remaining == 0
    assert kinds  # at least one batch ran

    system_db.refresh(package)
    assert package.gpu_seconds > 0

    status = quota.quota_status(system_db, tenant.id, AT_NIGHT.date())
    assert status.consumed_seconds == pytest.approx(package.gpu_seconds, rel=1e-6)
    # Every reservation was settled, none left hanging.
    assert status.reserved_seconds == pytest.approx(0, abs=1e-6)


def test_batching_keeps_the_number_of_model_loads_down(tenant_factory, system_db, runtime) -> None:
    """Twelve images should not mean twelve FLUX loads (handoff section 6)."""
    tenant, _ = tenant_factory("acme")
    quota.allocate_day(system_db, AT_NIGHT.date(), capacity_seconds=20 * 3600)
    for _ in range(12):
        dispatcher.enqueue_job(system_db, tenant_id=tenant.id, kind=GpuJobKind.IMAGE_FLUX)
    system_db.flush()

    kinds = drain(system_db, runtime, now=AT_NIGHT)
    # Batch size 4, so three batches — and the runtime only reloads on change.
    assert kinds == ["image_flux"] * 3

    state = dispatcher._window_state(system_db)
    assert state.current_kind is GpuJobKind.IMAGE_FLUX
    assert state.switch_count_today == 1


def test_measured_times_replace_the_seed_estimates(tenant_factory, system_db, runtime) -> None:
    """Section 7, step 2 in practice: estimates converge on observed cost."""
    tenant, _ = tenant_factory("acme")
    quota.allocate_day(system_db, AT_NIGHT.date(), capacity_seconds=20 * 3600)
    for _ in range(20):
        dispatcher.enqueue_job(system_db, tenant_id=tenant.id, kind=GpuJobKind.IMAGE_FLUX)
    system_db.flush()

    drain(system_db, runtime, now=AT_NIGHT)

    estimate = system_db.query(GpuJob).first()
    assert estimate is not None
    learned = quota.estimate_seconds(system_db, GpuJobKind.IMAGE_FLUX)
    # The simulator jitters ±25% around the seed, so the learned value should
    # land near it without being identical to it.
    seed = quota.DEFAULT_ESTIMATES[GpuJobKind.IMAGE_FLUX]
    assert learned == pytest.approx(seed, rel=0.3)
    assert learned != seed


def test_nightly_video_waits_for_the_night(tenant_factory, system_db, runtime) -> None:
    tenant, _ = tenant_factory("acme")
    quota.allocate_day(system_db, datetime.now(UTC).date(), capacity_seconds=20 * 3600)
    dispatcher.enqueue_job(system_db, tenant_id=tenant.id, kind=GpuJobKind.VIDEO_WAN)
    dispatcher.enqueue_job(system_db, tenant_id=tenant.id, kind=GpuJobKind.LLM_TEXT)
    system_db.flush()

    at_noon = datetime.now(UTC).replace(hour=12)
    kinds = drain(system_db, runtime, now=at_noon)
    assert kinds == ["llm_text"]

    still_pending = system_db.query(GpuJob).filter(GpuJob.status == GpuJobStatus.PENDING).one()
    assert still_pending.kind is GpuJobKind.VIDEO_WAN

    quota.allocate_day(system_db, AT_NIGHT.date(), capacity_seconds=20 * 3600)
    kinds = drain(system_db, runtime, now=AT_NIGHT)
    assert kinds == ["video_wan"]


def test_two_tenants_share_the_card(tenant_factory, system_db, runtime) -> None:
    """Neither customer is starved when both queue a lot of work."""
    heavy, _ = tenant_factory("heavy", weight=1)
    light, _ = tenant_factory("light", weight=1)
    quota.allocate_day(system_db, AT_NIGHT.date(), capacity_seconds=20 * 3600)

    for _ in range(20):
        dispatcher.enqueue_job(system_db, tenant_id=heavy.id, kind=GpuJobKind.IMAGE_FLUX)
    for _ in range(20):
        dispatcher.enqueue_job(system_db, tenant_id=light.id, kind=GpuJobKind.IMAGE_FLUX)
    system_db.flush()

    drain(system_db, runtime, now=AT_NIGHT)

    heavy_used = quota.quota_status(system_db, heavy.id, AT_NIGHT.date()).consumed_seconds
    light_used = quota.quota_status(system_db, light.id, AT_NIGHT.date()).consumed_seconds
    assert heavy_used > 0 and light_used > 0
    # Equal weights, equal work: the split should be close to even.
    assert heavy_used == pytest.approx(light_used, rel=0.35)


def test_the_window_state_survives_a_scheduler_restart(tenant_factory, system_db, runtime) -> None:
    """A restarted scheduler must not assume it has a cold GPU."""
    tenant, _ = tenant_factory("acme")
    quota.allocate_day(system_db, AT_NIGHT.date(), capacity_seconds=20 * 3600)
    dispatcher.enqueue_job(system_db, tenant_id=tenant.id, kind=GpuJobKind.LLM_TEXT)
    system_db.flush()

    dispatcher.claim_next_batch(system_db, "worker-1", now=AT_NIGHT)
    system_db.commit()

    state = dispatcher._window_state(system_db)
    assert state.current_window is GpuWindow.TEXT
    switches_before = state.switch_count_today

    # A second scheduler process reads the same row rather than starting cold.
    dispatcher.enqueue_job(system_db, tenant_id=tenant.id, kind=GpuJobKind.LLM_TEXT)
    system_db.flush()
    batch = dispatcher.claim_next_batch(system_db, "worker-2", now=AT_NIGHT)
    assert batch is not None
    assert batch.switch_required is False
    assert dispatcher._window_state(system_db).switch_count_today == switches_before
