"""Orchestration on the ``pipeline`` queue.

These tasks move a package through the chain and put the GPU work on the
queue; they never touch the card themselves.  The agent prompts and the JSON
schema validation of each agent's output are phase 1 weeks 3-4 — this module
is the skeleton they plug into, and it is already complete enough to drive a
package from ``Planned`` to gate 1 against the simulated runtime.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from app.core.errors import InvalidStateError, QuotaExceededError
from app.db.enums import (
    GpuJobKind,
    MediaKind,
    PackageStatus,
    PipelineStep,
    StepStatus,
    VideoMode,
)
from app.db.models import MediaAsset, StepRun
from app.db.tenancy import tenant_session
from app.services import packages as package_service
from app.worker.celery_app import celery_app
from app.worker.dispatcher import enqueue_job
from app.worker.queues import Queue

logger = logging.getLogger(__name__)

#: How many image options each variant gets at gate 2.
IMAGE_OPTIONS_PER_PACKAGE = 4


@celery_app.task(name="pipeline.start_text", queue=Queue.PIPELINE.value)
def start_text(tenant_id: str, package_id: str) -> dict:
    """Queue the first text agent and move the package into drafting."""
    return advance_text(tenant_id, package_id)


@celery_app.task(name="pipeline.advance_text", queue=Queue.PIPELINE.value)
def advance_text(tenant_id: str, package_id: str) -> dict:
    """Queue the next text step, or hand the package to gate 1.

    Called after each text job finishes.  Idempotent: if the step already has
    a pending run, nothing new is queued.
    """
    tid, pid = uuid.UUID(tenant_id), uuid.UUID(package_id)
    with tenant_session(tid) as session:
        package = package_service.get_package(session, pid)

        if package.status in {PackageStatus.PLANNED, PackageStatus.REFRESH}:
            package_service.transition(package, PackageStatus.DRAFTING)

        next_step = package_service.next_text_step(package.current_step)
        if next_step is None:
            # The line is done; a human looks at it before any GPU time goes
            # into media (handoff section 3, gate 1).
            package_service.transition(package, PackageStatus.TEXT_REVIEW)
            logger.info("package reached gate 1", extra={"package_id": package_id})
            return {"package_id": package_id, "status": package.status.value}

        step_run = StepRun(
            tenant_id=tid,
            package_id=package.id,
            step=next_step,
            status=StepStatus.PENDING,
            attempt=1,
            started_at=datetime.now(UTC),
        )
        session.add(step_run)
        session.flush()

        try:
            job = enqueue_job(
                session,
                tenant_id=tid,
                kind=GpuJobKind.LLM_TEXT,
                workspace_id=package.workspace_id,
                package_id=package.id,
                step_run_id=step_run.id,
                locale=package.locale,
                payload={"step": next_step.value, "locale": package.locale},
            )
        except QuotaExceededError:
            # Section 7, step 4: over-quota work waits for tomorrow instead of
            # failing the package.
            step_run.status = StepStatus.PENDING
            step_run.error = "deferred: today's GPU quota is exhausted"
            logger.info("package deferred on quota", extra={"package_id": package_id})
            return {"package_id": package_id, "deferred": True}

        package.current_step = next_step
        return {"package_id": package_id, "step": next_step.value, "job_id": str(job.id)}


@celery_app.task(name="pipeline.rerun_step", queue=Queue.PIPELINE.value)
def rerun_step(tenant_id: str, package_id: str, step: str) -> dict:
    """Re-run one step and everything after it (handoff phase 1, week 3).

    The context is rebuilt from the database, so the step sees what it would
    have seen originally, plus whatever feedback has accumulated since.
    """
    tid, pid = uuid.UUID(tenant_id), uuid.UUID(package_id)
    target = PipelineStep(step)

    with tenant_session(tid) as session:
        package = package_service.get_package(session, pid)
        if package.status not in _RERUNNABLE_STATUSES:
            raise InvalidStateError(
                f"a package in {package.status.value} cannot be re-run; it is past "
                "the point where the text pipeline applies",
                details={"allowed": sorted(s.value for s in _RERUNNABLE_STATUSES)},
            )
        if target not in package_service.TEXT_PIPELINE:
            raise InvalidStateError(
                f"{step!r} is not part of the text pipeline",
                details={"steps": [s.value for s in package_service.TEXT_PIPELINE]},
            )
        if package.status is not PackageStatus.DRAFTING:
            package_service.transition(package, PackageStatus.DRAFTING)
        package_service.rewind_to(package, target)

    return advance_text(tenant_id, package_id)


#: Re-running the text line only makes sense before the media stage begins.
_RERUNNABLE_STATUSES = frozenset(
    {
        PackageStatus.DRAFTING,
        PackageStatus.TEXT_REVIEW,
        PackageStatus.REJECTED,
        PackageStatus.FAILED,
    }
)


@celery_app.task(name="pipeline.start_media", queue=Queue.PIPELINE.value)
def start_media(tenant_id: str, package_id: str) -> dict:
    """Fan out image and video work after gate 1 has been approved.

    Images and video are queued together; the GPU scheduler is what serialises
    them onto the single card, batching by model (handoff section 6).
    """
    tid, pid = uuid.UUID(tenant_id), uuid.UUID(package_id)
    queued: list[str] = []

    with tenant_session(tid) as session:
        package = package_service.get_package(session, pid)
        if package.status is not PackageStatus.MEDIA_GENERATING:
            raise InvalidStateError(
                f"media generation needs a package in media_generating, "
                f"this one is in {package.status.value}"
            )

        for index in range(IMAGE_OPTIONS_PER_PACKAGE):
            asset = MediaAsset(
                tenant_id=tid,
                package_id=package.id,
                kind=MediaKind.IMAGE,
                storage_key=f"{package.id}/images/option-{index}.png",
            )
            session.add(asset)
            session.flush()
            job = enqueue_job(
                session,
                tenant_id=tid,
                kind=GpuJobKind.IMAGE_FLUX,
                workspace_id=package.workspace_id,
                package_id=package.id,
                media_asset_id=asset.id,
                locale=package.locale,
                payload={"option": index, "locale": package.locale},
            )
            queued.append(str(job.id))

        for kind in _voice_chain(package.video_mode):
            job = enqueue_job(
                session,
                tenant_id=tid,
                kind=kind,
                workspace_id=package.workspace_id,
                package_id=package.id,
                locale=package.locale,
                payload={"video_mode": package.video_mode.value, "locale": package.locale},
            )
            queued.append(str(job.id))

    logger.info("media queued", extra={"package_id": package_id, "jobs": len(queued)})
    return {"package_id": package_id, "queued": queued}


def _voice_chain(video_mode: VideoMode) -> tuple[GpuJobKind, ...]:
    """The GPU steps each video mode needs (handoff section 8).

    ``none`` needs no speech at all; ``voice`` needs TTS plus Whisper for
    subtitle timing; ``face`` adds lip-sync on top.  Persian TTS runs on the
    CPU via Piper and so never appears here.
    """
    if video_mode is VideoMode.NONE:
        return ()
    chain: tuple[GpuJobKind, ...] = (GpuJobKind.TTS, GpuJobKind.TRANSCRIBE_WHISPER)
    if video_mode is VideoMode.FACE:
        chain += (GpuJobKind.LIPSYNC_LATENTSYNC,)
    return chain


@celery_app.task(name="pipeline.media_finished", queue=Queue.PIPELINE.value)
def media_finished(tenant_id: str, package_id: str) -> dict:
    """Move a package to gate 2 once its media is ready."""
    tid, pid = uuid.UUID(tenant_id), uuid.UUID(package_id)
    with tenant_session(tid) as session:
        package = package_service.get_package(session, pid)
        package_service.transition(package, PackageStatus.SELECTION)
        return {"package_id": package_id, "status": package.status.value}
