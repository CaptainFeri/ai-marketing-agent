"""Database side of the GPU scheduler.

:mod:`app.worker.gpu_scheduler` decides *what* runs next; this module is what
actually claims those jobs, leases them to a worker, and books the GPU seconds
they used back into the quota ledger and the cost estimates.

All of it runs over :func:`app.db.tenancy.system_session` because choosing
between tenants is inherently cross-tenant work.  It is the only module apart
from the nightly maintenance tasks that legitimately does so.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import cast

from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.enums import GpuJobKind, GpuJobStatus, GpuWindow
from app.db.models import (
    ContentPackage,
    GpuJob,
    GpuQuotaLedger,
    GpuWindowState,
    MediaAsset,
    StepRun,
    Tenant,
)
from app.services import quota
from app.worker.gpu_scheduler import Batch, SchedulableJob, SchedulerConfig, select_batch

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# enqueue
# --------------------------------------------------------------------------
def enqueue_job(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    kind: GpuJobKind,
    payload: dict | None = None,
    workspace_id: uuid.UUID | None = None,
    package_id: uuid.UUID | None = None,
    step_run_id: uuid.UUID | None = None,
    media_asset_id: uuid.UUID | None = None,
    priority: int = 100,
    locale: str = quota.ANY_LOCALE,
    reserve_quota: bool = True,
) -> GpuJob:
    """Queue one unit of GPU work and hold its estimated cost against quota.

    Raises :class:`~app.core.errors.QuotaExceededError` when the tenant's
    share for today is spent, which the API turns into "this will run
    tomorrow" rather than an error the customer has to act on.
    """
    estimated = quota.estimate_seconds(session, kind, locale)
    if reserve_quota:
        quota.reserve(session, tenant_id, estimated)

    job = GpuJob(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        package_id=package_id,
        step_run_id=step_run_id,
        media_asset_id=media_asset_id,
        kind=kind,
        window=kind.window,
        status=GpuJobStatus.PENDING,
        priority=priority,
        nightly_only=kind.nightly_only,
        estimated_seconds=estimated,
        payload=payload or {},
    )
    session.add(job)
    session.flush()
    return job


# --------------------------------------------------------------------------
# claim
# --------------------------------------------------------------------------
def _window_state(session: Session) -> GpuWindowState:
    state = session.scalars(select(GpuWindowState).limit(1)).one_or_none()
    if state is None:
        state = GpuWindowState(singleton=True)
        session.add(state)
        session.flush()
    return state


def _pending_jobs(session: Session) -> list[GpuJob]:
    now = datetime.now(UTC)
    return list(
        session.scalars(
            select(GpuJob)
            .where(
                GpuJob.status == GpuJobStatus.PENDING,
                (GpuJob.available_at.is_(None)) | (GpuJob.available_at <= now),
            )
            .order_by(GpuJob.created_at)
            # A generous ceiling: the policy only needs a representative view
            # of the queue, not every one of ten thousand rows.
            .limit(500)
        ).all()
    )


def _tenant_weights(session: Session) -> dict[uuid.UUID, int]:
    rows = session.execute(
        select(Tenant.id, Tenant.quota_weight).where(Tenant.is_active.is_(True))
    ).all()
    return {row[0]: max(1, row[1]) for row in rows}


def _consumed_today(session: Session, day: date) -> dict[uuid.UUID, float]:
    rows = session.execute(
        select(GpuQuotaLedger.tenant_id, GpuQuotaLedger.consumed_seconds).where(
            GpuQuotaLedger.day == day
        )
    ).all()
    return {row[0]: float(row[1]) for row in rows}


#: Below this many samples the measured average is too noisy to batch against.
MIN_SWITCH_SAMPLES = 3

#: Weight of a new switch measurement in the running average.
SWITCH_EWMA_ALPHA = 0.25


def measured_switch_seconds(session: Session) -> float | None:
    """The observed cost of a window switch, once there is enough evidence.

    ``app.services.tuning`` estimates this from the hardware at install time;
    this is the number that replaces the estimate in operation.
    """
    state = session.scalars(select(GpuWindowState).limit(1)).one_or_none()
    if state is None or state.switch_samples < MIN_SWITCH_SAMPLES:
        return None
    return state.ewma_switch_seconds


def record_switch(session: Session, seconds: float) -> None:
    """Fold a measured switch into the running average."""
    if seconds <= 0:
        return
    state = _window_state(session)
    state.last_switch_seconds = seconds
    if state.ewma_switch_seconds is None:
        state.ewma_switch_seconds = seconds
    else:
        state.ewma_switch_seconds = (
            SWITCH_EWMA_ALPHA * seconds + (1 - SWITCH_EWMA_ALPHA) * state.ewma_switch_seconds
        )
    state.switch_samples += 1


def effective_scheduler_config(session: Session) -> SchedulerConfig:
    """The scheduler's settings, with the measured switch cost applied.

    A switch that turns out to cost two minutes rather than forty seconds
    means batches should be roughly three times longer; leaving the configured
    estimate in place would spend the day reloading models.
    """
    measured = measured_switch_seconds(session)
    if measured is None:
        return SchedulerConfig()
    from app.services.tuning import TARGET_SWITCH_OVERHEAD

    batch_seconds = min(
        3600.0,
        max(600.0, measured * (1 - TARGET_SWITCH_OVERHEAD) / TARGET_SWITCH_OVERHEAD),
    )
    return SchedulerConfig(
        max_batch_seconds=batch_seconds,
        window_starvation_seconds=max(1800.0, batch_seconds * 2),
    )


def claim_next_batch(
    session: Session,
    worker_id: str,
    *,
    now: datetime | None = None,
    config: SchedulerConfig | None = None,
) -> Batch | None:
    """Pick the next batch and lease it to ``worker_id``.

    Returns ``None`` when there is nothing eligible — an idle GPU, or only
    nightly jobs outside the night window.
    """
    now = now or datetime.now(UTC)
    pending = _pending_jobs(session)
    if not pending:
        return None

    config = config or effective_scheduler_config(session)
    state = _window_state(session)
    batch = select_batch(
        [
            SchedulableJob(
                id=job.id,
                tenant_id=job.tenant_id,
                kind=job.kind,
                window=job.window,
                priority=job.priority,
                nightly_only=job.nightly_only,
                estimated_seconds=job.estimated_seconds,
                created_at=_as_aware(job.created_at),
            )
            for job in pending
        ],
        now=now,
        current_window=state.current_window,
        current_kind=state.current_kind,
        tenant_weights=_tenant_weights(session),
        consumed_today=_consumed_today(session, now.date()),
        config=config,
    )
    if batch is None:
        return None

    lease_expires = now + timedelta(seconds=settings.gpu_lease_timeout_seconds)
    claim_result = cast(
        CursorResult,
        session.execute(
            update(GpuJob)
            .where(
                GpuJob.id.in_(batch.job_ids),
                # Guard against a second scheduler instance having taken them.
                GpuJob.status == GpuJobStatus.PENDING,
            )
            .values(
                status=GpuJobStatus.LEASED,
                batch_id=batch.batch_id,
                lease_owner=worker_id,
                lease_expires_at=lease_expires,
            )
        ),
    )
    claimed = claim_result.rowcount

    if claimed == 0:
        logger.info("batch lost to another scheduler", extra={"batch_id": str(batch.batch_id)})
        return None

    if batch.switch_required:
        state.switched_at = now
        state.switch_count_today += 1
    state.current_window = batch.window
    state.current_kind = batch.kind

    logger.info(
        "gpu batch leased",
        extra={
            "batch_id": str(batch.batch_id),
            "window": batch.window.value,
            "kind": batch.kind.value,
            "jobs": claimed,
            "switch": batch.switch_required,
            "reason": batch.reason,
            "estimated_seconds": round(batch.estimated_seconds, 1),
        },
    )
    return batch


def _as_aware(value: datetime) -> datetime:
    """Normalise to UTC-aware; SQLite-backed tests hand back naive values."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


