"""Tenant and workspace payloads."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.db.enums import Plan
from app.schemas.common import ORMModel

SLUG_PATTERN = r"^[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?$"


class TenantCreate(BaseModel):
    slug: str = Field(pattern=SLUG_PATTERN)
    name: str = Field(min_length=1, max_length=200)
    plan: Plan = Plan.TRIAL
    quota_weight: int = Field(default=1, ge=1, le=100)
    # The first owner is created together with the tenant.
    owner_email: str
    owner_password: str = Field(min_length=8, max_length=512)
    owner_full_name: str | None = None


class TenantOut(ORMModel):
    id: uuid.UUID
    slug: str
    name: str
    plan: Plan
    quota_weight: int
    is_active: bool
    storage_prefix: str
    created_at: datetime


class WorkspaceCreate(BaseModel):
    slug: str = Field(pattern=SLUG_PATTERN)
    name: str = Field(min_length=1, max_length=200)
    default_locale: str = "fa"
    locales: list[str] = Field(default_factory=lambda: ["fa"])
    timezone: str = "Asia/Tehran"
    calendar: str = "jalali"

    @field_validator("calendar")
    @classmethod
    def _known_calendar(cls, value: str) -> str:
        if value not in {"jalali", "gregorian"}:
            raise ValueError("calendar must be 'jalali' or 'gregorian'")
        return value

    @field_validator("locales")
    @classmethod
    def _non_empty(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("at least one locale is required")
        return value


class WorkspaceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    default_locale: str | None = None
    locales: list[str] | None = None
    timezone: str | None = None
    calendar: str | None = None
    is_active: bool | None = None


class WorkspaceOut(ORMModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    slug: str
    name: str
    default_locale: str
    locales: list[str]
    timezone: str
    calendar: str
    is_active: bool
    created_at: datetime
