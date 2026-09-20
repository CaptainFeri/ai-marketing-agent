"""Generated media and the speaker profiles behind ``face`` mode (D7)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.enums import MediaKind, SpeakerKind

if TYPE_CHECKING:
    from app.db.models.content import ContentPackage


class MediaAsset(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """An image, video, audio track or subtitle file in MinIO."""

    __tablename__ = "media_asset"
    __table_args__ = (Index("ix_media_asset_package_kind", "package_id", "kind"),)

    package_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_package.id", ondelete="CASCADE"), nullable=False
    )
    variant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("variant.id", ondelete="SET NULL")
    )
    kind: Mapped[MediaKind] = mapped_column(
        Enum(MediaKind, native_enum=False, length=16, name="media_kind"), nullable=False
    )
    # Object key inside the bucket; always starts with the tenant prefix.
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(128))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    prompt: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(String(200))
    gpu_seconds: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    # Set by a human at gate 2.
    is_selected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Decision D7: anything built from a real face or voice is labelled.
    is_ai_labelled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    speaker_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("speaker_profile.id", ondelete="SET NULL")
    )
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    package: Mapped[ContentPackage] = relationship(back_populates="media_assets")
    speaker_profile: Mapped[SpeakerProfile | None] = relationship(back_populates="media_assets")


class SpeakerProfile(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """A real face and/or voice usable in ``face`` mode.

    Decision D7: no generation may start unless a signed consent document is
    on file and still valid.  ``is_usable`` is the single check the media
    tasks call before touching the profile.
    """

    __tablename__ = "speaker_profile"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[SpeakerKind] = mapped_column(
        Enum(SpeakerKind, native_enum=False, length=32, name="speaker_kind"), nullable=False
    )
    # Reference material: portrait, 30-60s reference video, voice samples.
    assets: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    # The signed consent form itself, stored like any other tenant file.
    consent_document_key: Mapped[str | None] = mapped_column(String(1024))
    consent_signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consent_expires_on: Mapped[date | None] = mapped_column(Date)
    consent_notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    media_assets: Mapped[list[MediaAsset]] = relationship(back_populates="speaker_profile")

    def consent_is_valid(self, on: date | None = None) -> bool:
        if not self.consent_document_key or self.consent_signed_at is None:
            return False
        if self.consent_expires_on is None:
            return True
        return (on or date.today()) <= self.consent_expires_on

    def is_usable(self, on: date | None = None) -> bool:
        return self.is_active and self.consent_is_valid(on)
