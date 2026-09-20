"""The content package state machine and the two human gates (section 3)."""

from __future__ import annotations

import uuid

import pytest

from app.core.errors import InvalidStateError
from app.db.enums import (
    ApprovalDecision,
    ApprovalGate,
    PackageStatus,
    PipelineStep,
)
from app.db.models import ContentPackage
from app.schemas.content import ApprovalRequest
from app.services import packages as package_service
from tests.conftest import requires_db


def make_package(status: PackageStatus = PackageStatus.PLANNED) -> ContentPackage:
    return ContentPackage(
        tenant_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        title="مقاله",
        locale="fa",
        status=status,
        qa_retry_count=0,
    )


# --------------------------------------------------------------------------
# transitions (no database needed)
# --------------------------------------------------------------------------
def test_the_happy_path_is_walkable() -> None:
    package = make_package()
    for step in (
        PackageStatus.DRAFTING,
        PackageStatus.TEXT_REVIEW,
        PackageStatus.MEDIA_GENERATING,
        PackageStatus.SELECTION,
        PackageStatus.SCHEDULED,
        PackageStatus.PUBLISHED,
        PackageStatus.MEASURING,
        PackageStatus.REFRESH,
        PackageStatus.DRAFTING,
    ):
        package_service.transition(package, step)
    assert package.status is PackageStatus.DRAFTING


def test_skipping_a_gate_is_refused() -> None:
    """Drafting straight to media generation would bypass gate 1 entirely."""
    package = make_package(PackageStatus.DRAFTING)
    with pytest.raises(InvalidStateError) as excinfo:
        package_service.transition(package, PackageStatus.MEDIA_GENERATING)
    assert "media_generating" in excinfo.value.details["to"]


def test_publishing_without_scheduling_is_refused() -> None:
    package = make_package(PackageStatus.SELECTION)
    with pytest.raises(InvalidStateError):
        package_service.transition(package, PackageStatus.PUBLISHED)


def test_transitioning_to_the_same_status_is_a_no_op() -> None:
    package = make_package(PackageStatus.DRAFTING)
    package_service.transition(package, PackageStatus.DRAFTING)
    assert package.status is PackageStatus.DRAFTING


def test_a_rejected_package_can_be_restarted() -> None:
    package = make_package(PackageStatus.REJECTED)
    package_service.transition(package, PackageStatus.DRAFTING)
    assert package.status is PackageStatus.DRAFTING


# --------------------------------------------------------------------------
# the text pipeline order
# --------------------------------------------------------------------------
def test_the_pipeline_runs_in_the_documented_order() -> None:
    order = []
    step = package_service.next_text_step(None)
    while step is not None:
        order.append(step)
        step = package_service.next_text_step(step)
    assert order == [
        PipelineStep.RESEARCHER,
        PipelineStep.STRATEGIST,
        PipelineStep.WRITER,
        PipelineStep.GEO_OPTIMIZER,
        PipelineStep.SEO_OPTIMIZER,
        PipelineStep.QA,
    ]


def test_qa_passes_a_good_draft_through() -> None:
    package = make_package(PackageStatus.DRAFTING)
    assert package_service.handle_qa_result(package, 0.85) is None
    assert package.qa_score == 0.85
    assert package.qa_retry_count == 0


def test_qa_sends_a_weak_draft_back_to_the_writer() -> None:
    package = make_package(PackageStatus.DRAFTING)
    assert package_service.handle_qa_result(package, 0.4) is PipelineStep.WRITER
    assert package.qa_retry_count == 1


def test_the_qa_loop_gives_up_after_two_retries() -> None:
    """Section 3: after two returns a human looks at it, not the writer again."""
    package = make_package(PackageStatus.DRAFTING)
    assert package_service.handle_qa_result(package, 0.4) is PipelineStep.WRITER
    assert package_service.handle_qa_result(package, 0.4) is PipelineStep.WRITER
    assert package_service.handle_qa_result(package, 0.4) is None
    assert package.qa_retry_count == 2


# --------------------------------------------------------------------------
# gates (database-backed: an Approval row is written)
# --------------------------------------------------------------------------
@requires_db
def test_approving_gate_one_starts_media(tenant_factory, system_db) -> None:
    tenant, workspace = tenant_factory("acme")
    package = ContentPackage(
        tenant_id=tenant.id,
        workspace_id=workspace.id,
        title="x",
        locale="fa",
        status=PackageStatus.TEXT_REVIEW,
    )
    system_db.add(package)
    system_db.flush()

    approval = package_service.record_approval(
        system_db,
        package,
        ApprovalGate.TEXT,
        ApprovalRequest(decision=ApprovalDecision.APPROVED),
        decided_by=None,
    )
    assert package.status is PackageStatus.MEDIA_GENERATING
    assert approval.decided_at is not None


@requires_db
def test_requesting_changes_returns_to_the_named_step(tenant_factory, system_db) -> None:
    tenant, workspace = tenant_factory("acme")
    package = ContentPackage(
        tenant_id=tenant.id,
        workspace_id=workspace.id,
        title="x",
        locale="fa",
        status=PackageStatus.TEXT_REVIEW,
    )
    system_db.add(package)
    system_db.flush()

    package_service.record_approval(
        system_db,
        package,
        ApprovalGate.TEXT,
        ApprovalRequest(
            decision=ApprovalDecision.CHANGES_REQUESTED,
            feedback="آمار بدون منبع",
            return_to_step=PipelineStep.RESEARCHER,
        ),
        decided_by=None,
    )
    assert package.status is PackageStatus.DRAFTING
    assert package.current_step is PipelineStep.RESEARCHER


@requires_db
def test_rejecting_records_the_reason(tenant_factory, system_db) -> None:
    tenant, workspace = tenant_factory("acme")
    package = ContentPackage(
        tenant_id=tenant.id,
        workspace_id=workspace.id,
        title="x",
        locale="fa",
        status=PackageStatus.SELECTION,
    )
    system_db.add(package)
    system_db.flush()

    package_service.record_approval(
        system_db,
        package,
        ApprovalGate.MEDIA,
        ApprovalRequest(decision=ApprovalDecision.REJECTED, feedback="تصویرها مناسب نیستند"),
        decided_by=None,
    )
    assert package.status is PackageStatus.REJECTED
    assert package.failure_reason == "تصویرها مناسب نیستند"


@requires_db
def test_a_gate_applies_only_at_its_own_stage(tenant_factory, system_db) -> None:
    tenant, workspace = tenant_factory("acme")
    package = ContentPackage(
        tenant_id=tenant.id,
        workspace_id=workspace.id,
        title="x",
        locale="fa",
        status=PackageStatus.TEXT_REVIEW,
    )
    system_db.add(package)
    system_db.flush()

    with pytest.raises(InvalidStateError):
        package_service.record_approval(
            system_db,
            package,
            ApprovalGate.MEDIA,
            ApprovalRequest(decision=ApprovalDecision.APPROVED),
            decided_by=None,
        )
