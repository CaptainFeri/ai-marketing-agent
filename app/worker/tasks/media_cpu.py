"""The ``media_cpu`` queue: work that is CPU-bound but must not compete with
the single GPU worker for its one concurrency slot (handoff section 4) —
today, muxing the ``voice`` mode video once gate 2 has picked a background
image.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select

from app.db.enums import MediaKind, VideoMode
from app.db.models import ContentPackage, MediaAsset, Tenant
from app.db.tenancy import tenant_session
from app.services import storage, video_compose
from app.worker.celery_app import celery_app
from app.worker.queues import Queue

logger = logging.getLogger(__name__)

#: A gate-2 gallery option is generated at this size (see
#: image_compose.dimensions_for's "1:1" case) — the mux reuses it as the
#: frame size rather than re-deriving an aspect ratio from the file itself.
_DEFAULT_WIDTH = _DEFAULT_HEIGHT = 1024


@celery_app.task(name="media_cpu.mux_voice_video", queue=Queue.MEDIA_CPU.value)
def mux_voice_video(tenant_id: str, package_id: str) -> dict:
    """Combine the selected image, the narration track and its captions
    into the package's video, once an editor has approved gate 2.

    Best-effort: gate 2 approval has already happened by the time this runs,
    so a failure here is logged and swallowed rather than raised — a package
    with no muxed video is still fully schedulable on its text and image
    output, and an operator can re-run this task once the cause is fixed.
    """
    tid, pid = uuid.UUID(tenant_id), uuid.UUID(package_id)

    with tenant_session(tid) as session:
        package = session.get(ContentPackage, pid)
        if package is None:
            logger.warning("mux_voice_video: package not found", extra={"package_id": package_id})
            return {"muxed": False, "reason": "package_not_found"}
        if package.video_mode is VideoMode.NONE:
            return {"muxed": False, "reason": "video_mode_none"}

        image_asset = session.scalars(
            select(MediaAsset)
            .where(
                MediaAsset.package_id == pid,
                MediaAsset.kind == MediaKind.IMAGE,
                MediaAsset.is_selected.is_(True),
            )
            .order_by(MediaAsset.created_at)
            .limit(1)
        ).one_or_none()
        audio_asset = session.scalars(
            select(MediaAsset)
            .where(MediaAsset.package_id == pid, MediaAsset.kind == MediaKind.AUDIO)
            .order_by(MediaAsset.created_at.desc())
            .limit(1)
        ).one_or_none()
        subtitle_asset = session.scalars(
            select(MediaAsset)
            .where(MediaAsset.package_id == pid, MediaAsset.kind == MediaKind.SUBTITLE)
            .order_by(MediaAsset.created_at.desc())
            .limit(1)
        ).one_or_none()

        if image_asset is None or audio_asset is None:
            logger.warning(
                "mux_voice_video: missing an ingredient",
                extra={
                    "package_id": package_id,
                    "has_image": image_asset is not None,
                    "has_audio": audio_asset is not None,
                },
            )
            return {"muxed": False, "reason": "missing_asset"}

        tenant = session.get(Tenant, tid)
        if tenant is None:
            return {"muxed": False, "reason": "tenant_not_found"}

        backend = storage.get_backend()
        try:
            image_bytes = backend.get(image_asset.storage_key)
            audio_bytes = backend.get(audio_asset.storage_key)
            srt_bytes = backend.get(subtitle_asset.storage_key) if subtitle_asset else None

            result = video_compose.mux_voice_video(
                image_bytes,
                audio_bytes,
                srt_bytes,
                width=image_asset.width or _DEFAULT_WIDTH,
                height=image_asset.height or _DEFAULT_HEIGHT,
            )
        except Exception:  # noqa: BLE001 - best-effort, see docstring
            logger.exception("mux_voice_video failed", extra={"package_id": package_id})
            return {"muxed": False, "reason": "mux_failed"}

        key = storage.tenant_key(
            tenant.storage_prefix, "packages", str(pid), "video", f"{image_asset.id}.mp4"
        )
        stored = backend.put(key, result.video_bytes, "video/mp4")

        video_asset = MediaAsset(
            tenant_id=tid,
            package_id=pid,
            kind=MediaKind.VIDEO,
            storage_key=stored.key,
            mime_type=stored.content_type,
            size_bytes=stored.size_bytes,
            width=result.width,
            height=result.height,
            duration_seconds=result.duration_seconds,
            model="ffmpeg",
            is_selected=True,
            is_ai_labelled=True,
        )
        session.add(video_asset)
        session.flush()

        logger.info(
            "voice video muxed",
            extra={"package_id": package_id, "video_asset_id": str(video_asset.id)},
        )
        return {"muxed": True, "video_asset_id": str(video_asset.id)}
