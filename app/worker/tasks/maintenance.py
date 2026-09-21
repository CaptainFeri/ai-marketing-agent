"""Housekeeping on the ``maintenance`` queue."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.db.models import Workspace
from app.db.tenancy import system_session
from app.services import analytics
from app.services.backup import run_backup
from app.services.quota import allocate_day
from app.services.storage import get_backend
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


@celery_app.task(name="maintenance.pull_daily_metrics", queue=Queue.MAINTENANCE.value)
def pull_daily_metrics(day_offset: int = -1) -> dict[str, int]:
    """Pull yesterday's Search Console/GA4 numbers for every active
    workspace (handoff section 7, step 7). One workspace's failure is
    logged and does not stop the sweep — ``pull_metrics_for_workspace``
    already isolates a single provider's failure the same way.
    """
    day = (datetime.now(UTC) + timedelta(days=day_offset)).date()
    written_total = 0
    workspaces_swept = 0
    with system_session() as session:
        workspace_ids = session.scalars(
            select(Workspace.id).where(Workspace.is_active.is_(True))
        ).all()
        for workspace_id in workspace_ids:
            try:
                written_total += analytics.pull_metrics_for_workspace(session, workspace_id, day)
                workspaces_swept += 1
            except Exception:  # noqa: BLE001 - one workspace must not sink the sweep
                logger.exception(
                    "metrics sweep failed for workspace", extra={"workspace_id": str(workspace_id)}
                )
    logger.info(
        "daily metrics pulled",
        extra={"day": day.isoformat(), "workspaces": workspaces_swept, "snapshots": written_total},
    )
    return {"workspaces": workspaces_swept, "snapshots": written_total}


@celery_app.task(name="maintenance.run_daily_backup", queue=Queue.MAINTENANCE.value)
def run_daily_backup() -> dict[str, object]:
    """Handoff section 7, week 9: daily Postgres + MinIO backup.

    A failure here is loud, not swallowed — unlike the metrics sweep, there
    is no "per workspace" to isolate a failure to, and a silently-failing
    backup is worse than an alerting worker.
    """
    result = run_backup(backend=get_backend())
    return {
        "day": result.day.isoformat(),
        "postgres_bytes": result.postgres_dump_bytes,
        "objects_backed_up": result.objects_backed_up,
        "objects_bytes": result.objects_bytes,
        "pruned_days": result.pruned_days,
    }
