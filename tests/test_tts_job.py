"""Running one TTS job — narration, captions and storage tied together, the
same role ``tests/test_media_jobs.py`` covers for images.
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime

import pytest

from app.core.errors import InvalidStateError
from app.db.enums import GpuJobKind, MediaKind
from app.db.models import ContentPackage
from app.services import storage, tts_backend
from app.services.media_jobs import execute_tts_job
from app.worker import dispatcher
from tests.conftest import requires_db

pytestmark = [
    requires_db,
    pytest.mark.skipif(shutil.which("espeak-ng") is None, reason="espeak-ng is not installed"),
]


@pytest.fixture(autouse=True)
def _clean_singletons():
    storage.set_backend(storage.InMemoryStorageBackend())
    tts_backend.set_backend(None)  # real espeak backend, the default
    yield
    storage.set_backend(None)
    tts_backend.set_backend(None)


@pytest.fixture
def package_with_article(tenant_factory, system_db):
    from app.services import quota

    tenant, workspace = tenant_factory("acme")
    quota.allocate_day(system_db, datetime.now(UTC).date(), capacity_seconds=20 * 3600)
    row = ContentPackage(
        tenant_id=tenant.id,
        workspace_id=workspace.id,
        title="راهنمای خرید",
        locale="fa",
        article={
            "title": "راهنمای خرید دریل برقی",
            "excerpt": "این راهنما به شما کمک می‌کند بهترین دریل را انتخاب کنید.",
            "sections": [
                {"heading": "چرا این ابزار", "body": "این ابزار برای کارهای خانگی مناسب است."},
                {"heading": "جمع‌بندی", "body": "با توجه به بودجه خود انتخاب کنید."},
            ],
        },
    )
    system_db.add(row)
    system_db.flush()
    system_db.commit()
    return row


def make_tts_job(session, package):
    job = dispatcher.enqueue_job(
        session,
        tenant_id=package.tenant_id,
        kind=GpuJobKind.TTS,
        package_id=package.id,
        locale=package.locale,
        payload={"locale": package.locale},
    )
    session.flush()
    return job


def test_a_job_produces_stored_narration_and_captions(package_with_article, system_db) -> None:
    job = make_tts_job(system_db, package_with_article)

    result = execute_tts_job(system_db, job)

    from app.db.models import MediaAsset

    audio = system_db.get(MediaAsset, uuid.UUID(result.audio_asset_id))
    assert audio.kind is MediaKind.AUDIO
    assert audio.mime_type == "audio/wav"
    assert audio.duration_seconds and audio.duration_seconds > 0
    assert audio.is_ai_labelled is True  # decision D7
    assert storage.get_backend().get(audio.storage_key).startswith(b"RIFF")

    assert result.subtitle_asset_id is not None
    subtitle = system_db.get(MediaAsset, uuid.UUID(result.subtitle_asset_id))
    assert subtitle.kind is MediaKind.SUBTITLE
    srt = storage.get_backend().get(subtitle.storage_key)
    assert srt.startswith(b"1\n")
    assert "چرا این ابزار" not in srt.decode()  # heading is not narrated, only body


def test_gpu_seconds_are_zero_for_the_real_offline_backend(package_with_article, system_db) -> None:
    """espeak never touches the card — the quota ledger must not be charged
    for it (see tests/test_tts_backend.py and quota.record_actual)."""
    job = make_tts_job(system_db, package_with_article)
    result = execute_tts_job(system_db, job)
    assert result.gpu_seconds == 0.0


def test_a_package_with_no_article_yet_is_refused(tenant_factory, system_db) -> None:
    from app.services import quota

    tenant, workspace = tenant_factory("acme")
    quota.allocate_day(system_db, datetime.now(UTC).date(), capacity_seconds=20 * 3600)
    package = ContentPackage(
        tenant_id=tenant.id, workspace_id=workspace.id, title="x", locale="fa", article=None
    )
    system_db.add(package)
    system_db.flush()
    job = make_tts_job(system_db, package)

    with pytest.raises(InvalidStateError, match="no article text"):
        execute_tts_job(system_db, job)


def test_the_production_backend_fails_the_job_not_silently(package_with_article, system_db) -> None:
    tts_backend.set_backend(tts_backend.ProductionTtsBackend())
    job = make_tts_job(system_db, package_with_article)
    with pytest.raises(NotImplementedError):
        execute_tts_job(system_db, job)
