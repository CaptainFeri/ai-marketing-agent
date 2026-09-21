"""What a channel connector is, and what it is handed to publish.

Every connector turns one approved, selected ``Variant`` into a live post.
The content it works from is intentionally narrow — a plain dataclass built
from the database, not the ORM rows themselves — so a connector can be
called with fixtures in a test and never needs a session.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from app.connectors.credentials import CredentialPayload
from app.db.enums import Channel


class ConnectorError(RuntimeError):
    """A publish attempt failed.

    Raised for anything the channel itself rejected or that the network
    prevented — the publishing service is what decides retry versus give up
    (handoff section 3, step 6: three attempts, then an operator is
    alerted), not the connector.
    """


@dataclass(frozen=True)
class MediaForPublish:
    """One selected, already-generated asset, read once from storage.

    Carries the bytes themselves rather than a URL: the publishing service
    already has them (it is what selects and reads the asset), and handing
    them straight to the connector means WordPress's own upload endpoint
    receives real image data with no dependency on our storage being
    reachable from wherever the request happens to run, and no risk of a
    presigned URL expiring mid-publish.
    """

    data: bytes
    mime_type: str
    filename: str
    alt_text: str | None = None
    width: int | None = None
    height: int | None = None
    #: A presigned GET URL for the same bytes, when the storage backend can
    #: produce one. WordPress and Telegram both accept a raw upload and
    #: never need this; a connector whose own API expects a fetchable URL
    #: instead of a request body (Instagram's Content Publishing API) reads
    #: this and raises if it is missing rather than guessing at one.
    url: str | None = None


@dataclass(frozen=True)
class PublishContent:
    """Everything a connector needs, gathered once by the publishing service.

    ``article`` carries the SEO/GEO fields (``meta_title``, ``slug``,
    ``schema_org``, ...) assembled by ``app.services.packages.assemble_article``
    — present for channels that read from the finished article (WordPress),
    absent for a channel whose variant stands entirely on its own (Telegram).
    """

    title: str
    body: str
    hook: str | None
    hashtags: tuple[str, ...]
    call_to_action: str | None
    article: dict | None
    media: tuple[MediaForPublish, ...]
    utm: dict[str, str]


@dataclass(frozen=True)
class PublishResult:
    external_id: str
    external_url: str | None
    #: Anything worth keeping for diagnostics that is not the two fields
    #: above — the raw response's post type, or which SEO fields were
    #: actually accepted, for instance.
    details: dict = field(default_factory=dict)


class Connector(Protocol):
    channel: Channel

    def publish(self, content: PublishContent, credential: CredentialPayload) -> PublishResult:
        """Publish once. Raises :class:`ConnectorError` on any failure."""
        ...
