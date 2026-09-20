"""Engines and session factories.

Two engines on purpose:

``engine``
    The ordinary, tenant-scoped connection.  Its database role must **not**
    hold ``BYPASSRLS``, so a bug that forgets to scope a query returns zero
    rows instead of another customer's data.

``system_engine``
    Used only by cross-tenant machinery — the GPU scheduler, nightly
    maintenance, the quota allocator.  Its role holds ``BYPASSRLS``.

Nothing else should reach for ``system_engine``; ``app.db.tenancy`` is the
only sanctioned way to open a session.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

_COMMON_KWARGS: dict[str, Any] = {
    "pool_pre_ping": True,
    "echo": settings.db_echo,
    "future": True,
}


def _make_engine(url: str, **overrides: Any) -> Engine:
    return create_engine(url, **{**_COMMON_KWARGS, **overrides})


engine: Engine = _make_engine(
    str(settings.database_url),
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
)

system_engine: Engine = _make_engine(
    settings.effective_system_database_url,
    # Only a handful of background processes use it.
    pool_size=2,
    max_overflow=2,
)

SessionLocal = sessionmaker(bind=engine, class_=Session, expire_on_commit=False, future=True)
SystemSessionLocal = sessionmaker(
    bind=system_engine, class_=Session, expire_on_commit=False, future=True
)


def dispose_engines() -> None:
    """Close both pools.  Called on application shutdown and between tests."""
    engine.dispose()
    system_engine.dispose()
