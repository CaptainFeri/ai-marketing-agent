"""Content package lifecycle — the state machine from handoff section 3.

::

    Planned → Drafting → TextReview → (Drafting | Rejected | MediaGenerating)
            → Selection → Scheduled → Published → Measuring → (Refresh → Drafting)

Two human gates sit in that chain and nothing crosses them automatically:
gate 1 after the text is written, gate 2 after the media is generated.  That
is the main defence against the "bulk low-value content" risk in section 13,
so the transitions are enforced here rather than left to each caller.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import InvalidStateError, NotFoundError
from app.db.enums import (
    ApprovalDecision,
    ApprovalGate,
    PackageStatus,
    PipelineStep,
    VideoMode,
)
from app.db.models import Approval, BrandBrief, ContentPackage, Workspace
from app.schemas.content import ApprovalRequest, PackageCreate

#: Every legal move.  Anything not listed is rejected with a 409.
ALLOWED_TRANSITIONS: dict[PackageStatus, frozenset[PackageStatus]] = {
    PackageStatus.PLANNED: frozenset({PackageStatus.DRAFTING, PackageStatus.FAILED}),
    PackageStatus.DRAFTING: frozenset(
        {PackageStatus.TEXT_REVIEW, PackageStatus.DRAFTING, PackageStatus.FAILED}
    ),
    PackageStatus.TEXT_REVIEW: frozenset(
        {PackageStatus.DRAFTING, PackageStatus.REJECTED, PackageStatus.MEDIA_GENERATING}
    ),
    PackageStatus.MEDIA_GENERATING: frozenset({PackageStatus.SELECTION, PackageStatus.FAILED}),
    PackageStatus.SELECTION: frozenset(
        {PackageStatus.SCHEDULED, PackageStatus.MEDIA_GENERATING, PackageStatus.REJECTED}
    ),
    PackageStatus.SCHEDULED: frozenset({PackageStatus.PUBLISHED, PackageStatus.SELECTION}),
    PackageStatus.PUBLISHED: frozenset({PackageStatus.MEASURING}),
    PackageStatus.MEASURING: frozenset({PackageStatus.REFRESH}),
    # A refreshed article re-enters the line at the writer.
    PackageStatus.REFRESH: frozenset({PackageStatus.DRAFTING}),
    PackageStatus.REJECTED: frozenset({PackageStatus.DRAFTING}),
    PackageStatus.FAILED: frozenset({PackageStatus.DRAFTING}),
}

#: The gate that has to be open before each status can be entered.
GATE_FOR_STATUS = {
    PackageStatus.TEXT_REVIEW: ApprovalGate.TEXT,
    PackageStatus.SELECTION: ApprovalGate.MEDIA,
}

#: Order of the text pipeline (handoff section 3, step 2).
TEXT_PIPELINE: tuple[PipelineStep, ...] = (
    PipelineStep.RESEARCHER,
    PipelineStep.STRATEGIST,
    PipelineStep.WRITER,
    PipelineStep.GEO_OPTIMIZER,
    PipelineStep.SEO_OPTIMIZER,
    PipelineStep.QA,
)


def get_package(session: Session, package_id: uuid.UUID) -> ContentPackage:
    package = session.get(ContentPackage, package_id)
    if package is None:
        # Another tenant's package is invisible under RLS and lands here too.
        raise NotFoundError("content package not found")
    return package


def transition(package: ContentPackage, new_status: PackageStatus) -> ContentPackage:
    """Move a package, refusing any step the workflow does not allow."""
    if new_status == package.status:
        return package
    allowed = ALLOWED_TRANSITIONS.get(package.status, frozenset())
    if new_status not in allowed:
        raise InvalidStateError(
            f"cannot move a package from {package.status.value} to {new_status.value}",
            details={
                "from": package.status.value,
                "to": new_status.value,
                "allowed": sorted(status.value for status in allowed),
            },
        )
    package.status = new_status
    return package


def create_package(
    session: Session, tenant_id: uuid.UUID, payload: PackageCreate
) -> ContentPackage:
    """Start a package from the workspace's active brand brief."""
    workspace = session.get(Workspace, payload.workspace_id)
    if workspace is None:
        raise NotFoundError("workspace not found")
    if payload.locale not in (workspace.locales or [workspace.default_locale]):
        raise InvalidStateError(
            f"workspace {workspace.slug!r} is not configured for locale {payload.locale!r}",
            details={"configured": workspace.locales},
        )

    brief = session.scalars(
        select(BrandBrief).where(
            BrandBrief.workspace_id == workspace.id, BrandBrief.is_active.is_(True)
        )
    ).one_or_none()
    if brief is None:
        raise InvalidStateError(
            "this workspace has no active brand brief; fill in the questionnaire first"
        )

    package = ContentPackage(
        tenant_id=tenant_id,
        workspace_id=workspace.id,
        topic_id=payload.topic_id,
        brand_brief_id=brief.id,
        title=payload.title,
        locale=payload.locale,
        status=PackageStatus.PLANNED,
        video_mode=payload.video_mode or brief.video_mode or VideoMode.VOICE,
    )
    session.add(package)
    session.flush()
    return package


