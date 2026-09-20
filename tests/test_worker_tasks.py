"""The Celery tasks, invoked directly rather than through a broker.

Celery task objects are callable, so the task body runs synchronously here —
no worker, no Redis. What is being checked is the orchestration: that a
package walks the chain, that the GPU task drains a batch, and that
maintenance does its bookkeeping.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.db.enums import (
    GpuJobKind,
    GpuJobStatus,
    MediaKind,
    PackageStatus,
    PipelineStep,
    VideoMode,
)
from app.db.models import BrandBrief, ContentPackage, GpuJob, MediaAsset
from app.services import quota
from app.worker import dispatcher
from app.worker.gpu_runtime import SimulatedGpuRuntime, set_runtime
from app.worker.tasks.gpu import dispatch
from app.worker.tasks.maintenance import (
    allocate_daily_quota,
    reclaim_gpu_leases,
    report_queue_depth,
)
from app.worker.tasks.pipeline import advance_text, media_finished, start_media
from tests.conftest import requires_db

pytestmark = requires_db


@pytest.fixture(autouse=True)
def fast_runtime():
    """Install a simulated GPU so the tasks have something to run against."""
    set_runtime(SimulatedGpuRuntime(speedup=1_000_000, seed=3))
    yield
    set_runtime(None)


@pytest.fixture
def package(tenant_factory, system_db):
    tenant, workspace = tenant_factory("acme")
    quota.allocate_day(system_db, datetime.now(UTC).date(), capacity_seconds=20 * 3600)
    brief = BrandBrief(
        tenant_id=tenant.id,
        workspace_id=workspace.id,
        version=1,
        is_active=True,
        data={"brand": "Acme"},
        video_mode=VideoMode.VOICE,
    )
    system_db.add(brief)
    row = ContentPackage(
        tenant_id=tenant.id,
        workspace_id=workspace.id,
        brand_brief_id=brief.id,
        title="راهنمای خرید",
        locale="fa",
        status=PackageStatus.PLANNED,
        video_mode=VideoMode.VOICE,
    )
    system_db.add(row)
    system_db.flush()
    system_db.commit()
    return row


# --------------------------------------------------------------------------
# text pipeline
# --------------------------------------------------------------------------
def test_the_first_advance_starts_drafting(package, system_db) -> None:
    result = advance_text(str(package.tenant_id), str(package.id))

    assert result["step"] == PipelineStep.RESEARCHER.value
    system_db.refresh(package)
    assert package.status is PackageStatus.DRAFTING
    assert package.current_step is PipelineStep.RESEARCHER
    assert len(package.step_runs) == 1


def test_the_package_walks_the_chain_to_gate_one(package, system_db) -> None:
    """Six agent turns, then a human gate — no media work is queued before it."""
    steps = []
    for _ in range(10):
        result = advance_text(str(package.tenant_id), str(package.id))
        if result.get("status") == PackageStatus.TEXT_REVIEW.value:
            break
        steps.append(result["step"])
        dispatch()
    else:  # pragma: no cover - would mean the chain never terminates
        raise AssertionError("the pipeline did not reach gate 1")

    assert steps == [
        step.value
        for step in [
            PipelineStep.RESEARCHER,
            PipelineStep.STRATEGIST,
            PipelineStep.WRITER,
            PipelineStep.GEO_OPTIMIZER,
            PipelineStep.SEO_OPTIMIZER,
            PipelineStep.QA,
            PipelineStep.MARKETIZER,
        ]
    ]

    system_db.refresh(package)
    assert package.status is PackageStatus.TEXT_REVIEW
    assert package.gpu_seconds > 0
    # Nothing media-side was queued: gate 1 has not been passed.
    media_jobs = system_db.scalars(select(GpuJob).where(GpuJob.kind != GpuJobKind.LLM_TEXT)).all()
    assert media_jobs == []


def test_work_past_the_daily_share_is_deferred_not_failed(package, system_db) -> None:
    """Handoff section 7, step 4: it waits for tomorrow."""
    quota.allocate_day(system_db, datetime.now(UTC).date(), capacity_seconds=1.0)
    system_db.commit()

    result = advance_text(str(package.tenant_id), str(package.id))

    assert result["deferred"] is True
    system_db.refresh(package)
    assert package.status is PackageStatus.DRAFTING
    assert system_db.scalars(select(GpuJob)).all() == []


# --------------------------------------------------------------------------
# media
# --------------------------------------------------------------------------
def test_media_fans_out_after_gate_one(package, system_db) -> None:
    package.status = PackageStatus.MEDIA_GENERATING
    system_db.commit()

    result = start_media(str(package.tenant_id), str(package.id))

    # Four image options, plus TTS and Whisper for a `voice` package.
    assert len(result["queued"]) == 6
    kinds = {job.kind for job in system_db.scalars(select(GpuJob)).all()}
    assert kinds == {
        GpuJobKind.IMAGE_FLUX,
        GpuJobKind.TTS,
        GpuJobKind.TRANSCRIBE_WHISPER,
    }
    assets = system_db.scalars(select(MediaAsset)).all()
    assert len(assets) == 4
    assert all(asset.kind is MediaKind.IMAGE for asset in assets)


def test_a_none_mode_package_queues_no_speech(package, system_db) -> None:
    package.status = PackageStatus.MEDIA_GENERATING
    package.video_mode = VideoMode.NONE
    system_db.commit()

    start_media(str(package.tenant_id), str(package.id))

    kinds = {job.kind for job in system_db.scalars(select(GpuJob)).all()}
    assert kinds == {GpuJobKind.IMAGE_FLUX}


def test_a_face_mode_package_queues_lip_sync(package, system_db) -> None:
    package.status = PackageStatus.MEDIA_GENERATING
    package.video_mode = VideoMode.FACE
    system_db.commit()

    start_media(str(package.tenant_id), str(package.id))

    kinds = {job.kind for job in system_db.scalars(select(GpuJob)).all()}
    assert GpuJobKind.LIPSYNC_LATENTSYNC in kinds


def test_media_generation_refuses_a_package_before_gate_one(package) -> None:
    from app.core.errors import InvalidStateError

    with pytest.raises(InvalidStateError):
        start_media(str(package.tenant_id), str(package.id))


def test_finishing_media_opens_gate_two(package, system_db) -> None:
    package.status = PackageStatus.MEDIA_GENERATING
    system_db.commit()

    media_finished(str(package.tenant_id), str(package.id))

    system_db.refresh(package)
    assert package.status is PackageStatus.SELECTION


# --------------------------------------------------------------------------
# gpu and maintenance tasks
# --------------------------------------------------------------------------
def test_dispatch_is_a_no_op_on_an_empty_queue() -> None:
    assert dispatch() == {"claimed": 0}


def test_dispatch_runs_a_whole_batch(package, system_db) -> None:
    # TTS rather than IMAGE_FLUX: images now go through the real image
    # pipeline (app.services.media_jobs), which needs a MediaAsset behind
    # each job — see tests/test_media_jobs.py for that path. This test is
    # about the generic dispatch/complete mechanics, so any simulated-runtime
    # kind demonstrates it just as well.
    for _ in range(3):
        dispatcher.enqueue_job(
            system_db,
            tenant_id=package.tenant_id,
            kind=GpuJobKind.TTS,
            package_id=package.id,
        )
    system_db.commit()

    result = dispatch()

    assert result["succeeded"] == 3
    assert result["failed"] == 0
    assert result["kind"] == GpuJobKind.TTS.value
    remaining = system_db.scalars(
        select(GpuJob).where(GpuJob.status != GpuJobStatus.SUCCEEDED)
    ).all()
    assert remaining == []


def test_a_runtime_failure_is_recorded_not_swallowed(package, system_db) -> None:
    class Exploding(SimulatedGpuRuntime):
        def run(self, kind, payload):
            raise RuntimeError("CUDA out of memory")

    # Same reasoning as above: TTS still goes through runtime.run(), which is
    # what this override needs to intercept.
    set_runtime(Exploding(speedup=1_000_000))
    dispatcher.enqueue_job(system_db, tenant_id=package.tenant_id, kind=GpuJobKind.TTS)
    system_db.commit()

    result = dispatch()

    assert result["failed"] == 1
    job = system_db.scalars(select(GpuJob)).one()
    system_db.refresh(job)
    assert "CUDA out of memory" in job.error
    # First failure: retried, not abandoned.
    assert job.status is GpuJobStatus.PENDING


def test_the_maintenance_allocator_covers_every_tenant(tenant_factory, system_db) -> None:
    tenant_factory("one")
    tenant_factory("two")
    system_db.commit()

    assert allocate_daily_quota() == 2


def test_maintenance_reclaims_a_dead_workers_lease(package, system_db) -> None:
    job = dispatcher.enqueue_job(system_db, tenant_id=package.tenant_id, kind=GpuJobKind.IMAGE_FLUX)
    job.status = GpuJobStatus.LEASED
    job.lease_owner = "dead-worker"
    job.lease_expires_at = datetime.now(UTC) - timedelta(minutes=5)
    system_db.commit()

    assert reclaim_gpu_leases() == 1
    system_db.refresh(job)
    assert job.status is GpuJobStatus.PENDING


def test_queue_depth_is_reportable(package, system_db) -> None:
    dispatcher.enqueue_job(system_db, tenant_id=package.tenant_id, kind=GpuJobKind.LLM_TEXT)
    system_db.commit()
    assert report_queue_depth() == {"text": 1, "media": 0}


def test_an_unknown_package_id_is_reported_clearly(package) -> None:
    from app.core.errors import NotFoundError

    with pytest.raises(NotFoundError):
        advance_text(str(package.tenant_id), str(uuid.uuid4()))
