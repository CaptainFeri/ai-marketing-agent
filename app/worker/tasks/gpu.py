"""The ``gpu`` queue.  One worker, ``concurrency=1``, owns the card."""

from __future__ import annotations

import logging
import os
import socket
import uuid

from app.agents.executor import execute_standalone_job, execute_step_job
from app.db.enums import GpuJobKind, GpuJobStatus
from app.db.models import GpuJob
from app.db.tenancy import system_session
from app.services.media_jobs import execute_image_job, execute_tts_job
from app.worker.celery_app import celery_app
from app.worker.dispatcher import (
    claim_next_batch,
    complete_job,
    fail_job,
    package_has_pending_jobs,
    reclaim_expired_leases,
    record_switch,
    start_job,
)
from app.worker.gpu_runtime import get_runtime
from app.worker.queues import Queue

logger = logging.getLogger(__name__)


def worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


@celery_app.task(name="gpu.dispatch", queue=Queue.GPU.value, acks_late=True)
def dispatch() -> dict:
    """Claim the next batch and run it to completion.

    Beat fires this every 15 seconds.  Because the queue has a single worker
    at concurrency 1, at most one of these ever runs, which is what keeps the
    card single-owner.  The task returns immediately when nothing is eligible.
    """
    me = worker_id()
    with system_session() as session:
        reclaim_expired_leases(session)
        batch = claim_next_batch(session, me)
        if batch is None:
            return {"claimed": 0}
        job_ids = list(batch.job_ids)
        kind, window = batch.kind, batch.window

    runtime = get_runtime()
    # One model load for the whole batch — the point of batching (section 6).
    switch_seconds = runtime.ensure_window(window, kind)
    if switch_seconds > 0:
        # Feed the real cost back: the scheduler batches against it from here
        # on, replacing whatever the hardware probe estimated at install.
        with system_session() as session:
            record_switch(session, switch_seconds)

    succeeded = failed = 0
    # Packages whose next text step should be queued once the batch is booked.
    advanced: list[tuple[str, str]] = []
    # Packages that may have just finished all their media (image, TTS,
    # lip-sync, ...) and should be checked for gate 2.
    media_touched: list[tuple[str, str]] = []
    for job_id in job_ids:
        with system_session() as session:
            job = session.get(GpuJob, job_id)
            if job is None or job.status not in {GpuJobStatus.LEASED, GpuJobStatus.RUNNING}:
                continue
            start_job(session, job_id)
            payload = dict(job.payload)
            locale = str(payload.get("locale", "*"))
            job_tenant = str(job.tenant_id)
            job_package = str(job.package_id) if job.package_id else None

        gpu_seconds: float
        output: dict[str, object]
        try:
            if kind is GpuJobKind.LLM_TEXT:
                # Text jobs are agent runs: the executor rebuilds the context
                # from the database, runs the agent and stores the validated
                # output where it belongs.
                with system_session() as session:
                    job = session.get(GpuJob, job_id)
                    if job is None:
                        continue
                    if job.step_run_id is not None:
                        step_result = execute_step_job(session, job)
                    else:
                        # An agent that belongs to a workspace rather than a
                        # package — today, the brief assistant.
                        step_result = execute_standalone_job(session, job)
                        job_package = None
                gpu_seconds = step_result.seconds
                output = {
                    "step": step_result.step.value,
                    "attempts": step_result.attempts,
                    "model": step_result.model,
                }
            elif kind is GpuJobKind.IMAGE_FLUX:
                # Likewise for images: generate the base picture, draw any
                # overlay text separately (handoff section 5), composite and
                # store — see app.services.media_jobs.
                with system_session() as session:
                    job = session.get(GpuJob, job_id)
                    if job is None:
                        continue
                    image_result = execute_image_job(session, job)
                gpu_seconds = image_result.gpu_seconds
                output = {
                    "storage_key": image_result.storage_key,
                    "width": image_result.width,
                    "height": image_result.height,
                }
            elif kind is GpuJobKind.TTS:
                # Narration is a dedicated real backend too (espeak-ng by
                # default), not the generic simulated fallback below — see
                # app.services.tts_backend.
                with system_session() as session:
                    job = session.get(GpuJob, job_id)
                    if job is None:
                        continue
                    tts_result = execute_tts_job(session, job)
                gpu_seconds = tts_result.gpu_seconds
                output = {
                    "audio_asset_id": tts_result.audio_asset_id,
                    "subtitle_asset_id": tts_result.subtitle_asset_id,
                    "duration_seconds": tts_result.duration_seconds,
                }
            else:
                media_result = runtime.run(kind, payload)
                gpu_seconds = media_result.gpu_seconds
                output = media_result.output
        except Exception as exc:  # noqa: BLE001 - the failure is recorded, not swallowed
            logger.exception("gpu job failed", extra={"job_id": str(job_id)})
            with system_session() as session:
                fail_job(session, job_id, f"{type(exc).__name__}: {exc}")
            failed += 1
            if job_package and kind is not GpuJobKind.LLM_TEXT:
                media_touched.append((job_tenant, job_package))
            continue

        with system_session() as session:
            complete_job(
                session,
                job_id,
                gpu_seconds=gpu_seconds,
                result=output,
                locale=locale,
            )
        succeeded += 1

        if job_package:
            if kind is GpuJobKind.LLM_TEXT:
                advanced.append((job_tenant, job_package))
            else:
                media_touched.append((job_tenant, job_package))

    # Chaining happens after the batch so a slow pipeline task cannot hold
    # the card, and so a failed booking does not queue work twice.
    if advanced:
        from app.worker.tasks.pipeline import advance_text

        for tenant_id, package_id in dict.fromkeys(advanced):
            advance_text.delay(tenant_id, package_id)

    if media_touched:
        from app.worker.tasks.pipeline import media_finished

        for tenant_id, package_id in dict.fromkeys(media_touched):
            with system_session() as session:
                still_pending = package_has_pending_jobs(
                    session, uuid.UUID(package_id), exclude_kind=GpuJobKind.LLM_TEXT
                )
            if not still_pending:
                media_finished.delay(tenant_id, package_id)

    logger.info(
        "gpu batch finished",
        extra={
            "batch_id": str(batch.batch_id),
            "kind": kind.value,
            "succeeded": succeeded,
            "failed": failed,
            "switch_seconds": switch_seconds,
        },
    )
    return {
        "claimed": len(job_ids),
        "succeeded": succeeded,
        "failed": failed,
        "kind": kind.value,
        "window": window.value,
    }
