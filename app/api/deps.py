"""FastAPI dependencies.

The important one is :func:`get_db`: it opens a session already bound to the
caller's tenant, so a handler physically cannot read another customer's rows
even if it forgets a ``WHERE tenant_id = ...`` clause.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.errors import AuthenticationError, PermissionDeniedError
from app.core.security import decode_token
from app.db.enums import Role
from app.db.models import Membership, User
from app.db.session import SessionLocal
from app.db.tenancy import bind_tenant, system_session

bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    """The authenticated caller, already resolved to one tenant."""

    user_id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    is_superuser: bool
    memberships: tuple[Membership, ...]

    def role_for(self, workspace_id: uuid.UUID | None = None) -> Role | None:
        from app.services.auth import effective_role

        return effective_role(list(self.memberships), self.tenant_id, workspace_id)


def get_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> Principal:
    if credentials is None:
        raise AuthenticationError("missing bearer token")
    try:
        payload = decode_token(credentials.credentials, expected_type="access")
    except jwt.PyJWTError as exc:
        raise AuthenticationError(f"invalid token: {exc}") from exc

    try:
        user_id = uuid.UUID(payload["sub"])
        tenant_id = uuid.UUID(payload["tid"])
    except (KeyError, ValueError) as exc:
        raise AuthenticationError("token is missing a user or tenant claim") from exc

    # Memberships are re-read on every request rather than trusted from the
    # token, so revoking access takes effect immediately.
    with system_session() as session:
        user = session.get(User, user_id)
        if user is None or not user.is_active:
            raise AuthenticationError("this account is disabled")
        from app.services.auth import effective_role, memberships_for

        memberships = memberships_for(session, user_id)
        if effective_role(memberships, tenant_id) is None and not user.is_superuser:
            raise PermissionDeniedError("no access to this tenant")
        session.expunge_all()
        return Principal(
            user_id=user_id,
            tenant_id=tenant_id,
            email=user.email,
            is_superuser=user.is_superuser,
            memberships=tuple(memberships),
        )


def get_db(principal: Principal = Depends(get_principal)) -> Iterator[Session]:
    """A session scoped to the caller's tenant for the whole request."""
    session = SessionLocal()
    try:
        bind_tenant(session, principal.tenant_id)
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def require_role(minimum: Role):
    """Dependency factory: reject callers below ``minimum`` for this tenant.

    Workspace-scoped checks additionally pass the workspace id; see
    :func:`assert_workspace_role`.
    """

    def _check(principal: Principal = Depends(get_principal)) -> Principal:
        if principal.is_superuser:
            return principal
        role = principal.role_for()
        if role is None or not role.satisfies(minimum):
            raise PermissionDeniedError(f"this action needs the {minimum.value} role or higher")
        return principal

    return _check


def assert_workspace_role(principal: Principal, workspace_id: uuid.UUID, minimum: Role) -> None:
    if principal.is_superuser:
        return
    role = principal.role_for(workspace_id)
    if role is None or not role.satisfies(minimum):
        raise PermissionDeniedError(f"this action needs the {minimum.value} role on this workspace")


def client_locale(request: Request) -> str:
    """Best-effort UI language from ``Accept-Language``; fa, en or ar."""
    header = request.headers.get("accept-language", "")
    for part in header.split(","):
        code = part.split(";")[0].strip().lower()[:2]
        if code in {"fa", "en", "ar"}:
            return code
    return "fa"
