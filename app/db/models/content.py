"""Brand brief, topic, content package, step run, variant and approval."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import (
    Base,
    TenantScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    enum_column,
)
from app.db.enums import (
    ApprovalDecision,
    ApprovalGate,
    Channel,
    PackageStatus,
    PipelineStep,
    StepStatus,
    SuggestionStatus,
    TopicStatus,
    VideoMode,
)

if TYPE_CHECKING:
    from app.db.models.media import MediaAsset
    from app.db.models.publishing import Publication
    from app.db.models.tenancy import Workspace


class BrandBrief(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """The questionnaire answer, versioned.

    ``data`` holds the fields listed in handoff section 9: brand, voice,
    visual_identity, competitors, personas[], seed_keywords, pillars[],
    ai_questions[], channels[], goals, video_mode.  It is validated against
    ``app.schemas.brief.BrandBriefData`` before it is written.
    """

    __tablename__ = "brand_brief"
    __table_args__ = (UniqueConstraint("workspace_id", "version", name="workspace_version"),)

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # Default for new packages; an editor may override it per package (D4).
    video_mode: Mapped[VideoMode] = mapped_column(
        enum_column(VideoMode, length=16, name="video_mode"),
        default=VideoMode.VOICE,
        nullable=False,
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="SET NULL")
    )

    workspace: Mapped[Workspace] = relationship(back_populates="brand_briefs")


class BriefDraft(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """A questionnaire in progress (handoff phase 1, week 3).

    One per workspace. Answers are saved as the customer fills the wizard in,
    so a half-finished brief survives a closed tab.

    The assistant's suggestions are kept separate from the customer's answers
    and never merged automatically: a suggestion the customer did not notice
    is worse than a blank field, so accepting one is an explicit action.
    """

    __tablename__ = "brief_draft"
    # Not simply "workspace": PostgreSQL puts constraint-backing indexes in
    # the same namespace as tables, and a table called workspace exists.
    __table_args__ = (UniqueConstraint("workspace_id", name="one_per_workspace"),)

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False
    )
    answers: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    suggestion: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    suggestion_status: Mapped[SuggestionStatus] = mapped_column(
        enum_column(SuggestionStatus, length=16, name="suggestion_status"),
        default=SuggestionStatus.IDLE,
        nullable=False,
    )
    suggestion_error: Mapped[str | None] = mapped_column(Text)
    suggestion_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    #: The URL the assistant last read, and what it took from it. Kept so a
    #: re-run does not have to fetch the site again, and so the customer can
    #: see what the suggestion was actually based on.
    website_url: Mapped[str | None] = mapped_column(String(2048))
    website_excerpt: Mapped[str | None] = mapped_column(Text)
    website_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    #: Set once the draft has been turned into a BrandBrief version.
    submitted_brief_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("brand_brief.id", ondelete="SET NULL")
    )


class Topic(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """An entry in the editorial calendar, produced by the topic planner agent."""

    __tablename__ = "topic"
    __table_args__ = (Index("ix_topic_workspace_status", "workspace_id", "status"),)

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    locale: Mapped[str] = mapped_column(String(8), nullable=False)
    pillar: Mapped[str | None] = mapped_column(String(200))
    # Researched per language, not translated (D3).
    keywords: Mapped[list[Any]] = mapped_column(JSONB, default=list, nullable=False)
    search_intent: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[TopicStatus] = mapped_column(
        enum_column(TopicStatus, length=32, name="topic_status"),
        default=TopicStatus.PROPOSED,
        nullable=False,
    )
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    planned_for: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)


class ContentPackage(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """One unit of work moving through the seven stages."""

    __tablename__ = "content_package"
    __table_args__ = (
        Index("ix_content_package_workspace_status", "workspace_id", "status"),
        Index("ix_content_package_parent", "parent_package_id"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False
    )
    topic_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("topic.id", ondelete="SET NULL")
    )
    brand_brief_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("brand_brief.id", ondelete="SET NULL")
    )
    # Language children of a package share the parent's research (phase 2).
    parent_package_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_package.id", ondelete="SET NULL")
    )

    title: Mapped[str] = mapped_column(String(500), nullable=False)
    locale: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[PackageStatus] = mapped_column(
        enum_column(PackageStatus, length=32, name="package_status"),
        default=PackageStatus.PLANNED,
        nullable=False,
    )
    video_mode: Mapped[VideoMode] = mapped_column(
        enum_column(VideoMode, length=16, name="video_mode"),
        default=VideoMode.VOICE,
        nullable=False,
    )
    current_step: Mapped[PipelineStep | None] = mapped_column(
        enum_column(PipelineStep, length=32, name="pipeline_step")
    )
    # The QA loop returns to the writer at most ``settings.qa_max_retries``
    # times before the package is handed to a human.
    qa_retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    qa_score: Mapped[float | None] = mapped_column(Float)
    # Accumulated across every StepRun and MediaAsset; drives the quota ledger.
    gpu_seconds: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    article: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    failure_reason: Mapped[str | None] = mapped_column(Text)

    step_runs: Mapped[list[StepRun]] = relationship(
        back_populates="package", cascade="all, delete-orphan"
    )
    variants: Mapped[list[Variant]] = relationship(
        back_populates="package", cascade="all, delete-orphan"
    )
    approvals: Mapped[list[Approval]] = relationship(
        back_populates="package", cascade="all, delete-orphan"
    )
    media_assets: Mapped[list[MediaAsset]] = relationship(
        back_populates="package", cascade="all, delete-orphan"
    )
    publications: Mapped[list[Publication]] = relationship(
        back_populates="package", cascade="all, delete-orphan"
    )


class StepRun(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """One execution of one agent.

    Inputs and outputs are kept so a step can be re-run in isolation, and
    ``gpu_seconds`` is what section 7 feeds into the quota estimator.
    """

    __tablename__ = "step_run"
    __table_args__ = (Index("ix_step_run_package_step", "package_id", "step"),)

    package_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_package.id", ondelete="CASCADE"), nullable=False
    )
    step: Mapped[PipelineStep] = mapped_column(
        enum_column(PipelineStep, length=32, name="pipeline_step"), nullable=False
    )
    attempt: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[StepStatus] = mapped_column(
        enum_column(StepStatus, length=16, name="step_status"),
        default=StepStatus.PENDING,
        nullable=False,
    )
    model: Mapped[str | None] = mapped_column(String(200))
    input_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    output_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    gpu_seconds: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    trace_id: Mapped[str | None] = mapped_column(String(64))

    package: Mapped[ContentPackage] = relationship(back_populates="step_runs")


class Variant(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """A channel-specific rendering of the package, optionally an A/B arm."""

    __tablename__ = "variant"
    __table_args__ = (Index("ix_variant_package_channel", "package_id", "channel"),)

    package_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_package.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[Channel] = mapped_column(
        enum_column(Channel, length=32, name="channel"), nullable=False
    )
    # "a" / "b" for hook experiments; NULL when there is a single version.
    ab_label: Mapped[str | None] = mapped_column(String(8))
    body: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # What the image/video agents are asked to produce for this variant.
    visual_brief: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    is_selected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    package: Mapped[ContentPackage] = relationship(back_populates="variants")


class Approval(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """A decision taken at one of the two human gates."""

    __tablename__ = "approval"
    __table_args__ = (Index("ix_approval_package_gate", "package_id", "gate"),)

    package_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_package.id", ondelete="CASCADE"), nullable=False
    )
    gate: Mapped[ApprovalGate] = mapped_column(
        enum_column(ApprovalGate, length=16, name="approval_gate"), nullable=False
    )
    decision: Mapped[ApprovalDecision] = mapped_column(
        enum_column(ApprovalDecision, length=32, name="approval_decision"),
        nullable=False,
    )
    feedback: Mapped[str | None] = mapped_column(Text)
    # When the editor sends the package back, which step it restarts from.
    return_to_step: Mapped[PipelineStep | None] = mapped_column(
        enum_column(PipelineStep, length=32, name="pipeline_step")
    )
    decided_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    package: Mapped[ContentPackage] = relationship(back_populates="approvals")
