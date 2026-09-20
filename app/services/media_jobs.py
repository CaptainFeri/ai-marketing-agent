"""Running an image job — the bridge between the GPU queue and the image
pipeline, the same role :mod:`app.agents.executor` plays for text agents.

One GPU job produces one finished, stored image:

1. the backend (simulated today, ComfyUI once phase 0 delivers it) makes the
   base picture with no text in it;
2. if the visual brief carries ``overlay_text``, it is drawn separately with
   a real browser (handoff section 5 — diffusion models mangle Persian and
   Arabic script);
3. the two are composited;
4. the result is uploaded under the tenant's storage prefix and the
   ``MediaAsset`` row is filled in.

Only step 1 is billed as GPU time (``ImageResult.gpu_seconds``); the overlay
render and compositing are fast CPU work that happens to run on the same
worker process, mirroring how the agent executor charges model time and not
wall clock.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.db.models import GpuJob, MediaAsset, Tenant
from app.services import image_backend, image_compose, image_overlay, storage

logger = logging.getLogger(__name__)


@dataclass
class ImageJobResult:
    storage_key: str
    gpu_seconds: float
    width: int
    height: int


def execute_image_job(session: Session, job: GpuJob) -> ImageJobResult:
    """Generate, overlay, compose and store the image a leased job asked for.

    Raises rather than swallowing: the caller books the failure against the
    job, which is what decides retry versus give up.
    """
    if job.media_asset_id is None:
        raise ValueError(f"gpu job {job.id} has no media asset to fill in")

    asset = session.get(MediaAsset, job.media_asset_id)
    if asset is None:
        raise LookupError(f"media asset {job.media_asset_id} not found")
    tenant = session.get(Tenant, job.tenant_id)
    if tenant is None:
        raise LookupError(f"tenant {job.tenant_id} not found")

    payload = job.payload
    prompt = str(payload.get("prompt") or "").strip()
    if not prompt:
        raise ValueError(f"gpu job {job.id} has no prompt to generate from")
    width = int(payload.get("width") or 1024)
    height = int(payload.get("height") or 1024)
    negative_prompt = payload.get("negative_prompt") or None
    palette = tuple(payload.get("palette") or ())
    overlay_text = payload.get("overlay_text") or None
    locale = str(payload.get("locale") or "fa")
    seed = payload.get("seed")

    backend = image_backend.get_backend()
    generated = backend.generate(
        prompt,
        width=width,
        height=height,
        negative_prompt=negative_prompt,
        seed=seed if isinstance(seed, int) else None,
    )

    overlay_png: bytes | None = None
    if overlay_text:
        try:
            overlay_png = image_overlay.render_overlay(
                image_overlay.OverlaySpec(
                    text=overlay_text,
                    width=generated.width,
                    height=generated.height,
                    locale=locale,
                    palette=palette,
                )
            )
        except image_overlay.OverlayRenderError as exc:
            # A picture with no caption is still a usable gate-2 option; a
            # hard failure over legibility styling is not a fair trade for
            # losing the whole image and its GPU time.
            logger.warning(
                "overlay render failed; shipping the image without text",
                extra={"job_id": str(job.id), "reason": str(exc)},
            )

    composed = image_compose.compose(generated.png_bytes, overlay_png)

    key = asset.storage_key or storage.tenant_key(
        tenant.storage_prefix, "packages", str(asset.package_id), "images", f"{asset.id}.png"
    )
    stored = storage.get_backend().put(key, composed.png_bytes, "image/png")

    asset.storage_key = stored.key
    asset.mime_type = stored.content_type
    asset.size_bytes = stored.size_bytes
    asset.width = composed.width
    asset.height = composed.height
    asset.model = (
        "simulated" if isinstance(backend, image_backend.SimulatedImageBackend) else "comfyui"
    )
    session.flush()

    return ImageJobResult(
        storage_key=stored.key,
        gpu_seconds=generated.gpu_seconds,
        width=composed.width,
        height=composed.height,
    )