# --------------------------------------------------------------------------
# completion
# --------------------------------------------------------------------------
def start_job(session: Session, job_id: uuid.UUID) -> GpuJob:
    job = session.get(GpuJob, job_id)
    if job is None:
        raise LookupError(f"gpu job {job_id} not found")
    job.status = GpuJobStatus.RUNNING
    job.attempts += 1
    job.started_at = datetime.now(UTC)
    return job


def complete_job(
    session: Session,
    job_id: uuid.UUID,
    *,
    gpu_seconds: float,
    result: dict | None = None,
    locale: str = quota.ANY_LOCALE,
) -> GpuJob:
    """Book a finished job: quota, cost estimate and the owning record."""
    job = session.get(GpuJob, job_id)
    if job is None:
        raise LookupError(f"gpu job {job_id} not found")

    job.status = GpuJobStatus.SUCCEEDED
    job.gpu_seconds = gpu_seconds
    job.result = result
    job.finished_at = datetime.now(UTC)
    job.lease_owner = None
    job.lease_expires_at = None

    quota.consume(session, job.tenant_id, job.estimated_seconds, gpu_seconds)
    quota.record_actual(session, job.kind, gpu_seconds, locale)
    _propagate_gpu_seconds(session, job, gpu_seconds)
    return job


def fail_job(session: Session, job_id: uuid.UUID, error: str) -> GpuJob:
    """Retry the job if attempts remain, otherwise mark it failed.

    A retry keeps the original reservation — the work still has to happen.
    A final failure releases it, so a tenant is not charged for a job that
    never produced anything.
    """
    job = session.get(GpuJob, job_id)
    if job is None:
        raise LookupError(f"gpu job {job_id} not found")

    job.error = error[:4000]
    if job.attempts < job.max_attempts:
        job.status = GpuJobStatus.PENDING
        job.batch_id = None
        job.lease_owner = None
        job.lease_expires_at = None
        # Back off so a deterministic failure does not spin the GPU.
        job.available_at = datetime.now(UTC) + timedelta(seconds=60 * job.attempts)
        return job

    job.status = GpuJobStatus.FAILED
    job.finished_at = datetime.now(UTC)
    job.lease_owner = None
    job.lease_expires_at = None
    quota.release(session, job.tenant_id, job.estimated_seconds)
    return job


