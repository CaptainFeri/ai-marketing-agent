"""The image queue end to end: gate 1 → real visual-brief-driven FLUX
prompts → the real overlay/compose/storage pipeline → gate 2 → selection.

The language model and the FLUX generation are simulated; the queue, the
leases, the marketizer's visual briefs, the overlay renderer (a real headless
browser), the compositor and the storage backend are all the production code
path.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.agents.llm import SimulatedLlmClient, set_client
from app.agents.simulation import sample_for_schema_name
from app.core.errors import InvalidStateError
from app.db.enums import (
    ApprovalDecision,
    ApprovalGate,
    GpuJobKind,
    GpuJobStatus,
    MediaKind,
    PackageStatus,
    VideoMode,
)
from app.db.models import BrandBrief, ContentPackage, GpuJob, MediaAsset, Variant
from app.schemas.content import ApprovalRequest
from app.services import image_backend, quota, storage
from app.services import packages as package_service
from app.worker.gpu_runtime import SimulatedGpuRuntime, set_runtime
from app.worker.tasks.gpu import dispatch
from app.worker.tasks.pipeline import advance_text, media_finished, start_media
from tests.conftest import requires_db

pytestmark = requires_db


@pytest.fixture(autouse=True)
def simulated_stack(monkeypatch):
    set_runtime(SimulatedGpuRuntime(speedup=1_000_000, seed=11))
    set_client(SimulatedLlmClient(sample_factory=sample_for_schema_name))
    storage.set_backend(storage.InMemoryStorageBackend())
    image_backend.set_backend(image_backend.SimulatedImageBackend(speedup=1_000_000, seed=4))
    # gpu.py chains gate-2 readiness with media_finished.delay(...), which in
    # production a real Celery worker consumes off the broker. Nothing
    # consumes the broker in this test process, so .delay() is made to run
    # the task body immediately instead — the same substitution the rest of
    # this suite makes implicitly by calling a task directly rather than
    # through Celery, just applied to a call this test does not make itself.
    monkeypatch.setattr(media_finished, "delay", lambda tid, pid: media_finished(tid, pid))
    yield
    set_runtime(None)
    set_client(None)
    storage.set_backend(None)
    image_backend.set_backend(None)


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
        video_mode=VideoMode.NONE,
    )
    system_db.add(brief)
    row = ContentPackage(
        tenant_id=tenant.id,
        workspace_id=workspace.id,
        brand_brief_id=brief.id,
        title="راهنمای خرید دریل برقی",
        locale="fa",
        status=PackageStatus.PLANNED,
        video_mode=VideoMode.NONE,
    )
    system_db.add(row)
    system_db.flush()
    system_db.commit()
    return row


def drive_to_gate_one(package_row) -> None:
    for _ in range(12):
        result = advance_text(str(package_row.tenant_id), str(package_row.id))
        if result.get("status") == PackageStatus.TEXT_REVIEW.value:
            return
        dispatch()
    raise AssertionError("did not reach gate 1")


def approve_gate_one(session, package_row) -> None:
    package_service.record_approval(
        session,
        package_row,
        ApprovalGate.TEXT,
        ApprovalRequest(decision=ApprovalDecision.APPROVED),
        decided_by=None,
    )
    session.commit()


def drain_gpu(limit: int = 20) -> None:
    for _ in range(limit):
        result = dispatch()
        if not result.get("claimed"):
            return


def get_up_to_gate_two(package_row, session) -> None:
    drive_to_gate_one(package_row)
    session.refresh(package_row)
    approve_gate_one(session, package_row)
    start_media(str(package_row.tenant_id), str(package_row.id))
    drain_gpu()


# ---------------------------------------------------------------------------
def test_media_generation_uses_the_marketizers_own_visual_brief(package, system_db) -> None:
    """The prompt actually reflects what gate 1 approved — not a generic,
    per-package filler unrelated to the marketizer's output."""
    get_up_to_gate_two(package, system_db)

    image_jobs = system_db.scalars(select(GpuJob).where(GpuJob.kind == GpuJobKind.IMAGE_FLUX)).all()
    assert len(image_jobs) == 4
    for job in image_jobs:
        # The simulated marketizer's wordpress variant scene text.
        assert "a scene with no text in it" in job.payload["prompt"]
        assert job.payload["aspect_ratio"] == "16:9"
        assert job.payload["overlay_text"] == "[simulated] overlay"


