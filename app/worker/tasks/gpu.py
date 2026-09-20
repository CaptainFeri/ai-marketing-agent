"""The ``gpu`` queue.  One worker, ``concurrency=1``, owns the card."""

from __future__ import annotations

import logging
import os
import socket

from app.db.enums import GpuJobStatus
from app.db.models import GpuJob
from app.db.tenancy import system_session
from app.worker.celery_app import celery_app
from app.worker.dispatcher import (
    claim_next_batch,
    complete_job,
    fail_job,
    reclaim_expired_leases,
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

    succeeded = failed = 0
    for job_id in job_ids:
        with system_session() as session:
            job = session.get(GpuJob, job_id)
            if job is None or job.status not in {GpuJobStatus.LEASED, GpuJobStatus.RUNNING}:
                continue
            start_job(session, job_id)
            payload = dict(job.payload)
            locale = str(payload.get("locale", "*"))

        try:
            result = runtime.run(kind, payload)
        except Exception as exc:  # noqa: BLE001 - the failure is recorded, not swallowed
            logger.exception("gpu job failed", extra={"job_id": str(job_id)})
            with system_session() as session:
                fail_job(session, job_id, f"{type(exc).__name__}: {exc}")
            failed += 1
            continue

        with system_session() as session:
            complete_job(
                session,
                job_id,
                gpu_seconds=result.gpu_seconds,
                result=result.output,
                locale=locale,
            )
        succeeded += 1

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
