"""Login, refresh and "who am I"."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import Principal, get_principal
from app.db.tenancy import system_session
from app.schemas.auth import (
    LoginRequest,
    MembershipOut,
    RefreshRequest,
    SessionInfo,
    TenantSummary,
    TokenPair,
    UserOut,
)
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenPair)
def login(payload: LoginRequest) -> TokenPair:
    pair, _user, _tenant, _memberships = auth_service.login(
        payload.email, payload.password, payload.tenant_slug
    )
    return pair


@router.post("/refresh", response_model=TokenPair)
def refresh(payload: RefreshRequest) -> TokenPair:
    return auth_service.refresh(payload.refresh_token)


@router.get("/me", response_model=SessionInfo)
def me(principal: Principal = Depends(get_principal)) -> SessionInfo:
    with system_session() as session:
        from app.db.models import User

        user = session.get(User, principal.user_id)
        tenant = auth_service.get_tenant(session, principal.tenant_id)
        return SessionInfo(
            user=UserOut.model_validate(user),
            tenant=TenantSummary.model_validate(tenant),
            memberships=[MembershipOut.model_validate(m) for m in principal.memberships],
        )
