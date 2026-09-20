"""The GPU scheduler — handoff section 6.

One RTX 3090 Ti with 24 GB cannot hold the LLM and the media models at the
same time, so a single component owns the card and decides what it is loaded
with.  It works in two *windows*:

``text``
    vLLM holds Qwen3; the six text agents and the marketizer run here.
``media``
    FLUX, Wan, Whisper, the lip-sync models and Chatterbox run here.

Switching windows costs a model reload (measured in phase 0, stored in
``GPU_WINDOW_SWITCH_SECONDS``), so the scheduler:

* stays in the window it is already in while that window has work;
* switches when the idle window has been waiting longer than
  ``GPU_WINDOW_STARVATION_SECONDS``, so neither side starves;
* batches jobs of the same *kind* — one model load serves the whole batch;
* rotates between tenants by weighted deficit, so one customer cannot take
  the whole card;
* holds heavy video work back for the low-traffic night window.

:func:`select_batch` is deliberately a pure function over plain dataclasses:
the policy is the part worth testing, and it needs no database to exercise.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from app.core.config import settings
from app.db.enums import GpuJobKind, GpuWindow


@dataclass(frozen=True)
class SchedulableJob:
    """A pending job, reduced to what the policy needs."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    kind: GpuJobKind
    window: GpuWindow
    priority: int
    nightly_only: bool
    estimated_seconds: float
    created_at: datetime


@dataclass(frozen=True)
class SchedulerConfig:
    max_batch_size: int = settings.gpu_max_batch_size
    max_batch_seconds: float = float(settings.gpu_max_batch_seconds)
    window_starvation_seconds: float = float(settings.gpu_window_starvation_seconds)
    night_start_hour: int = settings.gpu_night_window_start_hour
    night_end_hour: int = settings.gpu_night_window_end_hour


@dataclass(frozen=True)
class Batch:
    """A group of jobs that will run back to back under one model load."""

    window: GpuWindow
    kind: GpuJobKind
    job_ids: tuple[uuid.UUID, ...]
    estimated_seconds: float
    switch_required: bool
    #: Why this window won, for the scheduler log.
    reason: str
    batch_id: uuid.UUID = field(default_factory=uuid.uuid4)

    def __len__(self) -> int:
        return len(self.job_ids)


def in_night_window(now: datetime, config: SchedulerConfig) -> bool:
    """True inside the low-traffic hours, handling a range that wraps midnight."""
    start, end = config.night_start_hour, config.night_end_hour
    hour = now.hour
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def _eligible(
    jobs: list[SchedulableJob], now: datetime, config: SchedulerConfig
) -> list[SchedulableJob]:
    night = in_night_window(now, config)
    return [job for job in jobs if night or not job.nightly_only]


def _tenant_deficits(
    jobs: list[SchedulableJob],
    tenant_weights: dict[uuid.UUID, int],
    consumed_today: dict[uuid.UUID, float],
) -> dict[uuid.UUID, float]:
    """How far behind its fair share each waiting tenant is.

    Positive means under-served, so it should go first.  A tenant with no
    recorded weight is treated as weight 1 rather than excluded — a missing
    row must not silently park someone's work forever.
    """
    waiting = {job.tenant_id for job in jobs}
    weights = {tenant: max(1, tenant_weights.get(tenant, 1)) for tenant in waiting}
    total_weight = sum(weights.values())
    total_consumed = sum(consumed_today.get(tenant, 0.0) for tenant in waiting)

    deficits: dict[uuid.UUID, float] = {}
    for tenant, weight in weights.items():
        fair_share = weight / total_weight
        used_share = consumed_today.get(tenant, 0.0) / total_consumed if total_consumed > 0 else 0.0
        deficits[tenant] = fair_share - used_share
    return deficits


def _oldest_wait(jobs: list[SchedulableJob], now: datetime) -> float:
    if not jobs:
        return 0.0
    oldest = min(job.created_at for job in jobs)
    return max(0.0, (now - oldest).total_seconds())


def _sort_key(job: SchedulableJob) -> tuple[int, datetime]:
    return (job.priority, job.created_at)


