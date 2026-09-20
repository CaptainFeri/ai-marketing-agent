"""The database side of the GPU scheduler: leases, retries, accounting."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.errors import QuotaExceededError
from app.db.enums import GpuJobKind, GpuJobStatus, GpuWindow
from app.db.models import GpuJob
from app.services import quota
from app.worker import dispatcher
from tests.conftest import requires_db

pytestmark = requires_db


@pytest.fixture
def allocated(tenant_factory, system_db):
    def _make(slug: str, *, weight: int = 1, capacity: float = 20 * 3600):
        tenant, workspace = tenant_factory(slug, weight=weight)
        quota.allocate_day(system_db, datetime.now(UTC).date(), capacity_seconds=capacity)
        system_db.flush()
        return tenant, workspace

    return _make


def test_enqueuing_reserves_the_estimated_cost(allocated, system_db) -> None:
    tenant, workspace = allocated("acme")
    before = quota.quota_status(system_db, tenant.id).remaining_seconds

    job = dispatcher.enqueue_job(
        system_db, tenant_id=tenant.id, kind=GpuJobKind.IMAGE_FLUX, workspace_id=workspace.id
    )

    after = quota.quota_status(system_db, tenant.id).remaining_seconds
    assert job.status is GpuJobStatus.PENDING
    assert job.window is GpuWindow.MEDIA
    assert before - after == pytest.approx(job.estimated_seconds)


def test_video_jobs_are_marked_nightly(allocated, system_db) -> None:
    tenant, _ = allocated("acme")
    job = dispatcher.enqueue_job(system_db, tenant_id=tenant.id, kind=GpuJobKind.VIDEO_WAN)
    assert job.nightly_only is True

    text_job = dispatcher.enqueue_job(system_db, tenant_id=tenant.id, kind=GpuJobKind.LLM_TEXT)
    assert text_job.nightly_only is False
    assert text_job.window is GpuWindow.TEXT


def test_enqueuing_past_the_daily_share_is_refused(allocated, system_db) -> None:
    tenant, _ = allocated("acme", capacity=20.0)
    with pytest.raises(QuotaExceededError):
        dispatcher.enqueue_job(system_db, tenant_id=tenant.id, kind=GpuJobKind.VIDEO_WAN)


def test_claiming_leases_the_batch(allocated, system_db) -> None:
    tenant, _ = allocated("acme")
    for _ in range(3):
        dispatcher.enqueue_job(system_db, tenant_id=tenant.id, kind=GpuJobKind.IMAGE_FLUX)
    system_db.flush()

    batch = dispatcher.claim_next_batch(system_db, "worker-1")
    assert batch is not None
    assert batch.kind is GpuJobKind.IMAGE_FLUX

    leased = [system_db.get(GpuJob, jid) for jid in batch.job_ids]
    assert all(job.status is GpuJobStatus.LEASED for job in leased)
    assert all(job.lease_owner == "worker-1" for job in leased)
    assert {job.batch_id for job in leased} == {batch.batch_id}


def test_a_second_claim_does_not_re_lease_the_same_jobs(allocated, system_db) -> None:
    tenant, _ = allocated("acme")
    dispatcher.enqueue_job(system_db, tenant_id=tenant.id, kind=GpuJobKind.IMAGE_FLUX)
    system_db.flush()

    first = dispatcher.claim_next_batch(system_db, "worker-1")
    second = dispatcher.claim_next_batch(system_db, "worker-2")
    assert first is not None
    assert second is None


def test_claiming_records_the_window_switch(allocated, system_db) -> None:
    tenant, _ = allocated("acme")
    dispatcher.enqueue_job(system_db, tenant_id=tenant.id, kind=GpuJobKind.LLM_TEXT)
    system_db.flush()
    dispatcher.claim_next_batch(system_db, "worker-1")

    state = dispatcher._window_state(system_db)
    assert state.current_window is GpuWindow.TEXT
    assert state.current_kind is GpuJobKind.LLM_TEXT


def test_completing_books_the_real_time(allocated, system_db) -> None:
    tenant, _ = allocated("acme")
    job = dispatcher.enqueue_job(system_db, tenant_id=tenant.id, kind=GpuJobKind.IMAGE_FLUX)
    system_db.flush()
    estimated = job.estimated_seconds

    dispatcher.start_job(system_db, job.id)
    dispatcher.complete_job(system_db, job.id, gpu_seconds=17.5, result={"ok": True})

    status = quota.quota_status(system_db, tenant.id)
    assert status.consumed_seconds == pytest.approx(17.5)
    assert status.reserved_seconds == pytest.approx(0)
    refreshed = system_db.get(GpuJob, job.id)
    assert refreshed.status is GpuJobStatus.SUCCEEDED
    assert refreshed.gpu_seconds == pytest.approx(17.5)
    # And the measurement feeds the estimator for next time.
    assert quota.estimate_seconds(system_db, GpuJobKind.IMAGE_FLUX) != estimated


def test_completing_propagates_onto_the_package(allocated, system_db) -> None:
    from app.db.models import ContentPackage

    tenant, workspace = allocated("acme")
    package = ContentPackage(tenant_id=tenant.id, workspace_id=workspace.id, title="x", locale="fa")
    system_db.add(package)
    system_db.flush()

    for seconds in (10.0, 5.0):
        job = dispatcher.enqueue_job(
            system_db, tenant_id=tenant.id, kind=GpuJobKind.IMAGE_FLUX, package_id=package.id
        )
        system_db.flush()
        dispatcher.complete_job(system_db, job.id, gpu_seconds=seconds)

    system_db.refresh(package)
    assert package.gpu_seconds == pytest.approx(15.0)


def test_a_failure_is_retried_before_it_gives_up(allocated, system_db) -> None:
    tenant, _ = allocated("acme")
    job = dispatcher.enqueue_job(system_db, tenant_id=tenant.id, kind=GpuJobKind.IMAGE_FLUX)
    system_db.flush()

    dispatcher.start_job(system_db, job.id)
    dispatcher.fail_job(system_db, job.id, "CUDA out of memory")
    system_db.flush()

    refreshed = system_db.get(GpuJob, job.id)
    assert refreshed.status is GpuJobStatus.PENDING
    assert refreshed.available_at is not None
    # The reservation is kept: the work still has to happen.
    assert quota.quota_status(system_db, tenant.id).reserved_seconds > 0


def test_a_final_failure_releases_the_reservation(allocated, system_db) -> None:
    tenant, _ = allocated("acme")
    job = dispatcher.enqueue_job(system_db, tenant_id=tenant.id, kind=GpuJobKind.IMAGE_FLUX)
    system_db.flush()

    for _ in range(job.max_attempts):
        dispatcher.start_job(system_db, job.id)
        dispatcher.fail_job(system_db, job.id, "boom")
    system_db.flush()

    refreshed = system_db.get(GpuJob, job.id)
    assert refreshed.status is GpuJobStatus.FAILED
    assert quota.quota_status(system_db, tenant.id).reserved_seconds == pytest.approx(0)


def test_cancelling_gives_the_reservation_back(allocated, system_db) -> None:
    tenant, _ = allocated("acme")
    job = dispatcher.enqueue_job(system_db, tenant_id=tenant.id, kind=GpuJobKind.IMAGE_FLUX)
    system_db.flush()

    dispatcher.cancel_job(system_db, job.id)
    assert quota.quota_status(system_db, tenant.id).reserved_seconds == pytest.approx(0)


def test_an_expired_lease_returns_to_the_queue(allocated, system_db) -> None:
    """A crashed GPU worker must not park the only card forever."""
    tenant, _ = allocated("acme")
    job = dispatcher.enqueue_job(system_db, tenant_id=tenant.id, kind=GpuJobKind.IMAGE_FLUX)
    system_db.flush()
    dispatcher.claim_next_batch(system_db, "doomed-worker")
    system_db.flush()

    later = datetime.now(UTC) + timedelta(days=1)
    reclaimed = dispatcher.reclaim_expired_leases(system_db, now=later)
    system_db.flush()

    assert reclaimed == 1
    refreshed = system_db.get(GpuJob, job.id)
    assert refreshed.status is GpuJobStatus.PENDING
    assert refreshed.lease_owner is None


def test_a_deferred_job_is_not_claimed_early(allocated, system_db) -> None:
    tenant, _ = allocated("acme")
    job = dispatcher.enqueue_job(system_db, tenant_id=tenant.id, kind=GpuJobKind.IMAGE_FLUX)
    job.available_at = datetime.now(UTC) + timedelta(hours=1)
    system_db.flush()

    assert dispatcher.claim_next_batch(system_db, "worker-1") is None


def test_queue_depth_counts_pending_work_per_window(allocated, system_db) -> None:
    tenant, _ = allocated("acme")
    dispatcher.enqueue_job(system_db, tenant_id=tenant.id, kind=GpuJobKind.LLM_TEXT)
    dispatcher.enqueue_job(system_db, tenant_id=tenant.id, kind=GpuJobKind.IMAGE_FLUX)
    dispatcher.enqueue_job(system_db, tenant_id=tenant.id, kind=GpuJobKind.IMAGE_FLUX)
    system_db.flush()

    assert dispatcher.queue_depth(system_db) == {"text": 1, "media": 2}


def test_the_card_rotates_between_tenants(allocated, system_db) -> None:
    """Section 6: one customer must not be able to monopolise the GPU."""
    heavy, _ = allocated("heavy")
    light, _ = allocated("light")

    for _ in range(12):
        dispatcher.enqueue_job(system_db, tenant_id=heavy.id, kind=GpuJobKind.IMAGE_FLUX)
    dispatcher.enqueue_job(system_db, tenant_id=light.id, kind=GpuJobKind.IMAGE_FLUX)
    system_db.flush()

    batch = dispatcher.claim_next_batch(system_db, "worker-1")
    assert batch is not None
    tenants = {system_db.get(GpuJob, jid).tenant_id for jid in batch.job_ids}
    assert light.id in tenants