def next_text_step(current: PipelineStep | None) -> PipelineStep | None:
    """The step after ``current``, or ``None`` once QA has passed."""
    if current is None:
        return TEXT_PIPELINE[0]
    if current not in TEXT_PIPELINE:
        return None
    index = TEXT_PIPELINE.index(current)
    return TEXT_PIPELINE[index + 1] if index + 1 < len(TEXT_PIPELINE) else None


def handle_qa_result(
    package: ContentPackage, score: float, threshold: float = 0.7
) -> PipelineStep | None:
    """Decide what a QA verdict means for the package.

    A low score sends the package back to the writer, at most
    ``settings.qa_max_retries`` times; after that a human looks at it rather
    than the loop spending more GPU time on the same draft.
    """
    package.qa_score = score
    if score >= threshold:
        return None
    if package.qa_retry_count >= settings.qa_max_retries:
        # Out of retries: straight to the human gate with the QA notes.
        return None
    package.qa_retry_count += 1
    return PipelineStep.WRITER


def record_approval(
    session: Session,
    package: ContentPackage,
    gate: ApprovalGate,
    payload: ApprovalRequest,
    decided_by: uuid.UUID | None,
) -> Approval:
    """Apply a human decision at one of the two gates."""
    expected_status = {
        ApprovalGate.TEXT: PackageStatus.TEXT_REVIEW,
        ApprovalGate.MEDIA: PackageStatus.SELECTION,
    }[gate]
    if package.status != expected_status:
        raise InvalidStateError(
            f"gate {gate.value} applies to packages in {expected_status.value}, "
            f"this one is in {package.status.value}"
        )

    approval = Approval(
        tenant_id=package.tenant_id,
        package_id=package.id,
        gate=gate,
        decision=payload.decision,
        feedback=payload.feedback,
        return_to_step=payload.return_to_step,
        decided_by_id=decided_by,
        decided_at=datetime.now(UTC),
    )
    session.add(approval)

    if payload.decision is ApprovalDecision.APPROVED:
        transition(
            package,
            PackageStatus.MEDIA_GENERATING
            if gate is ApprovalGate.TEXT
            else PackageStatus.SCHEDULED,
        )
    elif payload.decision is ApprovalDecision.CHANGES_REQUESTED:
        if gate is ApprovalGate.TEXT:
            transition(package, PackageStatus.DRAFTING)
            package.current_step = payload.return_to_step or PipelineStep.WRITER
        else:
            transition(package, PackageStatus.MEDIA_GENERATING)
    else:
        transition(package, PackageStatus.REJECTED)
        package.failure_reason = payload.feedback

    session.flush()
    return approval
