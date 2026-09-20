"""GPU job queue, quota ledger and the measured cost estimates.

Handoff sections 6 and 7.  The single RTX 3090 Ti is the bottleneck of the
whole platform, so every job that needs it goes through ``gpu_job`` and every
second it spends is written back into ``gpu_quota_ledger``.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import (
    Base,
    TenantScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    enum_column,
)
from app.db.enums import GpuJobKind, GpuJobStatus, GpuWindow


class GpuJob(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """A unit of work that needs the GPU.

    Not listed in handoff section 9, but section 6 cannot be built without it:
    the scheduler needs a durable, cross-tenant view of what is waiting before
    it can batch by model and rotate fairly between tenants.
    """

    __tablename__ = "gpu_job"
    __table_args__ = (
        # The scheduler's hot query: pending jobs, oldest first, per window.
        Index("ix_gpu_job_dispatch", "status", "window", "kind", "created_at"),
        Index("ix_gpu_job_tenant_status", "tenant_id", "status"),
        Index("ix_gpu_job_lease", "status", "lease_expires_at"),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        CheckConstraint("estimated_seconds >= 0", name="estimate_non_negative"),
    )

    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspace.id", ondelete="CASCADE")
    )
    package_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_package.id", ondelete="CASCADE")
    )
    step_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("step_run.id", ondelete="CASCADE")
    )
    media_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("media_asset.id", ondelete="SET NULL")
    )

    kind: Mapped[GpuJobKind] = mapped_column(
        enum_column(GpuJobKind, length=32, name="gpu_job_kind"), nullable=False
    )
    # Denormalised from ``kind`` so the dispatch index can be used directly.
    window: Mapped[GpuWindow] = mapped_column(
        enum_column(GpuWindow, length=16, name="gpu_window"), nullable=False
    )
    status: Mapped[GpuJobStatus] = mapped_column(
        enum_column(GpuJobStatus, length=16, name="gpu_job_status"),
        default=GpuJobStatus.PENDING,
        nullable=False,
    )
    # Lower value runs first within the same tenant.
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    # Heavy video jobs wait for the low-traffic window (section 6).
    nightly_only: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Reserved against the tenant's quota up front; reconciled when it ends.
    estimated_seconds: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    gpu_seconds: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    # Identifies the worker holding the job; a lease that expires is requeued.
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set when the scheduler groups jobs so they share one model load.
    batch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)

    available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


class GpuQuotaLedger(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """One row per tenant per day (section 7).

    ``allocated_seconds`` is that tenant's share of the day's capacity,
    ``reserved_seconds`` covers work already queued and ``consumed_seconds``
    is what actually ran.  Remaining budget is
    ``allocated - reserved - consumed``.
    """

    __tablename__ = "gpu_quota_ledger"
    __table_args__ = (
        UniqueConstraint("tenant_id", "day", name="tenant_day"),
        CheckConstraint("reserved_seconds >= 0", name="reserved_non_negative"),
        CheckConstraint("consumed_seconds >= 0", name="consumed_non_negative"),
    )

    day: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    allocated_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    reserved_seconds: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    consumed_seconds: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    # Snapshot of the inputs, so a past day's split stays explainable after a
    # plan change or a capacity change.
    weight: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    capacity_seconds: Mapped[float] = mapped_column(Float, nullable=False)

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.allocated_seconds - self.reserved_seconds - self.consumed_seconds)


class GpuCostEstimate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Moving average of how long each job kind really takes (section 7, step 2).

    Deliberately *not* tenant scoped: the GPU behaves the same whoever queued
    the work, and a shared estimate converges far faster than a per-tenant one.
    """

    __tablename__ = "gpu_cost_estimate"
    __table_args__ = (UniqueConstraint("kind", "locale", name="kind_locale"),)

    kind: Mapped[GpuJobKind] = mapped_column(
        enum_column(GpuJobKind, length=32, name="gpu_job_kind"), nullable=False
    )
    # Persian articles are longer to generate than English ones; keep them apart.
    locale: Mapped[str] = mapped_column(String(8), default="*", nullable=False)
    ewma_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    samples: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_seconds: Mapped[float | None] = mapped_column(Float)


class GpuWindowState(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Single-row table recording which model family the GPU currently holds.

    The scheduler reads it to know whether the next batch needs a window
    switch, and writes it after every switch so a restarted scheduler does not
    assume a cold GPU.
    """

    __tablename__ = "gpu_window_state"

    singleton: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, unique=True)
    current_window: Mapped[GpuWindow | None] = mapped_column(
        enum_column(GpuWindow, length=16, name="gpu_window")
    )
    current_kind: Mapped[GpuJobKind | None] = mapped_column(
        enum_column(GpuJobKind, length=32, name="gpu_job_kind")
    )
    switched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    switch_count_today: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_switch_seconds: Mapped[float | None] = mapped_column(Float)
    # Measured cost of a switch, learned from operation.  Once there are a few
    # samples this replaces the estimate ``app.services.tuning`` derived from
    # the hardware probe, and the scheduler batches against the real number.
    ewma_switch_seconds: Mapped[float | None] = mapped_column(Float)
    switch_samples: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
