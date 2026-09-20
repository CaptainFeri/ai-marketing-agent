"""GPU queue and quota payloads (handoff sections 6 and 7)."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel

from app.db.enums import GpuJobKind, GpuJobStatus, GpuWindow
from app.schemas.common import ORMModel


class GpuJobOut(ORMModel):
    id: uuid.UUID
    kind: GpuJobKind
    window: GpuWindow
    status: GpuJobStatus
    priority: int
    nightly_only: bool
    estimated_seconds: float
    gpu_seconds: float
    attempts: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None


class QuotaAffordance(BaseModel):
    """How many packages of a given shape today's remaining budget still buys.

    This is what the panel shows the customer (section 7, step 4).
    """

    label: str
    estimated_seconds_each: float
    affordable_today: int


class QuotaStatus(BaseModel):
    tenant_id: uuid.UUID
    day: date
    capacity_seconds: float
    weight: int
    allocated_seconds: float
    reserved_seconds: float
    consumed_seconds: float
    remaining_seconds: float
    affordances: list[QuotaAffordance]


class WindowStateOut(BaseModel):
    current_window: GpuWindow | None
    current_kind: GpuJobKind | None
    switched_at: datetime | None
    switch_count_today: int
    pending_by_window: dict[str, int]
