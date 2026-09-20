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
from app.db.models import MediaAsset, StepRun, Tenant, Workspace
from app.db.tenancy import tenant_session
from app.services import brief_draft as draft_service
from app.services import image_compose, storage, website
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


@celery_app.task(name="pipeline.suggest_brief", queue=Queue.PIPELINE.value)
def suggest_brief(
    tenant_id: str,
    workspace_id: str,
    website_url: str | None = None,
    refresh_website: bool = False,
) -> dict:
    """Prepare a brand brief suggestion for the questionnaire.

    Two halves on two queues: reading the customer's website is I/O on the
    CPU queue, and drafting from it is a model call that has to go through the
    GPU queue like everything else. Doing the fetch here keeps a slow customer
    site from occupying the card while it times out.
    """
    tid, wid = uuid.UUID(tenant_id), uuid.UUID(workspace_id)

    with tenant_session(tid) as session:
        draft = draft_service.get_or_create_draft(session, tid, wid)

        if website_url and (refresh_website or not draft.website_excerpt):
            try:
                content = website.fetch(website_url)
            except website.WebsiteFetchError as exc:
                # A bad URL is the customer's to fix, and it is the whole
                # point of the button, so it fails rather than quietly
                # producing a suggestion from nothing.
                draft_service.fail_suggestion(draft, str(exc))
                logger.info(
                    "website could not be read",
                    extra={"workspace_id": workspace_id, "reason": str(exc)},
                )
                return {"workspace_id": workspace_id, "error": str(exc)}
            draft_service.store_website(draft, content.url, content.as_prompt_block())

        workspace = session.get(Workspace, wid)
        locale = (workspace.default_locale if workspace else None) or "fa"

        try:
            job = enqueue_job(
                session,
                tenant_id=tid,
                kind=GpuJobKind.LLM_TEXT,
                workspace_id=wid,
                locale=locale,
                # Priority ahead of the default: someone is sitting in front
                # of the wizard waiting for it.
                priority=10,
                payload={
                    "agent": PipelineStep.BRIEF_ASSISTANT.value,
                    "draft_id": str(draft.id),
                    "locale": locale,
                    "brand": draft.answers.get("brand_name"),
                },
            )
        except QuotaExceededError as exc:
            draft_service.fail_suggestion(draft, "today's GPU quota is used up; try again tomorrow")
            return {"workspace_id": workspace_id, "error": str(exc)}

    return {"workspace_id": workspace_id, "job_id": str(job.id)}


@celery_app.task(name="pipeline.start_media", queue=Queue.PIPELINE.value)
def start_media(tenant_id: str, package_id: str) -> dict:
    """Fan out image and video work after gate 1 has been approved.

    Images and video are queued together; the GPU scheduler is what serialises
    them onto the single card, batching by model (handoff section 6).

    Each distinct visual brief the marketizer produced (handoff section 3 —
    it runs before gate 1, inside the text chain) gets its own gallery of
    ``IMAGE_OPTIONS_PER_PACKAGE`` options, so the FLUX prompt actually reflects
    what an editor approved rather than being generic per-package filler.
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

        tenant = session.get(Tenant, tid)
        prefix = tenant.storage_prefix if tenant else str(tid)

        for visual_brief in package_service.distinct_visual_briefs(session, package):
            aspect_ratio = str(visual_brief.get("aspect_ratio") or "1:1")
            width, height = image_compose.dimensions_for(aspect_ratio)
            prompt = _flux_prompt(visual_brief)
            overlay_text = (
                visual_brief.get("overlay_text")
                if visual_brief.get("render_text_separately", True)
                else None
            )

            for option in range(IMAGE_OPTIONS_PER_PACKAGE):
                asset = MediaAsset(
                    tenant_id=tid,
                    package_id=package.id,
                    kind=MediaKind.IMAGE,
                    storage_key="",
                    prompt=prompt,
                    meta={"visual_brief": visual_brief, "option": option},
                )
                session.add(asset)
                session.flush()
                # Computed up front, deterministically from the asset's own
                # id, so the executor has nowhere to write except here.
                asset.storage_key = storage.tenant_key(
                    prefix, "packages", str(package.id), "images", f"{asset.id}.png"
                )
                job = enqueue_job(
                    session,
                    tenant_id=tid,
                    kind=GpuJobKind.IMAGE_FLUX,
                    workspace_id=package.workspace_id,
                    package_id=package.id,
                    media_asset_id=asset.id,
                    locale=package.locale,
                    payload={
                        "prompt": prompt,
                        "negative_prompt": visual_brief.get("negative_prompt"),
                        "aspect_ratio": aspect_ratio,
                        "width": width,
                        "height": height,
                        "palette": visual_brief.get("palette", []),
                        "overlay_text": overlay_text,
                        "locale": package.locale,
                        "seed": option,
                    },
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


def _flux_prompt(visual_brief: dict) -> str:
    """The text prompt for the image model.

    Never includes ``overlay_text``: it is drawn separately, never fed into
    the diffusion model, because FLUX mangles Persian and Arabic script
    (handoff section 5). Only ``scene`` and the style keywords go in.
    """
    scene = str(visual_brief.get("scene") or "").strip()
    raw_keywords = visual_brief.get("style_keywords") or []
    keywords = [str(keyword).strip() for keyword in raw_keywords if str(keyword).strip()]
    return ", ".join(part for part in [scene, *keywords] if part)


def _voice_chain(video_mode: VideoMode) -> tuple[GpuJobKind, ...]:
    """The GPU steps each video mode needs (handoff section 8).

    ``none`` needs no speech at all. ``voice`` needs one narration track —
    no separate Whisper step: ``execute_tts_job`` synthesizes the narration
    one section at a time and already knows each section's exact text and
    audio duration, which is the entire reason Whisper would otherwise run
    (see ``app.services.subtitle_render``). ``face`` (phase 3) adds lip-sync
    on top of the same narration.
    """
    if video_mode is VideoMode.NONE:
        return ()
    chain: tuple[GpuJobKind, ...] = (GpuJobKind.TTS,)
    if video_mode is VideoMode.FACE:
        chain += (GpuJobKind.LIPSYNC_LATENTSYNC,)
    return chain


@celery_app.task(name="pipeline.media_finished", queue=Queue.PIPELINE.value)
def media_finished(tenant_id: str, package_id: str) -> dict:
    """Move a package to gate 2 once its media is ready.

    Queued by the GPU task once no media job is left pending for this package
    (``dispatcher.package_has_pending_media``). Idempotent: a package that is
    not (or no longer) ``media_generating`` — a duplicate call racing a
    second batch, or an operator who already forced it forward — is a no-op
    rather than an error, since nothing about that is actually wrong.
    """
    tid, pid = uuid.UUID(tenant_id), uuid.UUID(package_id)
    with tenant_session(tid) as session:
        package = package_service.get_package(session, pid)
        if package.status is not PackageStatus.MEDIA_GENERATING:
            return {"package_id": package_id, "status": package.status.value, "skipped": True}
        package_service.transition(package, PackageStatus.SELECTION)
        return {"package_id": package_id, "status": package.status.value}
