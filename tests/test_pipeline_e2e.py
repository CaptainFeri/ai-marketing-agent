"""A package walking the whole text line, through the real queue.

The LLM is simulated; the queue, the leases, the StepRun rows, the QA loop,
the article assembly and the gates are all the production code path.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.agents.llm import SimulatedLlmClient, set_client
from app.agents.simulation import sample_for_schema_name
from app.db.enums import (
    ApprovalDecision,
    ApprovalGate,
    PackageStatus,
    PipelineStep,
    StepStatus,
    VideoMode,
)
from app.db.models import BrandBrief, ContentPackage, StepRun, Variant
from app.schemas.content import ApprovalRequest
from app.services import packages as package_service
from app.services import quota
from app.worker.gpu_runtime import SimulatedGpuRuntime, set_runtime
from app.worker.tasks.gpu import dispatch
from app.worker.tasks.pipeline import advance_text
from tests.conftest import requires_db

pytestmark = requires_db

TEXT_STEPS = [step.value for step in package_service.TEXT_PIPELINE]


@pytest.fixture(autouse=True)
def simulated_stack():
    set_runtime(SimulatedGpuRuntime(speedup=1_000_000, seed=11))
    set_client(SimulatedLlmClient(sample_factory=sample_for_schema_name))
    yield
    set_runtime(None)
    set_client(None)


@pytest.fixture
def package(tenant_factory, system_db):
    tenant, workspace = tenant_factory("acme")
    quota.allocate_day(system_db, datetime.now(UTC).date(), capacity_seconds=20 * 3600)
    brief = BrandBrief(
        tenant_id=tenant.id,
        workspace_id=workspace.id,
        version=1,
        is_active=True,
        data={
            "brand": "آکمه",
            "description": "ابزار صنعتی",
            "voice": {"tone": ["حرفه‌ای"]},
            "channels": ["wordpress", "telegram"],
            "evidence_policy": "every statistic must carry a source URL",
        },
        video_mode=VideoMode.VOICE,
    )
    system_db.add(brief)
    row = ContentPackage(
        tenant_id=tenant.id,
        workspace_id=workspace.id,
        brand_brief_id=brief.id,
        title="راهنمای خرید دریل برقی",
        locale="fa",
        status=PackageStatus.PLANNED,
        video_mode=VideoMode.VOICE,
    )
    system_db.add(row)
    system_db.flush()
    system_db.commit()
    return row


def drive_to_gate_one(package, limit: int = 12) -> list[str]:
    """Alternate advancing the pipeline and draining the GPU, as beat does."""
    ran: list[str] = []
    for _ in range(limit):
        result = advance_text(str(package.tenant_id), str(package.id))
        if result.get("status") == PackageStatus.TEXT_REVIEW.value:
            return ran
        if result.get("deferred"):
            return ran
        ran.append(result["step"])
        dispatch()
    raise AssertionError(f"did not reach gate 1; ran {ran}")


# ---------------------------------------------------------------------------
def test_a_package_walks_the_six_agents_to_gate_one(package, system_db) -> None:
    ran = drive_to_gate_one(package)

    assert ran == TEXT_STEPS
    system_db.refresh(package)
    assert package.status is PackageStatus.TEXT_REVIEW


def test_each_step_stores_its_output_for_a_later_re_run(package, system_db) -> None:
    """Handoff phase 1, week 3: intermediate outputs are kept."""
    drive_to_gate_one(package)

    runs = system_db.scalars(
        select(StepRun).where(StepRun.package_id == package.id).order_by(StepRun.created_at)
    ).all()
    assert [run.step.value for run in runs] == TEXT_STEPS
    assert all(run.status is StepStatus.SUCCEEDED for run in runs)
    assert all(run.output_json for run in runs)
    # And what went in, so a failure is diagnosable without re-running it.
    assert all(run.input_json and "prompt_chars" in run.input_json for run in runs)
    assert all(run.model for run in runs)


def test_token_counts_and_gpu_time_are_recorded(package, system_db) -> None:
    drive_to_gate_one(package)
    system_db.refresh(package)

    runs = system_db.scalars(select(StepRun).where(StepRun.package_id == package.id)).all()
    assert all(run.prompt_tokens for run in runs)
    assert all(run.completion_tokens for run in runs)
    # Section 7: the measured time is what the quota mechanism runs on.
    assert package.gpu_seconds > 0
    assert quota.quota_status(system_db, package.tenant_id).consumed_seconds > 0


def test_the_article_is_assembled_from_the_text_stages(package, system_db) -> None:
    drive_to_gate_one(package)
    system_db.refresh(package)

    article = package.article
    assert article is not None
    # The writer's contribution...
    assert article["title"]
    assert article["sections"]
    # ...the GEO stage's...
    assert article["answer_blocks"]
    assert article["schema_org"]
    # ...and the SEO stage's.
    assert article["meta_title"]
    assert article["slug"]
    assert article["locale"] == "fa"


def test_a_later_stage_supersedes_an_earlier_ones_sections(package, system_db) -> None:
    """Each stage returns the whole article, so the last one wins."""
    drive_to_gate_one(package)
    system_db.refresh(package)

    seo = system_db.scalars(
        select(StepRun).where(
            StepRun.package_id == package.id, StepRun.step == PipelineStep.SEO_OPTIMIZER
        )
    ).one()
    assert package.article["sections"] == seo.output_json["sections"]


def test_the_researcher_runs_without_a_dependency(package, system_db) -> None:
    advance_text(str(package.tenant_id), str(package.id))
    dispatch()

    run = system_db.scalars(
        select(StepRun).where(
            StepRun.package_id == package.id, StepRun.step == PipelineStep.RESEARCHER
        )
    ).one()
    assert run.input_json["dependencies"] == []


def test_a_later_step_is_shown_what_ran_before_it(package, system_db) -> None:
    drive_to_gate_one(package)

    writer = system_db.scalars(
        select(StepRun).where(StepRun.package_id == package.id, StepRun.step == PipelineStep.WRITER)
    ).one()
    assert set(writer.input_json["dependencies"]) == {"researcher", "strategist"}


# ---------------------------------------------------------------------------
# the QA loop
# ---------------------------------------------------------------------------
def test_a_failing_qa_report_sends_the_package_back_to_the_writer(package, system_db) -> None:
    """The step that reruns must be the writer, not the one after it."""
    from app.agents import simulation

    original = dict(simulation._SAMPLES[PipelineStep.QA])
    simulation._SAMPLES[PipelineStep.QA] = {
        **original,
        "score": 0.2,
        "verdict": "revise",
    }
    try:
        ran = drive_to_gate_one(package, limit=20)
    finally:
        simulation._SAMPLES[PipelineStep.QA] = original

    # The six steps up to and including QA, then back to the writer —
    # never on to the marketizer, since QA failed.
    assert ran[:6] == TEXT_STEPS[:6]
    assert ran[6] == PipelineStep.WRITER.value

    system_db.refresh(package)
    assert package.qa_retry_count >= 1
    assert package.qa_score == pytest.approx(0.2)


def test_the_qa_loop_gives_up_and_hands_over_to_a_person(package, system_db) -> None:
    """Two returns, then a human looks at it — not another GPU-hour."""
    from app.agents import simulation

    original = dict(simulation._SAMPLES[PipelineStep.QA])
    simulation._SAMPLES[PipelineStep.QA] = {**original, "score": 0.2, "verdict": "revise"}
    try:
        drive_to_gate_one(package, limit=40)
    finally:
        simulation._SAMPLES[PipelineStep.QA] = original

    system_db.refresh(package)
    assert package.status is PackageStatus.TEXT_REVIEW
    assert package.qa_retry_count == 2


def test_qa_feedback_reaches_the_writers_next_prompt(package, system_db) -> None:
    from app.agents import simulation

    original = dict(simulation._SAMPLES[PipelineStep.QA])
    simulation._SAMPLES[PipelineStep.QA] = {
        **original,
        "score": 0.2,
        "verdict": "revise",
        "issues": [
            {
                "severity": "blocker",
                "category": "unsourced_claim",
                "location": "بخش اول",
                "description": "این آمار منبع ندارد",
            }
        ],
        "unsourced_claims": ["رشد ۴۰ درصدی فروش"],
    }
    try:
        drive_to_gate_one(package, limit=20)
    finally:
        simulation._SAMPLES[PipelineStep.QA] = original

    writer_runs = system_db.scalars(
        select(StepRun)
        .where(StepRun.package_id == package.id, StepRun.step == PipelineStep.WRITER)
        .order_by(StepRun.created_at)
    ).all()
    assert len(writer_runs) >= 2
    feedback = writer_runs[-1].input_json["feedback"]
    assert any("این آمار منبع ندارد" in item for item in feedback)
    assert any("رشد ۴۰ درصدی فروش" in item for item in feedback)


# ---------------------------------------------------------------------------
# re-running a step
# ---------------------------------------------------------------------------
def test_a_step_can_be_re_run_on_its_own(package, system_db) -> None:
    from app.worker.tasks.pipeline import rerun_step

    drive_to_gate_one(package)
    before = len(
        system_db.scalars(
            select(StepRun).where(
                StepRun.package_id == package.id, StepRun.step == PipelineStep.WRITER
            )
        ).all()
    )

    rerun_step(str(package.tenant_id), str(package.id), PipelineStep.WRITER.value)
    dispatch()

    after = system_db.scalars(
        select(StepRun).where(StepRun.package_id == package.id, StepRun.step == PipelineStep.WRITER)
    ).all()
    assert len(after) == before + 1
    system_db.refresh(package)
    assert package.status is PackageStatus.DRAFTING


def test_re_running_a_step_outside_the_text_line_is_refused(package) -> None:
    from app.core.errors import InvalidStateError
    from app.worker.tasks.pipeline import rerun_step

    with pytest.raises(InvalidStateError):
        rerun_step(str(package.tenant_id), str(package.id), PipelineStep.MARKETIZER.value)


# ---------------------------------------------------------------------------
# gate 1 onwards
# ---------------------------------------------------------------------------
def test_an_editors_note_reaches_the_rewritten_draft(package, system_db) -> None:
    drive_to_gate_one(package)
    system_db.refresh(package)

    package_service.record_approval(
        system_db,
        package,
        ApprovalGate.TEXT,
        ApprovalRequest(
            decision=ApprovalDecision.CHANGES_REQUESTED,
            feedback="لحن خیلی رسمی است",
            return_to_step=PipelineStep.WRITER,
        ),
        decided_by=None,
    )
    system_db.commit()

    result = advance_text(str(package.tenant_id), str(package.id))
    assert result["step"] == PipelineStep.WRITER.value
    dispatch()

    writer_runs = system_db.scalars(
        select(StepRun)
        .where(StepRun.package_id == package.id, StepRun.step == PipelineStep.WRITER)
        .order_by(StepRun.created_at)
    ).all()
    assert any("لحن خیلی رسمی است" in item for item in writer_runs[-1].input_json["feedback"])


def test_the_marketizer_produces_the_channel_variants(package, system_db) -> None:
    from app.agents.context import build_context
    from app.agents.runner import run_agent

    drive_to_gate_one(package)
    system_db.refresh(package)

    run = run_agent(
        PipelineStep.MARKETIZER, build_context(system_db, package, PipelineStep.MARKETIZER)
    )
    package_service.apply_agent_output(system_db, package, PipelineStep.MARKETIZER, run.payload)

    variants = system_db.scalars(select(Variant).where(Variant.package_id == package.id)).all()
    assert {variant.channel.value for variant in variants} == {"wordpress", "telegram", "instagram"}
    wordpress = next(v for v in variants if v.channel.value == "wordpress")
    assert wordpress.body["hook"]
    # Text is overlaid, never generated into the image (handoff section 5).
    assert wordpress.visual_brief["render_text_separately"] is True

    # Instagram carousel/reel fields (handoff section 11) survive the round
    # trip from the agent's output into the stored Variant.
    instagram = next(v for v in variants if v.channel.value == "instagram")
    assert len(instagram.body["carousel_slides"]) == 2
    assert len(instagram.body["reel_script"]) == 2


def test_re_running_the_marketizer_replaces_rather_than_duplicates(package, system_db) -> None:
    """Two versions of one post in front of the editor at gate 2 is a bug."""
    from app.agents.context import build_context
    from app.agents.runner import run_agent

    drive_to_gate_one(package)
    system_db.refresh(package)

    context = build_context(system_db, package, PipelineStep.MARKETIZER)
    for _ in range(2):
        run = run_agent(PipelineStep.MARKETIZER, context)
        package_service.apply_agent_output(system_db, package, PipelineStep.MARKETIZER, run.payload)

    variants = system_db.scalars(select(Variant).where(Variant.package_id == package.id)).all()
    assert len(variants) == 3
