"""Analytics credentials and the per-package metrics view (handoff section 7).

``public_metadata`` never carries the service account key — see
``app.schemas.analytics`` and ``app.connectors.analytics_credentials`` for
why, same posture ``app.api.v1.channels`` takes toward publish credentials.
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import Principal, assert_workspace_role, get_db, get_principal
from app.db.enums import Role
from app.db.models import AnalyticsCredential, MetricSnapshot, Publication
from app.schemas.analytics import (
    AnalyticsCredentialCreate,
    AnalyticsCredentialOut,
    MetricSnapshotOut,
    PackageMetricsOut,
    PullMetricsRequest,
)
from app.services import analytics, analytics_credentials
from app.services import packages as package_service

credentials_router = APIRouter(
    prefix="/workspaces/{workspace_id}/analytics-credentials", tags=["analytics"]
)
metrics_router = APIRouter(prefix="/packages/{package_id}/metrics", tags=["analytics"])


@credentials_router.get("", response_model=list[AnalyticsCredentialOut])
def list_credentials(
    workspace_id: uuid.UUID,
    session: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> list[AnalyticsCredentialOut]:
    assert_workspace_role(principal, workspace_id, Role.EDITOR)
    rows = session.scalars(
        select(AnalyticsCredential).where(AnalyticsCredential.workspace_id == workspace_id)
    ).all()
    return [AnalyticsCredentialOut.model_validate(row) for row in rows]


@credentials_router.post(
    "", response_model=AnalyticsCredentialOut, status_code=status.HTTP_201_CREATED
)
def create_credential(
    workspace_id: uuid.UUID,
    payload: AnalyticsCredentialCreate,
    session: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> AnalyticsCredentialOut:
    """Store one analytics credential, encrypted.

    Restricted to admins, the same bar ``POST .../credentials`` sets for a
    publish credential — a Google service account key is just as sensitive
    as a WordPress application password.
    """
    assert_workspace_role(principal, workspace_id, Role.ADMIN)
    credential = analytics_credentials.create_credential(
        session,
        principal.tenant_id,
        workspace_id,
        payload.provider,
        payload.payload,
        label=payload.label,
        public_metadata=payload.public_metadata,
    )
    return AnalyticsCredentialOut.model_validate(credential)


@credentials_router.delete("/{credential_id}", status_code=status.HTTP_204_NO_CONTENT)
def deactivate_credential(
    workspace_id: uuid.UUID,
    credential_id: uuid.UUID,
    session: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> None:
    assert_workspace_role(principal, workspace_id, Role.ADMIN)
    credential = analytics_credentials.get_credential(session, workspace_id, credential_id)
    analytics_credentials.deactivate_credential(session, credential)


@credentials_router.post("/pull", response_model=dict)
def pull_now(
    workspace_id: uuid.UUID,
    payload: PullMetricsRequest,
    session: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict:
    """Backfill one day's metrics on demand, run inline.

    Not queued: this is an operator re-running a day the nightly sweep
    missed, one workspace at a time — small, occasional, and the caller
    wants to see the result immediately rather than poll for it.
    """
    assert_workspace_role(principal, workspace_id, Role.ADMIN)
    day: date | None = payload.day
    written = analytics.pull_metrics_for_workspace(session, workspace_id, day)
    return {"snapshots_written": written}


@metrics_router.get("", response_model=PackageMetricsOut)
def get_package_metrics(
    package_id: uuid.UUID,
    session: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> PackageMetricsOut:
    """The basic dashboard view (handoff section 7): every daily snapshot
    recorded for any of this package's publications, newest first."""
    package = package_service.get_package(session, package_id)
    assert_workspace_role(principal, package.workspace_id, Role.VIEWER)

    rows = session.scalars(
        select(MetricSnapshot)
        .join(Publication, MetricSnapshot.publication_id == Publication.id)
        .where(Publication.package_id == package_id)
        .order_by(MetricSnapshot.captured_for.desc())
    ).all()
    return PackageMetricsOut(
        package_id=package_id,
        snapshots=[MetricSnapshotOut.model_validate(row) for row in rows],
    )
