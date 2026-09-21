"""Workspaces (brands) and their versioned brand briefs."""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.api.deps import Principal, assert_workspace_role, get_db, get_principal, require_role
from app.core.errors import ConflictError, InvalidStateError, NotFoundError
from app.db.enums import Channel, Role
from app.db.models import BrandBrief, Workspace
from app.schemas.brief import BrandBriefCreate, BrandBriefOut
from app.schemas.calendar import CalendarOut, PublishTimeSuggestionOut
from app.schemas.tenant import WorkspaceCreate, WorkspaceOut, WorkspaceUpdate
from app.services import calendar as calendar_service
from app.services import publish_time as publish_time_service

#: A calendar view wide enough for a real planning horizon, narrow enough
#: that a mistyped range can never turn into an unbounded table scan.
MAX_CALENDAR_RANGE_DAYS = 366

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


def _get_workspace(session: Session, workspace_id: uuid.UUID) -> Workspace:
    workspace = session.get(Workspace, workspace_id)
    if workspace is None:
        # Row level security also turns "another tenant's workspace" into
        # this same 404, which is the answer we want to give either way.
        raise NotFoundError("workspace not found")
    return workspace


@router.get("", response_model=list[WorkspaceOut])
def list_workspaces(
    session: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> list[WorkspaceOut]:
    rows = session.scalars(select(Workspace).order_by(Workspace.created_at)).all()
    visible = [
        row for row in rows if principal.is_superuser or principal.role_for(row.id) is not None
    ]
    return [WorkspaceOut.model_validate(row) for row in visible]


@router.post("", response_model=WorkspaceOut, status_code=status.HTTP_201_CREATED)
def create_workspace(
    payload: WorkspaceCreate,
    session: Session = Depends(get_db),
    principal: Principal = Depends(require_role(Role.ADMIN)),
) -> WorkspaceOut:
    exists = session.scalars(select(Workspace).where(Workspace.slug == payload.slug)).one_or_none()
    if exists is not None:
        raise ConflictError(f"workspace slug {payload.slug!r} is already used")

    workspace = Workspace(tenant_id=principal.tenant_id, **payload.model_dump())
    session.add(workspace)
    session.flush()
    return WorkspaceOut.model_validate(workspace)


@router.get("/{workspace_id}", response_model=WorkspaceOut)
def get_workspace(
    workspace_id: uuid.UUID,
    session: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> WorkspaceOut:
    workspace = _get_workspace(session, workspace_id)
    assert_workspace_role(principal, workspace_id, Role.VIEWER)
    return WorkspaceOut.model_validate(workspace)


@router.patch("/{workspace_id}", response_model=WorkspaceOut)
def update_workspace(
    workspace_id: uuid.UUID,
    payload: WorkspaceUpdate,
    session: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> WorkspaceOut:
    workspace = _get_workspace(session, workspace_id)
    assert_workspace_role(principal, workspace_id, Role.ADMIN)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(workspace, field, value)
    session.flush()
    return WorkspaceOut.model_validate(workspace)


@router.get("/{workspace_id}/calendar", response_model=CalendarOut)
def get_calendar(
    workspace_id: uuid.UUID,
    start: date = Query(...),
    end: date = Query(...),
    session: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> CalendarOut:
    """Every scheduled/published post plus known holidays in one range
    (handoff section 11) — what backs the panel's drag-and-drop calendar."""
    workspace = _get_workspace(session, workspace_id)
    assert_workspace_role(principal, workspace_id, Role.VIEWER)
    if end < start:
        raise InvalidStateError("end must not be before start")
    if (end - start) > timedelta(days=MAX_CALENDAR_RANGE_DAYS):
        raise InvalidStateError(f"range cannot exceed {MAX_CALENDAR_RANGE_DAYS} days")
    return calendar_service.workspace_calendar(session, workspace, start, end)


@router.get("/{workspace_id}/publish-time-suggestion", response_model=PublishTimeSuggestionOut)
def get_publish_time_suggestion(
    workspace_id: uuid.UUID,
    channel: Channel = Query(...),
    session: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> PublishTimeSuggestionOut:
    """The next reasonable time to schedule this channel (handoff section
    11: "پیشنهاد زمان انتشار") — rule-of-thumb until the workspace has
    enough of its own engagement history on this channel, then based on
    that. See ``docs/publish-time.md``."""
    workspace = _get_workspace(session, workspace_id)
    assert_workspace_role(principal, workspace_id, Role.VIEWER)
    suggestion = publish_time_service.suggest_publish_time(session, workspace, channel)
    return PublishTimeSuggestionOut(
        scheduled_at=suggestion.scheduled_at,
        basis=suggestion.basis,
        hour_of_day=suggestion.hour_of_day,
        sample_size=suggestion.sample_size,
    )


# --------------------------------------------------------------------------
# brand brief
# --------------------------------------------------------------------------
@router.post(
    "/{workspace_id}/briefs",
    response_model=BrandBriefOut,
    status_code=status.HTTP_201_CREATED,
)
def create_brief(
    workspace_id: uuid.UUID,
    payload: BrandBriefCreate,
    session: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> BrandBriefOut:
    """Store a new brief version.

    Briefs are never edited in place — the agents that produced a package must
    stay traceable to the exact brief they read.
    """
    _get_workspace(session, workspace_id)
    assert_workspace_role(principal, workspace_id, Role.EDITOR)

    highest = session.scalar(
        select(func.max(BrandBrief.version)).where(BrandBrief.workspace_id == workspace_id)
    )
    if payload.activate:
        session.execute(
            update(BrandBrief)
            .where(BrandBrief.workspace_id == workspace_id)
            .values(is_active=False)
        )

    brief = BrandBrief(
        tenant_id=principal.tenant_id,
        workspace_id=workspace_id,
        version=(highest or 0) + 1,
        is_active=payload.activate,
        data=payload.data.model_dump(mode="json"),
        video_mode=payload.data.video_mode,
        created_by_id=principal.user_id,
    )
    session.add(brief)
    session.flush()
    return BrandBriefOut.model_validate(brief)


@router.get("/{workspace_id}/briefs", response_model=list[BrandBriefOut])
def list_briefs(
    workspace_id: uuid.UUID,
    session: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> list[BrandBriefOut]:
    _get_workspace(session, workspace_id)
    assert_workspace_role(principal, workspace_id, Role.VIEWER)
    rows = session.scalars(
        select(BrandBrief)
        .where(BrandBrief.workspace_id == workspace_id)
        .order_by(BrandBrief.version.desc())
    ).all()
    return [BrandBriefOut.model_validate(row) for row in rows]


@router.get("/{workspace_id}/briefs/active", response_model=BrandBriefOut)
def active_brief(
    workspace_id: uuid.UUID,
    session: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> BrandBriefOut:
    _get_workspace(session, workspace_id)
    assert_workspace_role(principal, workspace_id, Role.VIEWER)
    brief = session.scalars(
        select(BrandBrief).where(
            BrandBrief.workspace_id == workspace_id, BrandBrief.is_active.is_(True)
        )
    ).one_or_none()
    if brief is None:
        raise NotFoundError("this workspace has no active brand brief yet")
    return BrandBriefOut.model_validate(brief)
