"""Liveness and readiness."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app import __version__
from app.core.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz() -> dict:
    """Process is up.  Never touches a dependency, so it cannot flap."""
    return {"status": "ok", "version": __version__, "environment": settings.environment}


@router.get("/readyz")
def readyz(response: Response) -> dict:
    """Dependencies are reachable: Postgres and Redis."""
    checks: dict[str, str] = {}

    from app.db.session import engine

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001 - the message is the useful part
        checks["database"] = f"error: {type(exc).__name__}"
        logger.warning("readiness: database unreachable", exc_info=True)

    try:
        import redis

        redis.Redis.from_url(str(settings.redis_url), socket_connect_timeout=2).ping()
        checks["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["redis"] = f"error: {type(exc).__name__}"
        logger.warning("readiness: redis unreachable", exc_info=True)

    ready = all(value == "ok" for value in checks.values())
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ready" if ready else "degraded", "checks": checks}
