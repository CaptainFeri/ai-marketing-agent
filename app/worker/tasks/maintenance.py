"""Housekeeping on the ``maintenance`` queue."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from app.db.tenancy import system_session
from app.services.quota import allocate_day
from app.worker.celery_app import celery_app
from app.worker.dispatcher import queue_depth, reclaim_expired_leases
from app.worker.queues import Queue

logger = logging.getLogger(__name__)


@celery_app.task(name="maintenance.reclaim_gpu_leases", queue=Queue.MAINTENANCE.value)
def reclaim_gpu_leases() -> int:
    with system_session() as session:
        return reclaim_expired_leases(session)


@celery_app.task(name="maintenance.allocate_daily_quota", queue=Queue.MAINTENANCE.value)
def allocate_daily_quota(day_offset: int = 0) -> int:
    """Split the day's GPU capacity between tenants (handoff section 7)."""
    day = (datetime.now(UTC) + timedelta(days=day_offset)).date()
    with system_session() as session:
        count = allocate_day(session, day)
    logger.info("daily GPU quota allocated", extra={"day": day.isoformat(), "tenants": count})
    return count


@celery_app.task(name="maintenance.report_queue_depth", queue=Queue.MAINTENANCE.value)
def report_queue_depth() -> dict[str, int]:
    with system_session() as session:
        depth = queue_depth(session)
    logger.info("gpu queue depth", extra=depth)
    return depth
