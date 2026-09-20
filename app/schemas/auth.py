"""Authentication and identity payloads."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, EmailStr, Field

from app.db.enums import Role
from app.schemas.common import ORMModel


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=512)
    # Optional: a user who belongs to several tenants picks one at login.
    tenant_slug: str | None = None


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    tenant_id: uuid.UUID


class RefreshRequest(BaseModel):
    refresh_token: str


class MembershipOut(ORMModel):
    tenant_id: uuid.UUID
    workspace_id: uuid.UUID | None
    role: Role


class UserOut(ORMModel):
    id: uuid.UUID
    email: EmailStr
    full_name: str | None
    is_active: bool
    locale: str


class TenantSummary(ORMModel):
    id: uuid.UUID
    slug: str
    name: str


class SessionInfo(BaseModel):
    """Everything the panel needs right after login."""

    user: UserOut
    tenant: TenantSummary
    memberships: list[MembershipOut]


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=512)
    full_name: str | None = None
    locale: str = "fa"
    role: Role = Role.EDITOR
