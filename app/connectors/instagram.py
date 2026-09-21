"""Instagram via the Graph API's Content Publishing flow (handoff section
10/11, phase 2).

Three calls, always in this order:

1. ``POST /{ig-user-id}/media`` — create a container from an ``image_url``.
   Instagram fetches the image itself; it does not accept an uploaded body
   the way WordPress's media endpoint does, which is why
   :class:`~app.connectors.base.MediaForPublish` carries a presigned URL as
   well as the raw bytes (``app.services.publishing.gather_content``
   populates it from ``StorageBackend.url()``). That URL has to be reachable
   from Meta's own servers — a MinIO bound to ``127.0.0.1`` only (this
   platform's default, ``docker-compose.yml``) will not work; production
   needs a public bucket or a CDN in front of it, an operator-side decision
   this connector cannot make for them.
2. Poll ``GET /{container-id}?fields=status_code`` until ``FINISHED``.
   Instagram processes the fetched image asynchronously; publishing before
   it finishes is rejected.
3. ``POST /{ig-user-id}/media_publish`` — turn the finished container into
   a live post.

No text-only posts: Instagram's feed API has no such thing, so a variant
with no selected image is a clear error here rather than a confusing one
from Meta's own API.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

import httpx

from app.connectors.base import ConnectorError, CredentialPayload, PublishContent, PublishResult
from app.connectors.credentials import InstagramCredential
from app.db.enums import Channel

logger = logging.getLogger(__name__)

#: Bump when Meta deprecates this version; the Graph API keeps each version
#: live for about two years, so this is a "revisit occasionally" constant,
#: not a "pin forever" one.
API_VERSION = "v21.0"
BASE_URL = f"https://graph.facebook.com/{API_VERSION}"
CAPTION_LIMIT = 2200
_TIMEOUT = 30.0
#: How long to wait for Meta to finish fetching and processing the image
#: before giving up — generous, since a slow container is common for a
#: freshly generated image on a cold CDN edge.
_POLL_ATTEMPTS = 20
_POLL_INTERVAL_SECONDS = 3.0


class InstagramConnector:
    channel = Channel.INSTAGRAM

    def __init__(
        self,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        # Tests inject a client built on httpx.MockTransport, and a no-op
        # sleep so the polling loop does not actually wait.
        self._client = client
        self._sleep = sleep

    def publish(self, content: PublishContent, credential: CredentialPayload) -> PublishResult:
        if not isinstance(credential, InstagramCredential):
            raise ConnectorError(
                f"InstagramConnector needs an InstagramCredential, got {type(credential).__name__}"
            )
        if not content.media:
            raise ConnectorError("Instagram needs at least one selected image; none was given")
        image = content.media[0]
        if not image.url:
            raise ConnectorError(
                "the selected image has no publicly reachable URL — is the storage "
                "backend configured with a public endpoint?"
            )

        caption = _compose_caption(content)
        client = self._client or httpx.Client(timeout=_TIMEOUT)
        owns_client = self._client is None

        try:
            container_id = self._create_container(client, credential, image.url, caption)
            self._wait_until_finished(client, credential, container_id)
            media_id = self._publish_container(client, credential, container_id)
        except httpx.HTTPError as exc:
            raise ConnectorError(f"Instagram request failed: {type(exc).__name__}: {exc}") from exc
        finally:
            if owns_client:
                client.close()

        return PublishResult(
            external_id=media_id,
            external_url=f"https://www.instagram.com/p/{media_id}/",
            details={"container_id": container_id},
        )

    def _create_container(
        self, client: httpx.Client, credential: InstagramCredential, image_url: str, caption: str
    ) -> str:
        response = client.post(
            f"{BASE_URL}/{credential.ig_user_id}/media",
            data={
                "image_url": image_url,
                "caption": caption,
                "access_token": credential.access_token,
            },
        )
        body = _raise_for_graph_error(response, "creating the media container")
        return str(body["id"])

    def _wait_until_finished(
        self, client: httpx.Client, credential: InstagramCredential, container_id: str
    ) -> None:
        for attempt in range(1, _POLL_ATTEMPTS + 1):
            response = client.get(
                f"{BASE_URL}/{container_id}",
                params={"fields": "status_code", "access_token": credential.access_token},
            )
            body = _raise_for_graph_error(response, "checking the container status")
            status = body.get("status_code")
            if status == "FINISHED":
                return
            if status == "ERROR":
                raise ConnectorError("Instagram failed to process the image")
            if status == "EXPIRED":
                raise ConnectorError("the media container expired before it could be published")
            if attempt == _POLL_ATTEMPTS:
                raise ConnectorError(
                    f"the media container was still {status!r} after "
                    f"{_POLL_ATTEMPTS * _POLL_INTERVAL_SECONDS:.0f}s"
                )
            self._sleep(_POLL_INTERVAL_SECONDS)

    def _publish_container(
        self, client: httpx.Client, credential: InstagramCredential, container_id: str
    ) -> str:
        response = client.post(
            f"{BASE_URL}/{credential.ig_user_id}/media_publish",
            data={"creation_id": container_id, "access_token": credential.access_token},
        )
        body = _raise_for_graph_error(response, "publishing the container")
        return str(body["id"])


def _compose_caption(content: PublishContent) -> str:
    parts = [part for part in (content.hook, content.body) if part]
    text = "\n\n".join(parts) if parts else content.body
    if content.hashtags:
        tags = " ".join(f"#{tag.lstrip('#')}" for tag in content.hashtags)
        text = f"{text}\n\n{tags}"
    if content.call_to_action:
        text = f"{text}\n\n{content.call_to_action}"
    if len(text) <= CAPTION_LIMIT:
        return text
    return text[: CAPTION_LIMIT - 1] + "…"


def _raise_for_graph_error(response: httpx.Response, doing: str) -> dict:
    try:
        body = response.json()
    except ValueError as exc:
        raise ConnectorError(f"Instagram sent an unreadable response while {doing}") from exc

    if response.status_code >= 400 or "error" in body:
        error = body.get("error", {})
        message = error.get("message", response.text)
        raise ConnectorError(
            f"Instagram refused while {doing} "
            f"(HTTP {response.status_code}, type {error.get('type')}): {message}"
        )
    return body
