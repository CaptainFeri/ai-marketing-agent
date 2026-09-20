"""Content packages, the two human gates, and their step history."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import Principal, assert_workspace_role, get_db, get_principal
from app.core.errors import InvalidStateError, NotFoundError
from app.db.enums import ApprovalGate, PackageStatus, PipelineStep, Role
from app.db.models import ContentPackage, MediaAsset, Topic
from app.schemas.common import Page
from app.schemas.content import (
    ApprovalOut,
    ApprovalRequest,
    MediaAssetOut,
    MediaAssetSelect,
    PackageCreate,
    PackageDetail,
    PackageOut,
    TopicCreate,
    TopicOut,
)
from app.services import packages as package_service
from app.services import storage

#: Re-running the text line only makes sense before the media stage begins.
RERUNNABLE_STATUSES = frozenset(
    {
        PackageStatus.DRAFTING,
        PackageStatus.TEXT_REVIEW,
        PackageStatus.REJECTED,
        PackageStatus.FAILED,
    }
)

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
    detail = PackageDetail.model_validate(package)
    _attach_media_urls(detail)
    return detail


def _attach_media_urls(detail: PackageDetail) -> None:
    """Fill in a loadable URL for each media asset.

    Not part of the pydantic model itself: generating one means a call out to
    the storage backend (a presigned GET against MinIO), which has no
    business happening inside a validator. An asset whose generation is still
    running or failed has no object at its key yet, so it is left ``None``
    rather than handed a URL that 404s.
    """
    backend = storage.get_backend()
    for asset in detail.media_assets:
        if asset.storage_key and asset.mime_type:
            asset.url = backend.url(asset.storage_key)


@router.patch("/{package_id}/media/{media_asset_id}", response_model=MediaAssetOut)
def select_media_asset(
    package_id: uuid.UUID,
    media_asset_id: uuid.UUID,
    payload: MediaAssetSelect,
    session: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> MediaAssetOut:
    """Mark (or unmark) one of the generated options as chosen.

    This is what gate 2 approval actually means: the gallery the image queue
    produced is only a set of options until an editor picks from it.
    Multiple assets may be selected at once — a package can carry an image
    for one channel and a different one for another.
    """
    package = package_service.get_package(session, package_id)
    assert_workspace_role(principal, package.workspace_id, Role.EDITOR)

    asset = session.get(MediaAsset, media_asset_id)
    if asset is None or asset.package_id != package.id:
        raise NotFoundError("media asset not found on this package")
    if not asset.mime_type:
        raise InvalidStateError("this asset has not finished generating yet")

    asset.is_selected = payload.is_selected
    session.flush()

    out = MediaAssetOut.model_validate(asset)
    out.url = storage.get_backend().url(asset.storage_key) if asset.storage_key else None
    return out


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


@router.post("/{package_id}/steps/{step}/rerun", response_model=PackageOut)
def rerun_step(
    package_id: uuid.UUID,
    step: PipelineStep,
    session: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> PackageOut:
    """Re-run one agent and everything after it.

    The step's context is rebuilt from the stored intermediate outputs, so it
    sees what it saw the first time plus any feedback since — which is what
    makes a targeted re-run cheaper than restarting the package.
    """
    package = package_service.get_package(session, package_id)
    assert_workspace_role(principal, package.workspace_id, Role.EDITOR)

    if package.status not in RERUNNABLE_STATUSES:
        raise InvalidStateError(
            f"a package in {package.status.value} cannot be re-run",
            details={"allowed": sorted(s.value for s in RERUNNABLE_STATUSES)},
        )
    if step not in package_service.TEXT_PIPELINE:
        raise InvalidStateError(
            f"{step.value!r} is not part of the text pipeline",
            details={"steps": [s.value for s in package_service.TEXT_PIPELINE]},
        )

    if package.status is not PackageStatus.DRAFTING:
        package_service.transition(package, PackageStatus.DRAFTING)
    package_service.rewind_to(package, step)
    session.flush()

    # Queued rather than run inline: the agent needs the GPU, and the request
    # should not wait minutes for it.
    from app.worker.tasks.pipeline import advance_text

    advance_text.delay(str(principal.tenant_id), str(package_id))
    return PackageOut.model_validate(package)


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
