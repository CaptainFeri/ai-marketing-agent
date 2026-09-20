"""Channel credentials (handoff section 10, decision D8).

Nothing here ever returns a decrypted payload. ``ChannelCredentialOut``
carries ``public_metadata`` only — whatever the caller supplied at creation
time as safe to show, never the encrypted secret itself.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import Principal, assert_workspace_role, get_db, get_principal
from app.db.enums import Role
from app.db.models import ChannelCredential
from app.schemas.channel import ChannelCredentialCreate, ChannelCredentialOut
from app.services import channel_credentials

router = APIRouter(prefix="/workspaces/{workspace_id}/credentials", tags=["channels"])


@router.get("", response_model=list[ChannelCredentialOut])
def list_credentials(
    workspace_id: uuid.UUID,
    session: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> list[ChannelCredentialOut]:
    assert_workspace_role(principal, workspace_id, Role.EDITOR)
    rows = session.scalars(
        select(ChannelCredential).where(ChannelCredential.workspace_id == workspace_id)
    ).all()
    return [ChannelCredentialOut.model_validate(row) for row in rows]


@router.post("", response_model=ChannelCredentialOut, status_code=status.HTTP_201_CREATED)
def create_credential(
    workspace_id: uuid.UUID,
    payload: ChannelCredentialCreate,
    session: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> ChannelCredentialOut:
    """Store one channel credential, encrypted.

    Restricted to admins: a WordPress application password or a Telegram bot
    token is what lets the platform post as the customer, which is a higher
    bar than the editor role that runs the rest of the pipeline.
    """
    assert_workspace_role(principal, workspace_id, Role.ADMIN)
    credential = channel_credentials.create_credential(
        session,
        principal.tenant_id,
        workspace_id,
        payload.channel,
        payload.payload,
        label=payload.label,
        public_metadata=payload.public_metadata,
    )
    return ChannelCredentialOut.model_validate(credential)


@router.delete("/{credential_id}", status_code=status.HTTP_204_NO_CONTENT)
def deactivate_credential(
    workspace_id: uuid.UUID,
    credential_id: uuid.UUID,
    session: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> None:
    """Deactivate a credential.

    Soft delete: the row (and which publications used it) stays for audit;
    it is simply no longer offered to a new publish attempt.
    """
    assert_workspace_role(principal, workspace_id, Role.ADMIN)
    credential = channel_credentials.get_credential(session, workspace_id, credential_id)
    channel_credentials.deactivate_credential(session, credential)
