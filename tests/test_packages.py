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
    """The marketizer runs inside the chain, before gate 1 — not after it.

    Handoff section 3 is explicit that what gate 1 approves is "channel
    version + visual_brief + A/B variants", and the image queue reads the
    marketizer's visual_brief, so it has to exist before MEDIA_GENERATING can
    be entered at all.
    """
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
        PipelineStep.MARKETIZER,
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
    # What matters is which step runs next, not what current_step holds:
    # current_step records the step that last ran, so "send it back to the
    # researcher" means the next advance must produce the researcher.
    assert package_service.next_text_step(package.current_step) is PipelineStep.RESEARCHER


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


def test_rewinding_makes_the_named_step_run_next() -> None:
    """current_step is the step that last ran, so a rewind sets its predecessor.

    Assigning the target directly would skip it — the opposite of what
    "send it back to the writer" means.
    """
    package = make_package(PackageStatus.DRAFTING)
    for target in package_service.TEXT_PIPELINE:
        package_service.rewind_to(package, target)
        assert package_service.next_text_step(package.current_step) is target


def test_rewinding_to_the_first_step_clears_the_marker() -> None:
    package = make_package(PackageStatus.DRAFTING)
    package_service.rewind_to(package, PipelineStep.RESEARCHER)
    assert package.current_step is None


def test_rewinding_to_a_step_outside_the_text_line_is_left_alone() -> None:
    """A step this state machine has never heard of is passed through as-is
    rather than raising — ``rewind_to`` only special-cases members of
    ``TEXT_PIPELINE``, and the topic planner and brief assistant are not."""
    package = make_package(PackageStatus.DRAFTING)
    package_service.rewind_to(package, PipelineStep.TOPIC_PLANNER)
    assert package.current_step is PipelineStep.TOPIC_PLANNER


def test_rewinding_to_the_marketizer_reruns_only_the_marketizer() -> None:
    """It is now part of the chain, so it follows the same predecessor rule
    as every other step."""
    package = make_package(PackageStatus.DRAFTING)
    package_service.rewind_to(package, PipelineStep.MARKETIZER)
    assert package_service.next_text_step(package.current_step) is PipelineStep.MARKETIZER


# --------------------------------------------------------------------------
# how enums are stored
# --------------------------------------------------------------------------
def test_enum_columns_store_values_not_member_names() -> None:
    """``WHERE status = 'drafting'`` must find rows.

    SQLAlchemy stores the member name by default, so the database would hold
    ``DRAFTING`` while the API, the JSONB payloads and every hand-written
    query use ``drafting``. ``app.db.base.enum_column`` is what prevents that;
    this test is what stops someone reverting to a bare ``Enum(...)``.
    """
    import sqlalchemy as sa

    from app.db.models import Base

    checked = 0
    for table in Base.metadata.sorted_tables:
        for column in table.columns:
            if not isinstance(column.type, sa.Enum) or column.type.enum_class is None:
                continue
            checked += 1
            stored = set(column.type.enums)
            values = {member.value for member in column.type.enum_class}
            assert stored == values, f"{table.name}.{column.name} stores member names"
    assert checked > 20, "the walk found almost no enum columns; has the model moved?"


@requires_db
def test_a_status_round_trips_through_the_database(tenant_factory, system_db) -> None:
    from sqlalchemy import text

    tenant, workspace = tenant_factory("acme")
    package = ContentPackage(
        tenant_id=tenant.id,
        workspace_id=workspace.id,
        title="x",
        locale="fa",
        status=PackageStatus.TEXT_REVIEW,
    )
    system_db.add(package)
    system_db.commit()

    raw = system_db.execute(
        text("SELECT status FROM content_package WHERE id = :id"), {"id": package.id}
    ).scalar()
    assert raw == "text_review"

    system_db.expire(package)
    assert package.status is PackageStatus.TEXT_REVIEW
