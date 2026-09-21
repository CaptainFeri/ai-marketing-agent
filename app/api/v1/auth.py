"""Login, refresh and "who am I"."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status

from app.api.deps import Principal, get_principal
from app.core.config import settings
from app.core.errors import RateLimitedError
from app.core.rate_limit import get_rate_limiter
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
from app.schemas.tenant import SelfServiceSignup
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenPair)
def login(payload: LoginRequest) -> TokenPair:
    pair, _user, _tenant, _memberships = auth_service.login(
        payload.email, payload.password, payload.tenant_slug
    )
    return pair


@router.post("/register", response_model=TokenPair, status_code=status.HTTP_201_CREATED)
def register(payload: SelfServiceSignup, request: Request) -> TokenPair:
    """Self-service signup (handoff section 11, phase 2).

    The one endpoint a brand-new customer reaches with no bearer token —
    every other route is protected by requiring one, so this is the one
    that needs its own abuse guard.
    """
    limiter = get_rate_limiter()
    client_ip = request.client.host if request.client else "unknown"
    if not limiter.check(
        f"register:ip:{client_ip}",
        limit=settings.registration_rate_limit_per_hour,
        window_seconds=3600,
    ):
        raise RateLimitedError("too many signups from this address; try again later")
    if not limiter.check(
        f"register:email:{payload.owner_email.strip().lower()}",
        limit=settings.registration_rate_limit_per_email_per_hour,
        window_seconds=3600,
    ):
        raise RateLimitedError("too many signup attempts for this email; try again later")

    pair, _tenant = auth_service.register(payload)
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
