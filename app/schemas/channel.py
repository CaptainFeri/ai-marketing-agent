"""Channel credential payloads.

The secret fields (application password, bot token) appear only in
``ChannelCredentialCreate`` — what the client sends. Nothing in this module
can hold a decrypted secret in a response; ``ChannelCredentialOut`` only ever
carries ``public_metadata``, which is what the caller supplied separately as
safe-to-show.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.db.enums import Channel
from app.schemas.common import ORMModel


class ChannelCredentialCreate(BaseModel):
    channel: Channel
    label: str = Field(default="default", min_length=1, max_length=64)
    #: Validated against the channel's own schema
    #: (``app.connectors.credentials``) before it is ever encrypted.
    payload: dict[str, Any]
    #: Safe to display later — a site URL, a channel handle. Never the
    #: secret itself.
    public_metadata: dict[str, Any] = Field(default_factory=dict)


class ChannelCredentialOut(ORMModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    channel: Channel
    label: str
    public_metadata: dict[str, Any]
    is_active: bool
    expires_at: datetime | None
    last_used_at: datetime | None
    created_at: datetime
