"""GPU quota and queue visibility (handoff sections 6 and 7)."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import Principal, get_db, get_principal
from app.db.tenancy import system_session
from app.schemas.gpu import QuotaStatus, WindowStateOut
from app.services import quota as quota_service
from app.worker.dispatcher import queue_depth

router = APIRouter(prefix="/gpu", tags=["gpu"])


@router.get("/quota", response_model=QuotaStatus)
def my_quota(
    day: date | None = Query(default=None),
    locale: str = Query(default="*"),
    session: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> QuotaStatus:
    """Today's share, and how many packages of each shape it still buys."""
    return quota_service.quota_status(session, principal.tenant_id, day, locale)


@router.get("/window", response_model=WindowStateOut)
def window_state(_: Principal = Depends(get_principal)) -> WindowStateOut:
    """Which model family the card currently holds, and what is waiting.

    Deliberately global rather than per tenant: on a single GPU the queue
    depth is what explains a customer's wait, so hiding it would only make
    the panel confusing.  No tenant-identifying detail is exposed.
    """
    from sqlalchemy import select

    from app.db.models import GpuWindowState

    with system_session() as session:
        state = session.scalars(select(GpuWindowState).limit(1)).one_or_none()
        depth = queue_depth(session)
        return WindowStateOut(
            current_window=state.current_window if state else None,
            current_kind=state.current_kind if state else None,
            switched_at=state.switched_at if state else None,
            switch_count_today=state.switch_count_today if state else 0,
            pending_by_window=depth,
        )