def select_batch(
    jobs: list[SchedulableJob],
    *,
    now: datetime,
    current_window: GpuWindow | None = None,
    current_kind: GpuJobKind | None = None,
    tenant_weights: dict[uuid.UUID, int] | None = None,
    consumed_today: dict[uuid.UUID, float] | None = None,
    config: SchedulerConfig | None = None,
) -> Batch | None:
    """Choose the next group of jobs to run, or ``None`` when nothing is ready.

    ``jobs`` is every pending job across every tenant.  ``consumed_today`` is
    GPU seconds already spent per tenant, which is what makes the rotation
    fair rather than merely round-robin.
    """
    config = config or SchedulerConfig()
    tenant_weights = tenant_weights or {}
    consumed_today = consumed_today or {}

    candidates = _eligible(jobs, now, config)
    if not candidates:
        return None

    by_window: dict[GpuWindow, list[SchedulableJob]] = defaultdict(list)
    for job in candidates:
        by_window[job.window].append(job)

    window, reason = _choose_window(by_window, now, current_window, config)
    window_jobs = by_window[window]

    deficits = _tenant_deficits(candidates, tenant_weights, consumed_today)
    lead = min(
        window_jobs,
        key=lambda job: (-deficits.get(job.tenant_id, 0.0), job.priority, job.created_at),
    )

    # One model load per batch, so everything in it must share a kind.
    same_kind = sorted(
        (job for job in window_jobs if job.kind == lead.kind),
        key=lambda job: (-deficits.get(job.tenant_id, 0.0), job.priority, job.created_at),
    )
    chosen = _fill_batch(same_kind, lead, config)

    # A load is paid whenever the card is not already holding this exact
    # model — including on a cold start, where ``current_kind`` is None.
    switch_required = current_kind != lead.kind or (
        current_window is not None and current_window != window
    )
    return Batch(
        window=window,
        kind=lead.kind,
        job_ids=tuple(job.id for job in chosen),
        estimated_seconds=sum(job.estimated_seconds for job in chosen),
        switch_required=switch_required,
        reason=reason,
    )


def _choose_window(
    by_window: dict[GpuWindow, list[SchedulableJob]],
    now: datetime,
    current_window: GpuWindow | None,
    config: SchedulerConfig,
) -> tuple[GpuWindow, str]:
    populated = [w for w in (GpuWindow.TEXT, GpuWindow.MEDIA) if by_window.get(w)]
    if len(populated) == 1:
        return populated[0], "only window with work"

    other = GpuWindow.MEDIA if current_window is GpuWindow.TEXT else GpuWindow.TEXT
    if current_window is not None and by_window.get(current_window):
        # Switching costs a reload, so only do it for a starving queue.
        if _oldest_wait(by_window[other], now) >= config.window_starvation_seconds:
            return other, "other window starving"
        return current_window, "staying in current window"

    # Cold start, or the current window has nothing: take the longest wait.
    best = max(populated, key=lambda w: _oldest_wait(by_window[w], now))
    return best, "longest waiting queue"


def _fill_batch(
    ordered: list[SchedulableJob], lead: SchedulableJob, config: SchedulerConfig
) -> list[SchedulableJob]:
    """Take jobs in fairness order, one per tenant per pass.

    Round-robin within the batch keeps a tenant with fifty queued images from
    filling the whole batch while another waits for one.
    """
    per_tenant: dict[uuid.UUID, list[SchedulableJob]] = defaultdict(list)
    for job in ordered:
        per_tenant[job.tenant_id].append(job)
    for queue in per_tenant.values():
        queue.sort(key=_sort_key)

    # Tenant order follows the fairness order the caller already applied.
    tenant_order: list[uuid.UUID] = []
    for job in ordered:
        if job.tenant_id not in tenant_order:
            tenant_order.append(job.tenant_id)

    chosen: list[SchedulableJob] = []
    total = 0.0
    while len(chosen) < config.max_batch_size:
        progressed = False
        for tenant in tenant_order:
            queue = per_tenant[tenant]
            if not queue:
                continue
            job = queue[0]
            # The lead job always goes in, even if it alone exceeds the cap:
            # a 13-minute Wan clip must still be runnable.
            if chosen and total + job.estimated_seconds > config.max_batch_seconds:
                continue
            queue.pop(0)
            chosen.append(job)
            total += job.estimated_seconds
            progressed = True
            if len(chosen) >= config.max_batch_size:
                break
        if not progressed:
            break

    if lead not in chosen:  # pragma: no cover - defensive
        chosen.insert(0, lead)
    return chosen
