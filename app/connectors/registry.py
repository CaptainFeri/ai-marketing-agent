"""Which connector class handles which channel."""

from __future__ import annotations

from collections.abc import Callable

from app.connectors.base import Connector
from app.connectors.telegram import TelegramConnector
from app.connectors.wordpress import WordPressConnector
from app.db.enums import Channel

#: A constructor per channel rather than ``type[Connector]``: mypy's support
#: for ``type[SomeProtocol]`` is limited, and what is actually meant here —
#: "something callable with no arguments that returns a Connector" — is
#: exactly what ``Callable[[], Connector]`` says.
#:
#: Kept in step with ``app.connectors.credentials.CREDENTIAL_SCHEMAS`` by
#: ``tests/test_connectors.py`` — a channel present in one but not the other
#: would mean either a credential nothing can publish with, or a connector
#: nothing can ever configure.
CONNECTORS: dict[Channel, Callable[[], Connector]] = {
    Channel.WORDPRESS: WordPressConnector,
    Channel.TELEGRAM: TelegramConnector,
}


def build_connector(channel: Channel) -> Connector:
    connector_class = CONNECTORS.get(channel)
    if connector_class is None:
        from app.core.errors import AppError

        raise AppError(
            f"no connector is implemented for {channel.value!r} yet",
            details={"channel": channel.value, "implemented": [c.value for c in CONNECTORS]},
        )
    return connector_class()
