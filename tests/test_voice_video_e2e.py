"""A ``voice`` package walking the whole way to a real, muxed, narrated
video — the gap this closes. Same structure as
``tests/test_image_queue_e2e.py``, with the espeak/ffmpeg-backed voice
pipeline left real rather than swapped for a fake, the same way that file
leaves Playwright and Pillow real for the overlay and compositor.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.agents.llm import SimulatedLlmClient, set_client
from app.agents.simulation import sample_for_schema_name
from app.db.enums import (
    ApprovalDecision,
    ApprovalGate,
    MediaKind,
    PackageStatus,
    VideoMode,
)
from app.db.models import BrandBrief, ContentPackage, MediaAsset
from app.schemas.content import ApprovalRequest
from app.services import image_backend, quota, storage
from app.services import packages as package_service
from app.worker.gpu_runtime import SimulatedGpuRuntime, set_runtime
from app.worker.tasks.gpu import dispatch
from app.worker.tasks.media_cpu import mux_voice_video
from app.worker.tasks.pipeline import advance_text, media_finished, start_media
from tests.conftest import requires_db

pytestmark = [
    requires_db,
    pytest.mark.skipif(
        shutil.which("espeak-ng") is None or shutil.which("ffmpeg") is None,
        reason="espeak-ng and ffmpeg are required for the real voice pipeline",
    ),
]

@pytest.fixture(autouse=True)
def simulated_stack(monkeypatch):
    set_runtime(SimulatedGpuRuntime(speedup=1_000_000, seed=11))
    set_client(SimulatedLlmClient(sample_factory=sample_for_schema_name))
    storage.set_backend(storage.InMemoryStorageBackend())
    image_backend.set_backend(image_backend.SimulatedImageBackend(speedup=1_000_000, seed=4))
    # Both chained .delay() calls need something to consume them in-process —
    # same substitution test_image_queue_e2e.py makes for media_finished.
    monkeypatch.setattr(media_finished, "delay", lambda tid, pid: media_finished(tid, pid))
    monkeypatch.setattr(mux_voice_video, "delay", lambda tid, pid: mux_voice_video(tid, pid))
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


def drive_to_gate_one(package_row) -> None:
    for _ in range(12):
        result = advance_text(str(package_row.tenant_id), str(package_row.id))
        if result.get("status") == PackageStatus.TEXT_REVIEW.value:
            return
        dispatch()
    raise AssertionError("did not reach gate 1")


def drain_gpu(limit: int = 20) -> None:
    for _ in range(limit):
        result = dispatch()
        if not result.get("claimed"):
            return


def get_to_gate_two(package_row, session) -> None:
    drive_to_gate_one(package_row)
    session.refresh(package_row)
    package_service.record_approval(
        session,
        package_row,
        ApprovalGate.TEXT,
        ApprovalRequest(decision=ApprovalDecision.APPROVED),
        decided_by=None,
    )
    session.commit()
    start_media(str(package_row.tenant_id), str(package_row.id))
    drain_gpu()


def approve_gate_two(session, package_row) -> None:
    """Pick the first image, as an editor would, then pass gate 2 — which is
    what fires the mux (app.api.v1.packages.decide_gate)."""
    # get_to_gate_two's transition to SELECTION happens inside media_finished,
    # which runs over its own tenant_session — a different connection than
    # this one, so this session's cached copy of package_row is stale until
    # refreshed (same reasoning test_image_queue_e2e.py's
    # test_the_package_reaches_gate_two_on_its_own documents).
    session.refresh(package_row)
    image = session.scalars(
        select(MediaAsset).where(
            MediaAsset.package_id == package_row.id, MediaAsset.kind == MediaKind.IMAGE
        )
    ).first()
    image.is_selected = True
    session.flush()

    package_service.record_approval(
        session,
        package_row,
        ApprovalGate.MEDIA,
        ApprovalRequest(decision=ApprovalDecision.APPROVED),
        decided_by=None,
    )
    session.commit()

    # api/v1/packages.py fires this itself; the service layer alone (what
    # this test drives) does not, so the test fires it explicitly — the
    # eager .delay() patched above makes this synchronous either way.
    mux_voice_video.delay(str(package_row.tenant_id), str(package_row.id))


# ---------------------------------------------------------------------------
def test_a_voice_package_gets_one_narration_track_not_a_gallery(package, system_db) -> None:
    get_to_gate_two(package, system_db)

    audio_assets = system_db.scalars(
        select(MediaAsset).where(
            MediaAsset.package_id == package.id, MediaAsset.kind == MediaKind.AUDIO
        )
    ).all()
    assert len(audio_assets) == 1
    assert audio_assets[0].duration_seconds > 0
    assert audio_assets[0].is_ai_labelled is True


def test_captions_are_generated_alongside_the_narration(package, system_db) -> None:
    get_to_gate_two(package, system_db)

    subtitle = system_db.scalars(
        select(MediaAsset).where(
            MediaAsset.package_id == package.id, MediaAsset.kind == MediaKind.SUBTITLE
        )
    ).one_or_none()
    assert subtitle is not None
    srt = storage.get_backend().get(subtitle.storage_key)
    assert srt.startswith(b"1\n")


def test_gate_two_approval_produces_a_real_muxed_video(package, system_db) -> None:
    get_to_gate_two(package, system_db)
    approve_gate_two(system_db, package)

    video = system_db.scalars(
        select(MediaAsset).where(
            MediaAsset.package_id == package.id, MediaAsset.kind == MediaKind.VIDEO
        )
    ).one_or_none()
    assert video is not None
    assert video.mime_type == "video/mp4"
    assert video.duration_seconds and video.duration_seconds > 0
    assert video.is_ai_labelled is True

    raw = storage.get_backend().get(video.storage_key)
    assert len(raw) > 1000
    assert b"ftyp" in raw[:64]  # an MP4 container signature


def test_a_none_mode_package_is_never_muxed(tenant_factory, system_db, monkeypatch) -> None:
    tenant, workspace = tenant_factory("acme")
    quota.allocate_day(system_db, datetime.now(UTC).date(), capacity_seconds=20 * 3600)
    brief = BrandBrief(
        tenant_id=tenant.id,
        workspace_id=workspace.id,
        version=1,
        is_active=True,
        data={"brand": "آکمه", "channels": ["wordpress"]},
        video_mode=VideoMode.NONE,
    )
    system_db.add(brief)
    row = ContentPackage(
        tenant_id=tenant.id,
        workspace_id=workspace.id,
        brand_brief_id=brief.id,
        title="بدون ویدیو",
        locale="fa",
        status=PackageStatus.PLANNED,
        video_mode=VideoMode.NONE,
    )
    system_db.add(row)
    system_db.flush()
    system_db.commit()

    get_to_gate_two(row, system_db)
    system_db.refresh(row)  # see approve_gate_two's comment on why
    image = system_db.scalars(
        select(MediaAsset).where(
            MediaAsset.package_id == row.id, MediaAsset.kind == MediaKind.IMAGE
        )
    ).first()
    image.is_selected = True
    system_db.flush()
    package_service.record_approval(
        system_db,
        row,
        ApprovalGate.MEDIA,
        ApprovalRequest(decision=ApprovalDecision.APPROVED),
        decided_by=None,
    )
    system_db.commit()

    result = mux_voice_video(str(tenant.id), str(row.id))
    assert result == {"muxed": False, "reason": "video_mode_none"}

