"""Storing and reading Search Console / GA4 credentials (handoff section 7).

Same shape and the same reasoning as ``app.services.channel_credentials``:
nothing here ever hands a caller the decrypted payload except
:func:`decrypt_for_read`, called immediately before a metrics pull needs it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.analytics_credentials import (
    AnalyticsCredentialPayload,
    validate_analytics_credential,
)
from app.core.crypto import decrypt_payload, encrypt_payload
from app.core.errors import ConflictError, NotFoundError
from app.db.enums import AnalyticsProvider
from app.db.models import AnalyticsCredential


def create_credential(
    session: Session,
    tenant_id: uuid.UUID,
    workspace_id: uuid.UUID,
    provider: AnalyticsProvider,
    payload: dict,
    *,
    label: str = "default",
    public_metadata: dict | None = None,
) -> AnalyticsCredential:
    """Validate and store one credential, encrypted.

    Raises whatever the provider's own schema raises on a malformed
    payload — checked before anything touches the database, so a pasted key
    file with a missing field never gets encrypted and stored only to fail
    on the first metrics pull.
    """
    validate_analytics_credential(provider, payload)

    existing = session.scalars(
        select(AnalyticsCredential).where(
            AnalyticsCredential.workspace_id == workspace_id,
            AnalyticsCredential.provider == provider,
            AnalyticsCredential.label == label,
        )
    ).one_or_none()
    if existing is not None:
        raise ConflictError(
            f"a {provider.value!r} credential labelled {label!r} already exists "
            "for this workspace; delete it first or use a different label"
        )

    credential = AnalyticsCredential(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        provider=provider,
        label=label,
        encrypted_payload=encrypt_payload(payload),
        public_metadata=public_metadata or {},
        is_active=True,
    )
    session.add(credential)
    session.flush()
    return credential


def get_credential(
    session: Session, workspace_id: uuid.UUID, credential_id: uuid.UUID
) -> AnalyticsCredential:
    credential = session.get(AnalyticsCredential, credential_id)
    if credential is None or credential.workspace_id != workspace_id:
        raise NotFoundError("analytics credential not found")
    return credential


def active_credential_for(
    session: Session,
    workspace_id: uuid.UUID,
    provider: AnalyticsProvider,
    label: str = "default",
) -> AnalyticsCredential | None:
    """``None`` rather than raising: an unconfigured provider is the normal
    case (most workspaces will only ever wire up one of the two), and the
    metrics puller needs to skip it quietly, not fail the whole sweep."""
    return session.scalars(
        select(AnalyticsCredential).where(
            AnalyticsCredential.workspace_id == workspace_id,
            AnalyticsCredential.provider == provider,
            AnalyticsCredential.label == label,
            AnalyticsCredential.is_active.is_(True),
        )
    ).one_or_none()


def deactivate_credential(session: Session, credential: AnalyticsCredential) -> None:
    """Soft-delete: kept for audit, just no longer offered to a metrics pull."""
    credential.is_active = False


def decrypt_for_read(credential: AnalyticsCredential) -> AnalyticsCredentialPayload:
    """The one place a credential is actually decrypted. Never logged, never
    put on an ORM object, never returned over the API."""
    raw = decrypt_payload(credential.encrypted_payload)
    payload = validate_analytics_credential(credential.provider, raw)
    credential.last_used_at = datetime.now(UTC)
    return payload
