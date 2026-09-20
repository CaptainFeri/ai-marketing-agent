"""Channel connectors (handoff section 10).

Each connector turns one approved, selected ``Variant`` into a live post on
one channel. ``app.services.publishing`` is what calls these — gathering the
content, decrypting the credential, retrying on failure, and updating the
``Publication`` row; a connector itself is a pure "here is the content, here
is the credential, publish it or raise" boundary.
"""

from app.connectors.base import (
    Connector,
    ConnectorError,
    MediaForPublish,
    PublishContent,
    PublishResult,
)
from app.connectors.credentials import (
    CREDENTIAL_SCHEMAS,
    CredentialPayload,
    CredentialValidationError,
    TelegramCredential,
    WordPressCredential,
    validate_credential,
)
from app.connectors.registry import CONNECTORS, build_connector

__all__ = [
    "CONNECTORS",
    "CREDENTIAL_SCHEMAS",
    "Connector",
    "ConnectorError",
    "CredentialPayload",
    "CredentialValidationError",
    "MediaForPublish",
    "PublishContent",
    "PublishResult",
    "TelegramCredential",
    "WordPressCredential",
    "build_connector",
    "validate_credential",
]
