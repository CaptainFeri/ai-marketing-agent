"""Row level security plumbing (decision D8).

Every tenant table carries a policy comparing ``tenant_id`` against
``current_setting('app.tenant_id')``.  This module is what sets that GUC.

``SET LOCAL`` is used deliberately: the value lives exactly as long as the
surrounding transaction, so a pooled connection can never leak one tenant's
scope into the next request.  When the GUC is unset, ``current_setting(...,
true)`` returns NULL, the policy predicate is NULL, and the query returns
nothing — the failure mode is an empty result, never a cross-tenant read.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import SessionLocal, SystemSessionLocal

TENANT_GUC = "app.tenant_id"


def bind_tenant(session: Session, tenant_id: uuid.UUID | str) -> None:
    """Scope an already-open session to one tenant for the current transaction.

    ``set_config`` is used rather than ``SET LOCAL`` so the value can be passed
    as a bound parameter; ``SET LOCAL`` only accepts literals, which would mean
    interpolating a value into SQL.
    """
    session.execute(
        text("SELECT set_config(:key, :value, true)"),
        {"key": TENANT_GUC, "value": str(tenant_id)},
    )


def current_tenant(session: Session) -> uuid.UUID | None:
    value = session.execute(
        text("SELECT current_setting(:key, true)"), {"key": TENANT_GUC}
    ).scalar()
    return uuid.UUID(value) if value else None


@contextmanager
def tenant_session(tenant_id: uuid.UUID | str) -> Iterator[Session]:
    """Open a session that can only see ``tenant_id``.

    Commits on success, rolls back on any exception.
    """
    session = SessionLocal()
    try:
        bind_tenant(session, tenant_id)
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def system_session() -> Iterator[Session]:
    """Open a cross-tenant session.

    Only for machinery that is genuinely tenant-agnostic: the GPU scheduler
    choosing between queues, the nightly quota allocator, migrations.  Anything
    serving an API request must use :func:`tenant_session` instead.
    """
    session = SystemSessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
