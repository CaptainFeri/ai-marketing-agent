"""Content packages, the two human gates, and their step history."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import Principal, assert_workspace_role, get_db, get_principal
from app.core.errors import NotFoundError
from app.db.enums import ApprovalGate, PackageStatus, Role
from app.db.models import ContentPackage, Topic
from app.schemas.common import Page
from app.schemas.content import (
    ApprovalOut,
    ApprovalRequest,
    PackageCreate,
    PackageDetail,
    PackageOut,
    TopicCreate,
    TopicOut,
)
from app.services import packages as package_service

router = APIRouter(prefix="/packages", tags=["content"])
topics_router = APIRouter(prefix="/topics", tags=["content"])


@router.get("", response_model=Page[PackageOut])
def list_packages(
    workspace_id: uuid.UUID | None = Query(default=None),
    package_status: PackageStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db),
    _: Principal = Depends(get_principal),
) -> Page[PackageOut]:
    conditions = []
    if workspace_id is not None:
        conditions.append(ContentPackage.workspace_id == workspace_id)
    if package_status is not None:
        conditions.append(ContentPackage.status == package_status)

    total = session.scalar(select(func.count()).select_from(ContentPackage).where(*conditions)) or 0
    rows = session.scalars(
        select(ContentPackage)
        .where(*conditions)
        .order_by(ContentPackage.created_at.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return Page(
        items=[PackageOut.model_validate(row) for row in rows],
        total=int(total),
        limit=limit,
        offset=offset,
    )


@router.post("", response_model=PackageOut, status_code=status.HTTP_201_CREATED)
def create_package(
    payload: PackageCreate,
    session: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> PackageOut:
    assert_workspace_role(principal, payload.workspace_id, Role.EDITOR)
    package = package_service.create_package(session, principal.tenant_id, payload)
    return PackageOut.model_validate(package)


@router.get("/{package_id}", response_model=PackageDetail)
def get_package(
    package_id: uuid.UUID,
    session: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> PackageDetail:
    package = session.scalars(
        select(ContentPackage)
        .where(ContentPackage.id == package_id)
        .options(
            selectinload(ContentPackage.step_runs),
            selectinload(ContentPackage.variants),
            selectinload(ContentPackage.media_assets),
        )
    ).one_or_none()
    if package is None:
        raise NotFoundError("content package not found")
    assert_workspace_role(principal, package.workspace_id, Role.VIEWER)
    return PackageDetail.model_validate(package)


@router.post("/{package_id}/gates/{gate}", response_model=ApprovalOut)
def decide_gate(
    package_id: uuid.UUID,
    gate: ApprovalGate,
    payload: ApprovalRequest,
    session: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> ApprovalOut:
    """Record a human decision at gate 1 (text) or gate 2 (media selection).

    Nothing moves past either gate without a row here — that is what keeps a
    person in the loop (handoff section 3).
    """
    package = package_service.get_package(session, package_id)
    assert_workspace_role(principal, package.workspace_id, Role.EDITOR)
    approval = package_service.record_approval(session, package, gate, payload, principal.user_id)
    return ApprovalOut.model_validate(approval)


# --------------------------------------------------------------------------
# topics
# --------------------------------------------------------------------------
@topics_router.get("", response_model=list[TopicOut])
def list_topics(
    workspace_id: uuid.UUID = Query(...),
    session: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> list[TopicOut]:
    assert_workspace_role(principal, workspace_id, Role.VIEWER)
    rows = session.scalars(
        select(Topic)
        .where(Topic.workspace_id == workspace_id)
        .order_by(Topic.priority, Topic.created_at)
    ).all()
    return [TopicOut.model_validate(row) for row in rows]


@topics_router.post("", response_model=TopicOut, status_code=status.HTTP_201_CREATED)
def create_topic(
    payload: TopicCreate,
    workspace_id: uuid.UUID = Query(...),
    session: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> TopicOut:
    assert_workspace_role(principal, workspace_id, Role.EDITOR)
    topic = Topic(
        tenant_id=principal.tenant_id,
        workspace_id=workspace_id,
        **payload.model_dump(),
    )
    session.add(topic)
    session.flush()
    return TopicOut.model_validate(topic)
