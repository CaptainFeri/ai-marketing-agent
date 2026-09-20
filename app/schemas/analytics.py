"""Analytics credential and metric payloads (handoff section 7).

The secret fields (the service account key) appear only in
``AnalyticsCredentialCreate`` — what the client sends. Nothing in this
module can hold a decrypted secret in a response; ``AnalyticsCredentialOut``
only ever carries ``public_metadata``, the same pattern
``app.schemas.channel`` uses.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field

from app.db.enums import AnalyticsProvider
from app.schemas.common import ORMModel


class AnalyticsCredentialCreate(BaseModel):
    provider: AnalyticsProvider
    label: str = Field(default="default", min_length=1, max_length=64)
    #: Validated against the provider's own schema
    #: (``app.connectors.analytics_credentials``) before it is encrypted.
    payload: dict[str, Any]
    #: Safe to display later — the GA4 property id, the Search Console site
    #: URL. Never the service account key itself.
    public_metadata: dict[str, Any] = Field(default_factory=dict)


class AnalyticsCredentialOut(ORMModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    provider: AnalyticsProvider
    label: str
    public_metadata: dict[str, Any]
    is_active: bool
    last_used_at: datetime | None
    created_at: datetime


class MetricSnapshotOut(ORMModel):
    id: uuid.UUID
    publication_id: uuid.UUID
    source: str
    captured_for: datetime
    impressions: int | None
    clicks: int | None
    position: float | None
    metrics: dict[str, Any]


class PackageMetricsOut(BaseModel):
    package_id: uuid.UUID
    snapshots: list[MetricSnapshotOut]


class PullMetricsRequest(BaseModel):
    """Manual trigger for a single day — the daily sweep uses "yesterday"
    itself; this is for an operator backfilling a day it missed."""

    day: date | None = None
