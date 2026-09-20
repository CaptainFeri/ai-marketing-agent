"""Running one image job — backend, overlay, compositor and storage tied
together, and the ``MediaAsset`` row filled in from what actually happened.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.db.enums import GpuJobKind, MediaKind
from app.db.models import ContentPackage, MediaAsset, Tenant
from app.services import image_backend, storage
from app.services.media_jobs import execute_image_job
from app.worker import dispatcher
from tests.conftest import requires_db

pytestmark = requires_db


@pytest.fixture(autouse=True)
def _clean_singletons():
    storage.set_backend(storage.InMemoryStorageBackend())
    image_backend.set_backend(image_backend.SimulatedImageBackend(speedup=1_000_000, seed=3))
    yield
    storage.set_backend(None)
    image_backend.set_backend(None)


@pytest.fixture
def package_with_quota(tenant_factory, system_db):
    from app.services import quota

    tenant, workspace = tenant_factory("acme")
    quota.allocate_day(system_db, datetime.now(UTC).date(), capacity_seconds=20 * 3600)
    row = ContentPackage(tenant_id=tenant.id, workspace_id=workspace.id, title="x", locale="fa")
    system_db.add(row)
    system_db.flush()
    system_db.commit()
    return row


def make_asset_and_job(session, package, **payload_overrides):
    tenant = session.get(Tenant, package.tenant_id)
    asset = MediaAsset(
        tenant_id=package.tenant_id,
        package_id=package.id,
        kind=MediaKind.IMAGE,
        storage_key="",
    )
    session.add(asset)
    session.flush()
    asset.storage_key = storage.tenant_key(
        tenant.storage_prefix, "packages", str(package.id), "images", f"{asset.id}.png"
    )
    payload = {
        "prompt": "a drill on a workbench",
        "width": 256,
        "height": 256,
        "locale": "fa",
        "seed": 0,
        **payload_overrides,
    }
    job = dispatcher.enqueue_job(
        session,
        tenant_id=package.tenant_id,
        kind=GpuJobKind.IMAGE_FLUX,
        package_id=package.id,
        media_asset_id=asset.id,
        payload=payload,
    )
    session.flush()
    return asset, job


def test_a_job_produces_a_stored_image_and_fills_in_the_asset(
    package_with_quota, system_db
) -> None:
    asset, job = make_asset_and_job(system_db, package_with_quota)

    result = execute_image_job(system_db, job)

    system_db.refresh(asset)
    assert asset.storage_key == result.storage_key
    assert asset.mime_type == "image/png"
    assert asset.width == 256
    assert asset.height == 256
    assert asset.size_bytes and asset.size_bytes > 0
    assert asset.model == "simulated"

    stored = storage.get_backend().get(asset.storage_key)
    assert stored.startswith(b"\x89PNG\r\n\x1a\n")


def test_gpu_seconds_come_from_the_backend_not_the_wall_clock(
    package_with_quota, system_db
) -> None:
    """Overlay rendering and compositing are fast CPU work happening on the
    same worker — only the model's own time is billed, mirroring how the
    agent executor separates model_seconds from wall clock."""
    _, job = make_asset_and_job(system_db, package_with_quota)
    result = execute_image_job(system_db, job)
    assert result.gpu_seconds > 0


def test_overlay_text_is_composited_when_present(package_with_quota, system_db) -> None:
    import io

    from PIL import Image

    asset, job = make_asset_and_job(
        system_db,
        package_with_quota,
        overlay_text="راهنمای خرید دریل برقی",
        palette=["#1F4E79"],
    )
    execute_image_job(system_db, job)

    system_db.refresh(asset)
    with_overlay = storage.get_backend().get(asset.storage_key)

    # Same prompt/seed but no overlay text, for comparison.
    asset2, job2 = make_asset_and_job(system_db, package_with_quota)
    execute_image_job(system_db, job2)
    system_db.refresh(asset2)
    without_overlay = storage.get_backend().get(asset2.storage_key)

    assert with_overlay != without_overlay
    assert Image.open(io.BytesIO(with_overlay)).size == (256, 256)


def test_no_overlay_text_means_a_plain_base_image(package_with_quota, system_db) -> None:
    asset, job = make_asset_and_job(system_db, package_with_quota, overlay_text=None)
    execute_image_job(system_db, job)
    system_db.refresh(asset)
    assert storage.get_backend().exists(asset.storage_key)


def test_an_overlay_failure_does_not_lose_the_whole_image(
    package_with_quota, system_db, monkeypatch
) -> None:
    """A picture with no caption is still a usable gate-2 option; a hard
    failure over legibility styling is not a fair trade for the GPU time
    already spent generating the base image."""
    from app.services import image_overlay

    def boom(spec):
        raise image_overlay.OverlayRenderError("no chromium in this container")

    monkeypatch.setattr(image_overlay, "render_overlay", boom)

    asset, job = make_asset_and_job(system_db, package_with_quota, overlay_text="متن روی تصویر")
    result = execute_image_job(system_db, job)

    system_db.refresh(asset)
    assert asset.mime_type == "image/png"
    assert result.gpu_seconds > 0


def test_a_missing_media_asset_reference_is_reported_clearly(package_with_quota, system_db) -> None:
    _, job = make_asset_and_job(system_db, package_with_quota)
    job.media_asset_id = None
    system_db.flush()

    with pytest.raises(ValueError, match="no media asset"):
        execute_image_job(system_db, job)


def test_an_empty_prompt_is_refused_rather_than_billed(package_with_quota, system_db) -> None:
    _, job = make_asset_and_job(system_db, package_with_quota, prompt="   ")
    with pytest.raises(ValueError, match="no prompt"):
        execute_image_job(system_db, job)


def test_the_real_backend_fails_the_job_not_silently(package_with_quota, system_db) -> None:
    image_backend.set_backend(image_backend.ComfyUIImageBackend())
    _, job = make_asset_and_job(system_db, package_with_quota)
    with pytest.raises(NotImplementedError):
        execute_image_job(system_db, job)
