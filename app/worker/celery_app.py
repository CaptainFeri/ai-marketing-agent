"""Celery application (decision D5 — Temporal is a phase 4 migration)."""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab
from celery.signals import setup_logging

from app.core.config import settings
from app.core.logging import configure_logging
from app.worker.queues import Queue

celery_app = Celery(
    "ai_marketing_agent",
    broker=settings.broker_url,
    backend=settings.result_backend,
    include=[
        "app.worker.tasks.gpu",
        "app.worker.tasks.pipeline",
        "app.worker.tasks.maintenance",
        "app.worker.tasks.publish",
    ],
)

celery_app.conf.update(
    task_default_queue=Queue.PIPELINE.value,
    task_acks_late=True,
    # A GPU job that a dead worker was holding must not be re-delivered
    # blindly; the lease reclaimer in maintenance decides when to requeue it.
    task_reject_on_worker_lost=False,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    result_expires=60 * 60 * 24 * 7,
    timezone="UTC",
    enable_utc=True,
    task_routes={
        "gpu.*": {"queue": Queue.GPU.value},
        "pipeline.*": {"queue": Queue.PIPELINE.value},
        "publish.*": {"queue": Queue.PUBLISH.value},
        "maintenance.*": {"queue": Queue.MAINTENANCE.value},
        "media_cpu.*": {"queue": Queue.MEDIA_CPU.value},
    },
    beat_schedule={
        # The scheduler tick: claim and run the next batch.  Short interval
        # because the task exits immediately when the GPU is busy.
        "gpu-dispatch": {
            "task": "gpu.dispatch",
            "schedule": 15.0,
            "options": {"queue": Queue.GPU.value, "expires": 14},
        },
        "reclaim-gpu-leases": {
            "task": "maintenance.reclaim_gpu_leases",
            "schedule": 300.0,
            "options": {"queue": Queue.MAINTENANCE.value},
        },
        # Split tomorrow's capacity just after midnight UTC.
        "allocate-daily-quota": {
            "task": "maintenance.allocate_daily_quota",
            "schedule": crontab(hour="0", minute="5"),
            "options": {"queue": Queue.MAINTENANCE.value},
        },
        # Handoff section 3, step 6: find what is due and fire the connector.
        "publish-dispatch": {
            "task": "publish.dispatch",
            "schedule": 60.0,
            "options": {"queue": Queue.PUBLISH.value, "expires": 55},
        },
    },
)


@setup_logging.connect
def _configure_celery_logging(**_: object) -> None:
    """Use the application's JSON logging instead of Celery's own format."""
    configure_logging()
