"""Storing and reading channel credentials (handoff section 10, decision D8).

Nothing here ever hands a caller the decrypted payload except
:func:`decrypt_for_publish`, which the publishing service calls immediately
before a connector needs it and never logs, stores, or returns over the API.
Every other function works with ``ChannelCredential.public_metadata`` —
whatever is safe to show a customer (a site URL, a channel handle) — kept
deliberately separate from the encrypted payload at write time so there is no
field to accidentally serialise into an API response later.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.credentials import CredentialPayload, validate_credential
from app.core.crypto import decrypt_payload, encrypt_payload
from app.core.errors import ConflictError, NotFoundError
from app.db.enums import Channel
from app.db.models import ChannelCredential


def create_credential(
    session: Session,
    tenant_id: uuid.UUID,
    workspace_id: uuid.UUID,
    channel: Channel,
    payload: dict,
    *,
    label: str = "default",
    public_metadata: dict | None = None,
) -> ChannelCredential:
    """Validate and store one credential, encrypted.

    Raises whatever the channel's own schema raises (a ``ValidationError``)
    on a malformed payload — checked before anything touches the database,
    so a typo in a token never gets encrypted and stored only to fail on the
    first publish attempt.
    """
    validate_credential(channel, payload)

    existing = session.scalars(
        select(ChannelCredential).where(
            ChannelCredential.workspace_id == workspace_id,
            ChannelCredential.channel == channel,
            ChannelCredential.label == label,
        )
    ).one_or_none()
    if existing is not None:
        raise ConflictError(
            f"a {channel.value!r} credential labelled {label!r} already exists "
            "for this workspace; delete it first or use a different label"
        )

    credential = ChannelCredential(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        channel=channel,
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
) -> ChannelCredential:
    credential = session.get(ChannelCredential, credential_id)
    if credential is None or credential.workspace_id != workspace_id:
        raise NotFoundError("channel credential not found")
    return credential


def active_credential_for(
    session: Session, workspace_id: uuid.UUID, channel: Channel, label: str = "default"
) -> ChannelCredential:
    credential = session.scalars(
        select(ChannelCredential).where(
            ChannelCredential.workspace_id == workspace_id,
            ChannelCredential.channel == channel,
            ChannelCredential.label == label,
            ChannelCredential.is_active.is_(True),
        )
    ).one_or_none()
    if credential is None:
        raise NotFoundError(
            f"no active {channel.value!r} credential is configured for this workspace"
        )
    return credential


def active_credential_or_none(
    session: Session, workspace_id: uuid.UUID, channel: Channel, label: str = "default"
) -> ChannelCredential | None:
    """Same lookup as :func:`active_credential_for`, but ``None`` rather
    than raising when nothing is configured — for callers like the social
    insights sweep (``app.services.analytics``) where an unconfigured
    channel is the normal case, not an error to fail the whole sweep on."""
    return session.scalars(
        select(ChannelCredential).where(
            ChannelCredential.workspace_id == workspace_id,
            ChannelCredential.channel == channel,
            ChannelCredential.label == label,
            ChannelCredential.is_active.is_(True),
        )
    ).one_or_none()


def deactivate_credential(session: Session, credential: ChannelCredential) -> None:
    """Soft-delete: kept for audit (which publications used it), just no
    longer offered to new publish attempts."""
    credential.is_active = False


def decrypt_for_publish(credential: ChannelCredential) -> CredentialPayload:
    """The one place a credential is actually decrypted.

    Called immediately before a connector needs it; the result never leaves
    the calling function, is never logged and is never put on an ORM object
    that might get serialised.
    """
    raw = decrypt_payload(credential.encrypted_payload)
    payload = validate_credential(credential.channel, raw)
    credential.last_used_at = datetime.now(UTC)
    return payload
