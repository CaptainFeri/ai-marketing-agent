"""Authentication, membership resolution and tenant provisioning.

Login is the one place that legitimately has to look across tenants: a user
types an email, and only afterwards is it known which tenant they belong to.
That lookup happens over :func:`~app.db.tenancy.system_session`, deliberately
and in one place.  Every other read in the application is tenant-scoped.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AuthenticationError, ConflictError, NotFoundError
from app.core.security import create_token, decode_token, hash_password, verify_password
from app.db.enums import Role
from app.db.models import Membership, Tenant, User, Workspace
from app.db.tenancy import system_session
from app.schemas.auth import TokenPair
from app.schemas.tenant import TenantCreate


# --------------------------------------------------------------------------
# users
# --------------------------------------------------------------------------
def get_user_by_email(session: Session, email: str) -> User | None:
    return session.scalars(
        select(User).where(func.lower(User.email) == email.strip().lower())
    ).one_or_none()


def authenticate(session: Session, email: str, password: str) -> User:
    user = get_user_by_email(session, email)
    # The same error either way: a different message would tell an attacker
    # which addresses exist.
    if user is None or not verify_password(password, user.hashed_password):
        raise AuthenticationError("incorrect email or password")
    if not user.is_active:
        raise AuthenticationError("this account is disabled")
    return user


def memberships_for(session: Session, user_id: uuid.UUID) -> list[Membership]:
    return list(session.scalars(select(Membership).where(Membership.user_id == user_id)).all())


def effective_role(
    memberships: list[Membership],
    tenant_id: uuid.UUID,
    workspace_id: uuid.UUID | None = None,
) -> Role | None:
    """Highest role the user holds for this tenant, or for one workspace of it.

    A tenant-wide membership (``workspace_id IS NULL``) counts everywhere; a
    workspace membership counts only in that workspace.
    """
    best: Role | None = None
    for membership in memberships:
        if membership.tenant_id != tenant_id:
            continue
        applies = membership.workspace_id is None or membership.workspace_id == workspace_id
        if not applies:
            continue
        if best is None or membership.role.rank > best.rank:
            best = membership.role
    return best


# --------------------------------------------------------------------------
# login
# --------------------------------------------------------------------------
def _pick_tenant(
    session: Session, memberships: list[Membership], tenant_slug: str | None
) -> Tenant:
    tenant_ids = {m.tenant_id for m in memberships}
    if not tenant_ids:
        raise AuthenticationError("this account has no workspace access")

    tenants = list(
        session.scalars(
            select(Tenant).where(Tenant.id.in_(tenant_ids), Tenant.is_active.is_(True))
        ).all()
    )
    if not tenants:
        raise AuthenticationError("every tenant for this account is disabled")

    if tenant_slug is not None:
        for tenant in tenants:
            if tenant.slug == tenant_slug:
                return tenant
        raise AuthenticationError(f"no access to tenant {tenant_slug!r}")

    if len(tenants) > 1:
        # Deterministic rather than arbitrary: oldest membership wins, and the
        # panel can still switch explicitly with ``tenant_slug``.
        oldest = min(memberships, key=lambda m: m.created_at)
        for tenant in tenants:
            if tenant.id == oldest.tenant_id:
                return tenant
    return tenants[0]


def login(
    email: str, password: str, tenant_slug: str | None = None
) -> tuple[TokenPair, User, Tenant, list[Membership]]:
    """Verify credentials and mint a token pair bound to one tenant."""
    with system_session() as session:
        user = authenticate(session, email, password)
        memberships = memberships_for(session, user.id)
        tenant = _pick_tenant(session, memberships, tenant_slug)
        user.last_login_at = datetime.now(UTC)
        session.flush()
        session.expunge_all()

    pair = TokenPair(
        access_token=create_token(user.id, "access", tenant_id=tenant.id),
        refresh_token=create_token(user.id, "refresh", tenant_id=tenant.id),
        expires_in=settings.access_token_expire_minutes * 60,
        tenant_id=tenant.id,
    )
    return pair, user, tenant, memberships


def refresh(refresh_token: str) -> TokenPair:
    """Exchange a refresh token for a new pair.

    The tenant binding is carried over from the old token, and membership is
    re-checked: access revoked since the token was issued must not survive.
    """
    import jwt

    try:
        payload = decode_token(refresh_token, expected_type="refresh")
    except jwt.PyJWTError as exc:
        raise AuthenticationError(f"invalid refresh token: {exc}") from exc

    user_id = uuid.UUID(payload["sub"])
    tenant_id = uuid.UUID(payload["tid"])

    with system_session() as session:
        user = session.get(User, user_id)
        if user is None or not user.is_active:
            raise AuthenticationError("this account is disabled")
        memberships = memberships_for(session, user_id)
        if effective_role(memberships, tenant_id) is None:
            raise AuthenticationError("access to this tenant has been revoked")

    return TokenPair(
        access_token=create_token(user_id, "access", tenant_id=tenant_id),
        refresh_token=create_token(user_id, "refresh", tenant_id=tenant_id),
        expires_in=settings.access_token_expire_minutes * 60,
        tenant_id=tenant_id,
    )


# --------------------------------------------------------------------------
# provisioning
# --------------------------------------------------------------------------
def create_tenant_with_owner(payload: TenantCreate) -> Tenant:
    """Provision a tenant, its first owner and a default workspace.

    Self-service onboarding in phase 2 calls this; until then it is the
    operator's way of adding a pilot customer.
    """
    with system_session() as session:
        if session.scalars(select(Tenant).where(Tenant.slug == payload.slug)).one_or_none():
            raise ConflictError(f"tenant slug {payload.slug!r} is already taken")

        tenant = Tenant(
            slug=payload.slug,
            name=payload.name,
            plan=payload.plan,
            quota_weight=payload.quota_weight,
            storage_prefix=payload.slug,
            is_active=True,
            settings_json={},
        )
        session.add(tenant)
        session.flush()

        user = get_user_by_email(session, payload.owner_email)
        if user is None:
            user = User(
                email=payload.owner_email.strip().lower(),
                hashed_password=hash_password(payload.owner_password),
                full_name=payload.owner_full_name,
                is_active=True,
            )
            session.add(user)
            session.flush()

        workspace = Workspace(
            tenant_id=tenant.id,
            slug="default",
            name=payload.name,
            default_locale=settings.default_locale,
            locales=[settings.default_locale],
        )
        session.add(workspace)
        # Tenant-wide owner: workspace_id stays NULL.
        session.add(
            Membership(tenant_id=tenant.id, user_id=user.id, workspace_id=None, role=Role.OWNER)
        )
        session.flush()
        session.refresh(tenant)
        session.expunge_all()
        return tenant


def add_member(
    session: Session,
    tenant_id: uuid.UUID,
    email: str,
    password: str,
    role: Role,
    *,
    full_name: str | None = None,
    locale: str = "fa",
    workspace_id: uuid.UUID | None = None,
) -> tuple[User, Membership]:
    """Add a user to a tenant, creating the account if it does not exist yet.

    ``session`` must be a system session: ``app_user`` is global, and an
    existing account may belong to another tenant already.
    """
    user = get_user_by_email(session, email)
    if user is None:
        user = User(
            email=email.strip().lower(),
            hashed_password=hash_password(password),
            full_name=full_name,
            locale=locale,
            is_active=True,
        )
        session.add(user)
        session.flush()

    existing = session.scalars(
        select(Membership).where(
            Membership.tenant_id == tenant_id,
            Membership.user_id == user.id,
            Membership.workspace_id.is_(workspace_id)
            if workspace_id is None
            else Membership.workspace_id == workspace_id,
        )
    ).one_or_none()
    if existing is not None:
        raise ConflictError("this user already has a membership with that scope")

    membership = Membership(
        tenant_id=tenant_id, user_id=user.id, workspace_id=workspace_id, role=role
    )
    session.add(membership)
    session.flush()
    return user, membership


def get_tenant(session: Session, tenant_id: uuid.UUID) -> Tenant:
    tenant = session.get(Tenant, tenant_id)
    if tenant is None:
        raise NotFoundError("tenant not found")
    return tenant
