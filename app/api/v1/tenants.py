"""Tenant provisioning and membership management."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.api.deps import Principal, get_principal, require_role
from app.core.errors import PermissionDeniedError
from app.db.enums import Role
from app.db.tenancy import system_session
from app.schemas.auth import UserCreate, UserOut
from app.schemas.tenant import TenantCreate, TenantOut
from app.services import auth as auth_service

router = APIRouter(prefix="/tenants", tags=["tenants"])


@router.post("", response_model=TenantOut, status_code=status.HTTP_201_CREATED)
def create_tenant(
    payload: TenantCreate, principal: Principal = Depends(get_principal)
) -> TenantOut:
    """Provision a new tenant.

    Restricted to platform operators until self-service onboarding lands in
    phase 2 (handoff section 11).
    """
    if not principal.is_superuser:
        raise PermissionDeniedError("only platform operators may create tenants")
    tenant = auth_service.create_tenant_with_owner(payload)
    return TenantOut.model_validate(tenant)


@router.get("/current", response_model=TenantOut)
def current_tenant(principal: Principal = Depends(get_principal)) -> TenantOut:
    with system_session() as session:
        tenant = auth_service.get_tenant(session, principal.tenant_id)
        return TenantOut.model_validate(tenant)


@router.post("/current/members", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def add_member(
    payload: UserCreate, principal: Principal = Depends(require_role(Role.ADMIN))
) -> UserOut:
    """Invite a user into the caller's tenant.

    Only an owner may hand out the owner role.
    """
    if payload.role is Role.OWNER and not (
        principal.is_superuser or (principal.role_for() or Role.VIEWER) is Role.OWNER
    ):
        raise PermissionDeniedError("only an owner may grant the owner role")

    with system_session() as session:
        user, _membership = auth_service.add_member(
            session,
            principal.tenant_id,
            payload.email,
            payload.password,
            payload.role,
            full_name=payload.full_name,
            locale=payload.locale,
        )
        return UserOut.model_validate(user)