def cancel_job(session: Session, job_id: uuid.UUID) -> GpuJob:
    job = session.get(GpuJob, job_id)
    if job is None:
        raise LookupError(f"gpu job {job_id} not found")
    if job.status in {GpuJobStatus.SUCCEEDED, GpuJobStatus.FAILED, GpuJobStatus.CANCELLED}:
        return job
    job.status = GpuJobStatus.CANCELLED
    job.finished_at = datetime.now(UTC)
    quota.release(session, job.tenant_id, job.estimated_seconds)
    return job


def reclaim_expired_leases(session: Session, now: datetime | None = None) -> int:
    """Return jobs whose worker died back to the queue.

    Without this a crashed GPU worker would park its batch forever, and the
    single card would stall behind it.
    """
    now = now or datetime.now(UTC)
    result = cast(
        CursorResult,
        session.execute(
            update(GpuJob)
            .where(
                GpuJob.status.in_([GpuJobStatus.LEASED, GpuJobStatus.RUNNING]),
                GpuJob.lease_expires_at.is_not(None),
                GpuJob.lease_expires_at < now,
            )
            .values(
                status=GpuJobStatus.PENDING,
                lease_owner=None,
                lease_expires_at=None,
                batch_id=None,
                error="lease expired; worker presumed dead",
            )
        ),
    )
    if result.rowcount:
        logger.warning("reclaimed expired GPU leases", extra={"count": result.rowcount})
    return int(result.rowcount)


def _propagate_gpu_seconds(session: Session, job: GpuJob, gpu_seconds: float) -> None:
    """Mirror the measured time onto the record the job was queued for."""
    if job.step_run_id:
        step = session.get(StepRun, job.step_run_id)
        if step is not None:
            step.gpu_seconds = gpu_seconds
    if job.media_asset_id:
        asset = session.get(MediaAsset, job.media_asset_id)
        if asset is not None:
            asset.gpu_seconds = gpu_seconds
    if job.package_id:
        # An atomic increment rather than read-modify-write: several jobs for
        # one package can finish in separate sessions, and a lost update here
        # would understate what the package cost.
        session.execute(
            update(ContentPackage)
            .where(ContentPackage.id == job.package_id)
            .values(gpu_seconds=ContentPackage.gpu_seconds + gpu_seconds)
        )
        # Drop any stale copy this session already holds, without loading one.
        cached = session.identity_map.get(session.identity_key(ContentPackage, job.package_id))
        if cached is not None:
            session.expire(cached, ["gpu_seconds"])


def queue_depth(session: Session) -> dict[str, int]:
    """Pending job count per window, for the panel and for alerting."""
    rows = session.execute(
        select(GpuJob.window, func.count())
        .where(GpuJob.status == GpuJobStatus.PENDING)
        .group_by(GpuJob.window)
    ).all()
    depth = {window.value: 0 for window in GpuWindow}
    for window, count in rows:
        key = window.value if isinstance(window, GpuWindow) else str(window)
        depth[key] = int(count)
    return depth
