"""Publication, metrics and the encrypted channel credentials."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.enums import Channel, PublicationStatus

if TYPE_CHECKING:
    from app.db.models.content import ContentPackage


class Publication(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """A scheduled or completed post on one channel."""

    __tablename__ = "publication"
    __table_args__ = (
        Index("ix_publication_scheduled", "status", "scheduled_at"),
        Index("ix_publication_package", "package_id"),
    )

    package_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_package.id", ondelete="CASCADE"), nullable=False
    )
    variant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("variant.id", ondelete="SET NULL")
    )
    channel: Mapped[Channel] = mapped_column(
        Enum(Channel, native_enum=False, length=32, name="channel"), nullable=False
    )
    status: Mapped[PublicationStatus] = mapped_column(
        Enum(PublicationStatus, native_enum=False, length=32, name="publication_status"),
        default=PublicationStatus.SCHEDULED,
        nullable=False,
    )
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Identifier returned by the channel (WordPress post id, Telegram message id...).
    external_id: Mapped[str | None] = mapped_column(String(255))
    external_url: Mapped[str | None] = mapped_column(String(2048))
    utm: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    # Three attempts, then the operator is alerted (handoff section 3, step 6).
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    operator_alerted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    package: Mapped[ContentPackage] = relationship(back_populates="publications")
    metric_snapshots: Mapped[list[MetricSnapshot]] = relationship(
        back_populates="publication", cascade="all, delete-orphan"
    )


class MetricSnapshot(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """Daily performance numbers pulled from Search Console, GA4 or a channel."""

    __tablename__ = "metric_snapshot"
    __table_args__ = (
        UniqueConstraint("publication_id", "source", "captured_for", name="publication_source_day"),
    )

    publication_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("publication.id", ondelete="CASCADE"), nullable=False
    )
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    # The day the numbers describe, not the day they were fetched.
    captured_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    impressions: Mapped[int | None] = mapped_column(Integer)
    clicks: Mapped[int | None] = mapped_column(Integer)
    position: Mapped[float | None] = mapped_column(Float)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    publication: Mapped[Publication] = relationship(back_populates="metric_snapshots")


class ChannelCredential(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """Per-tenant channel token, encrypted at rest (see ``app.core.crypto``)."""

    __tablename__ = "channel_credential"
    __table_args__ = (
        UniqueConstraint("workspace_id", "channel", "label", name="workspace_channel_label"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[Channel] = mapped_column(
        Enum(Channel, native_enum=False, length=32, name="channel"), nullable=False
    )
    # Lets one workspace hold e.g. two Telegram channels.
    label: Mapped[str] = mapped_column(String(64), default="default", nullable=False)
    encrypted_payload: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # Safe to display: account name, site URL, channel handle.
    public_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
