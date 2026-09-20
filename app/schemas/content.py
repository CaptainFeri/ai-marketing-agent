"""Content package, step run, variant and approval payloads."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field

from app.db.enums import (
    ApprovalDecision,
    ApprovalGate,
    Channel,
    MediaKind,
    PackageStatus,
    PipelineStep,
    PublicationStatus,
    StepStatus,
    TopicStatus,
    VideoMode,
)
from app.schemas.common import ORMModel


class TopicCreate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    locale: str
    pillar: str | None = None
    keywords: list[str] = Field(default_factory=list)
    search_intent: str | None = None
    priority: int = Field(default=100, ge=1, le=1000)
    planned_for: date | None = None
    notes: str | None = None


class TopicOut(ORMModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    title: str
    locale: str
    pillar: str | None
    keywords: list
    status: TopicStatus
    priority: int
    planned_for: date | None


class PackageCreate(BaseModel):
    workspace_id: uuid.UUID
    title: str = Field(min_length=1, max_length=500)
    locale: str
    topic_id: uuid.UUID | None = None
    # Overrides the brand brief default for this package only (decision D4).
    video_mode: VideoMode | None = None


class StepRunOut(ORMModel):
    id: uuid.UUID
    step: PipelineStep
    attempt: int
    status: StepStatus
    model: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    gpu_seconds: float
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None


class VariantOut(ORMModel):
    id: uuid.UUID
    channel: Channel
    ab_label: str | None
    body: dict
    visual_brief: dict | None
    is_selected: bool


class MediaAssetOut(ORMModel):
    id: uuid.UUID
    kind: MediaKind
    storage_key: str
    mime_type: str | None
    width: int | None
    height: int | None
    duration_seconds: float | None
    prompt: str | None
    model: str | None
    gpu_seconds: float
    is_selected: bool
    is_ai_labelled: bool
    #: A URL the panel can load the object from. Not an ORM attribute — the
    #: API layer fills it in from the storage backend, since generating it
    #: means a call out to MinIO (a presigned GET) that has no business
    #: happening inside a pydantic validator.
    url: str | None = None


class MediaAssetSelect(BaseModel):
    is_selected: bool = True


class VariantSelect(BaseModel):
    is_selected: bool = True


class PublicationCreate(BaseModel):
    variant_id: uuid.UUID
    scheduled_at: datetime


class PublicationOut(ORMModel):
    id: uuid.UUID
    package_id: uuid.UUID
    variant_id: uuid.UUID | None
    channel: Channel
    status: PublicationStatus
    scheduled_at: datetime
    published_at: datetime | None
    external_id: str | None
    external_url: str | None
    attempt_count: int
    last_error: str | None
    operator_alerted: bool
    created_at: datetime


class PackageOut(ORMModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    title: str
    locale: str
    status: PackageStatus
    video_mode: VideoMode
    current_step: PipelineStep | None
    qa_retry_count: int
    qa_score: float | None
    gpu_seconds: float
    created_at: datetime
    updated_at: datetime


class PackageDetail(PackageOut):
    article: dict | None
    step_runs: list[StepRunOut] = Field(default_factory=list)
    variants: list[VariantOut] = Field(default_factory=list)
    media_assets: list[MediaAssetOut] = Field(default_factory=list)


class ApprovalRequest(BaseModel):
    """A decision at gate 1 or gate 2."""

    decision: ApprovalDecision
    feedback: str | None = Field(default=None, max_length=5000)
    # Only meaningful with CHANGES_REQUESTED: which step to restart from.
    return_to_step: PipelineStep | None = None


class ApprovalOut(ORMModel):
    id: uuid.UUID
    gate: ApprovalGate
    decision: ApprovalDecision
    feedback: str | None
    return_to_step: PipelineStep | None
    decided_by_id: uuid.UUID | None
    decided_at: datetime | None