def test_four_image_options_are_generated_and_stored(package, system_db) -> None:
    get_up_to_gate_two(package, system_db)

    assets = system_db.scalars(
        select(MediaAsset).where(
            MediaAsset.package_id == package.id, MediaAsset.kind == MediaKind.IMAGE
        )
    ).all()
    assert len(assets) == 4

    backend = storage.get_backend()
    contents = set()
    for asset in assets:
        assert asset.mime_type == "image/png"
        assert asset.width == 1280 and asset.height == 720  # 16:9 at ~1024px
        raw = backend.get(asset.storage_key)
        assert raw.startswith(b"\x89PNG\r\n\x1a\n")
        contents.add(raw)

    # Four distinct seeds must not collapse into one identical placeholder —
    # a gallery of four copies of the same image is not four options.
    assert len(contents) == 4


def test_the_package_reaches_gate_two_on_its_own(package, system_db) -> None:
    """No test-only glue calls media_finished directly — the GPU task chains
    it automatically once nothing is left pending for the package."""
    get_up_to_gate_two(package, system_db)
    system_db.refresh(package)
    assert package.status is PackageStatus.SELECTION


def test_every_gpu_job_for_the_package_finished(package, system_db) -> None:
    get_up_to_gate_two(package, system_db)
    remaining = system_db.scalars(
        select(GpuJob).where(
            GpuJob.package_id == package.id,
            GpuJob.status.notin_([GpuJobStatus.SUCCEEDED, GpuJobStatus.FAILED]),
        )
    ).all()
    assert remaining == []


def test_gpu_time_for_images_is_charged_to_the_tenant(package, system_db) -> None:
    get_up_to_gate_two(package, system_db)
    status = quota.quota_status(system_db, package.tenant_id)
    assert status.consumed_seconds > 0


def test_a_visual_brief_with_no_marketizer_scene_still_fills_the_gallery(
    package, system_db
) -> None:
    """The contract allows a channel with no visual brief at all (a
    text-only post); the fallback in distinct_visual_briefs must still leave
    gate 2 with something to choose from."""
    from app.agents import simulation
    from app.db.enums import PipelineStep

    original = dict(simulation._SAMPLES[PipelineStep.MARKETIZER])
    simulation._SAMPLES[PipelineStep.MARKETIZER] = {
        "variants": [
            {"channel": "telegram", "hook": "x", "body": "y", "hashtags": []},
        ],
        "video_script": [],
    }
    try:
        get_up_to_gate_two(package, system_db)
    finally:
        simulation._SAMPLES[PipelineStep.MARKETIZER] = original

    assets = system_db.scalars(select(MediaAsset).where(MediaAsset.package_id == package.id)).all()
    assert len(assets) == 4
    system_db.refresh(package)
    assert package.status is PackageStatus.SELECTION


# ---------------------------------------------------------------------------
# selection and gate 2
# ---------------------------------------------------------------------------
def test_gate_two_cannot_be_approved_with_nothing_selected(package, system_db) -> None:
    """Section 3: gate 2 is "select option + schedule". Approving it without
    having picked anything is skipping the gate, not passing it."""
    get_up_to_gate_two(package, system_db)
    system_db.refresh(package)

    with pytest.raises(InvalidStateError):
        package_service.record_approval(
            system_db,
            package,
            ApprovalGate.MEDIA,
            ApprovalRequest(decision=ApprovalDecision.APPROVED),
            decided_by=None,
        )


def test_gate_two_approves_once_something_is_selected(package, system_db) -> None:
    get_up_to_gate_two(package, system_db)
    system_db.refresh(package)

    asset = system_db.scalars(
        select(MediaAsset).where(MediaAsset.package_id == package.id).limit(1)
    ).one()
    asset.is_selected = True
    system_db.flush()

    package_service.record_approval(
        system_db,
        package,
        ApprovalGate.MEDIA,
        ApprovalRequest(decision=ApprovalDecision.APPROVED),
        decided_by=None,
    )
    assert package.status is PackageStatus.SCHEDULED


def test_rejecting_gate_two_needs_no_selection(package, system_db) -> None:
    """The selection requirement only applies to approving — rejecting a
    gallery nobody liked should not first demand a pick from it."""
    get_up_to_gate_two(package, system_db)
    system_db.refresh(package)

    package_service.record_approval(
        system_db,
        package,
        ApprovalGate.MEDIA,
        ApprovalRequest(decision=ApprovalDecision.REJECTED, feedback="نه"),
        decided_by=None,
    )
    assert package.status is PackageStatus.REJECTED


def test_distinct_channels_sharing_no_visual_brief_still_produce_one_gallery(
    package, system_db
) -> None:
    """wordpress and telegram in the simulated sample: one has a visual
    brief, one does not (falls back to None, not a duplicate of the
    other) — exactly one gallery, not two."""
    get_up_to_gate_two(package, system_db)

    variants = system_db.scalars(select(Variant).where(Variant.package_id == package.id)).all()
    assert {v.channel.value for v in variants} == {"wordpress", "telegram"}

    assets = system_db.scalars(select(MediaAsset).where(MediaAsset.package_id == package.id)).all()
    assert len(assets) == 4  # one gallery of four, not eight
