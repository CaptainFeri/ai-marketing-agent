"""Tenant, user, membership and workspace — the multi-tenancy backbone (D8)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
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
from app.db.enums import Plan, Role

if TYPE_CHECKING:
    from app.db.models.content import BrandBrief


class Tenant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A paying customer.  Owns workspaces, files and channel credentials."""

    __tablename__ = "tenant"

    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    plan: Mapped[Plan] = mapped_column(
        enum_column(Plan, length=32, name="plan"),
        default=Plan.TRIAL,
        nullable=False,
    )
    # Relative share of the daily GPU seconds (section 7, step 3).
    quota_weight: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # MinIO prefix; defaults to the slug but stays stable if the slug changes.
    storage_prefix: Mapped[str] = mapped_column(String(64), nullable=False)
    settings_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    workspaces: Mapped[list[Workspace]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan"
    )
    memberships: Mapped[list[Membership]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan"
    )


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A person.  Users are global; their access is granted per tenant."""

    __tablename__ = "app_user"

    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Operators of the platform itself, not of a tenant.
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    locale: Mapped[str] = mapped_column(String(8), default="fa", nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    memberships: Mapped[list[Membership]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Membership(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """Grants a user a role inside a tenant, optionally scoped to one workspace.

    ``workspace_id IS NULL`` means the role applies to every workspace of the
    tenant, which is how owners and admins are represented.
    """

    __tablename__ = "membership"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", "workspace_id", name="tenant_user_workspace"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspace.id", ondelete="CASCADE")
    )
    role: Mapped[Role] = mapped_column(enum_column(Role, length=16, name="role"), nullable=False)

    tenant: Mapped[Tenant] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship(back_populates="memberships")
    workspace: Mapped[Workspace | None] = relationship(back_populates="memberships")


class Workspace(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """One brand.  A tenant can run several brands side by side."""

    __tablename__ = "workspace"
    __table_args__ = (UniqueConstraint("tenant_id", "slug", name="tenant_slug"),)

    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Primary market language; language children of a package use the others.
    default_locale: Mapped[str] = mapped_column(String(8), default="fa", nullable=False)
    locales: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Tehran", nullable=False)
    # Persian (Jalali) or Gregorian calendar in the panel.
    calendar: Mapped[str] = mapped_column(String(16), default="jalali", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    tenant: Mapped[Tenant] = relationship(back_populates="workspaces")
    memberships: Mapped[list[Membership]] = relationship(back_populates="workspace")
    brand_briefs: Mapped[list[BrandBrief]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
