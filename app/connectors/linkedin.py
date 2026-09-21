"""LinkedIn via the Posts API (handoff section 10/11, phase 2).

The current API (not the deprecated ``ugcPosts`` one it replaced): a post is
one ``POST /rest/posts`` call, plus — when there is a selected image — an
upload round trip first, since LinkedIn's asset store, like Instagram's,
will not take an image inline in the post body:

1. ``POST /rest/images?action=initializeUpload`` — get a one-time
   ``uploadUrl`` and an ``urn:li:image:...`` for the finished asset.
2. ``PUT`` the raw bytes straight to that ``uploadUrl`` (an already-signed
   URL, not another LinkedIn API call — no extra auth header needed beyond
   the bearer token LinkedIn's own docs show on it).
3. ``POST /rest/posts`` with the image's URN under ``content.media``.

A successful create returns ``201`` with an empty body; the new post's URN
comes back in the ``x-restli-id`` response header, not the JSON.
"""

from __future__ import annotations

import logging

import httpx

from app.connectors.base import ConnectorError, CredentialPayload, PublishContent, PublishResult
from app.connectors.credentials import LinkedInCredential
from app.db.enums import Channel

logger = logging.getLogger(__name__)

#: LinkedIn versions its REST API by calendar month (``YYYYMM``) and expects
#: every request to declare one; each version stays supported for about a
#: year, so this is a "revisit occasionally" constant, not a "pin forever"
#: one — the same idea as Instagram's ``API_VERSION``.
API_VERSION = "202601"
BASE_URL = "https://api.linkedin.com/rest"
COMMENTARY_LIMIT = 3000
_TIMEOUT = 30.0


class LinkedInConnector:
    channel = Channel.LINKEDIN

    def __init__(self, client: httpx.Client | None = None) -> None:
        # Tests inject a client built on httpx.MockTransport.
        self._client = client

    def publish(self, content: PublishContent, credential: CredentialPayload) -> PublishResult:
        if not isinstance(credential, LinkedInCredential):
            raise ConnectorError(
                f"LinkedInConnector needs a LinkedInCredential, got {type(credential).__name__}"
            )
        commentary = _compose_commentary(content)
        client = self._client or httpx.Client(timeout=_TIMEOUT)
        owns_client = self._client is None
        headers = {
            "Authorization": f"Bearer {credential.access_token}",
            "X-Restli-Protocol-Version": "2.0.0",
            "LinkedIn-Version": API_VERSION,
        }

        try:
            image_urn = None
            if content.media:
                image_urn = self._upload_image(client, credential, headers, content.media[0])
            post_urn = self._create_post(client, credential, headers, commentary, image_urn)
        except httpx.HTTPError as exc:
            raise ConnectorError(f"LinkedIn request failed: {type(exc).__name__}: {exc}") from exc
        finally:
            if owns_client:
                client.close()

        return PublishResult(
            external_id=post_urn,
            external_url=f"https://www.linkedin.com/feed/update/{post_urn}/",
            details={"image_urn": image_urn},
        )

    def _upload_image(self, client, credential: LinkedInCredential, headers, media) -> str:
        init_response = client.post(
            f"{BASE_URL}/images?action=initializeUpload",
            headers={**headers, "Content-Type": "application/json"},
            json={"initializeUploadRequest": {"owner": credential.organization_urn}},
        )
        init_body = _raise_for_linkedin_error(init_response, "starting the image upload")
        upload_url = init_body["value"]["uploadUrl"]
        image_urn = init_body["value"]["image"]

        upload_response = client.put(
            upload_url,
            headers={"Authorization": headers["Authorization"]},
            content=media.data,
        )
        if upload_response.status_code >= 400:
            raise ConnectorError(
                f"LinkedIn refused the image upload (HTTP {upload_response.status_code})"
            )
        return str(image_urn)

    def _create_post(
        self,
        client,
        credential: LinkedInCredential,
        headers,
        commentary: str,
        image_urn: str | None,
    ) -> str:
        payload: dict = {
            "author": credential.organization_urn,
            "commentary": commentary,
            "visibility": "PUBLIC",
            "distribution": {
                "feedDistribution": "MAIN_FEED",
                "targetEntities": [],
                "thirdPartyDistributionChannels": [],
            },
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        }
        if image_urn:
            payload["content"] = {"media": {"id": image_urn}}

        response = client.post(
            f"{BASE_URL}/posts",
            headers={**headers, "Content-Type": "application/json"},
            json=payload,
        )
        if response.status_code >= 400:
            _raise_for_linkedin_error(response, "creating the post")
        post_urn = response.headers.get("x-restli-id")
        if not post_urn:
            raise ConnectorError("LinkedIn accepted the post but returned no id")
        return post_urn


class LinkedInInsightsClient:
    """``GET /rest/organizationalEntityShareStatistics`` — per-post
    impressions/clicks/engagement for the daily metrics sweep
    (``app.services.analytics``). The long-standing Organization Share
    Statistics API, still current under the versioned ``/rest`` surface —
    a separate class from :class:`LinkedInConnector` since publishing and
    reading statistics are independent operations sharing one API and one
    credential.
    """

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client

    def share_statistics(self, credential: LinkedInCredential, post_urn: str) -> dict[str, int]:
        client = self._client or httpx.Client(timeout=_TIMEOUT)
        owns_client = self._client is None
        headers = {
            "Authorization": f"Bearer {credential.access_token}",
            "X-Restli-Protocol-Version": "2.0.0",
            "LinkedIn-Version": API_VERSION,
        }
        try:
            response = client.get(
                f"{BASE_URL}/organizationalEntityShareStatistics",
                headers=headers,
                params={
                    "q": "organizationalEntity",
                    "organizationalEntity": credential.organization_urn,
                    "shares[0]": post_urn,
                },
            )
        except httpx.HTTPError as exc:
            raise ConnectorError(
                f"LinkedIn insights request failed: {type(exc).__name__}: {exc}"
            ) from exc
        finally:
            if owns_client:
                client.close()

        body = _raise_for_linkedin_error(response, "reading share statistics")
        elements = body.get("elements", [])
        if not elements:
            return {}
        stats = elements[0].get("totalShareStatistics", {})
        return {
            "impressions": stats.get("impressionCount", 0),
            "clicks": stats.get("clickCount", 0),
            "likes": stats.get("likeCount", 0),
            "comments": stats.get("commentCount", 0),
            "shares": stats.get("shareCount", 0),
            "engagement": stats.get("engagement", 0),
        }


def _compose_commentary(content: PublishContent) -> str:
    parts = [part for part in (content.hook, content.body) if part]
    text = "\n\n".join(parts) if parts else content.body
    if content.hashtags:
        tags = " ".join(f"#{tag.lstrip('#')}" for tag in content.hashtags)
        text = f"{text}\n\n{tags}"
    if content.call_to_action:
        text = f"{text}\n\n{content.call_to_action}"
    if len(text) <= COMMENTARY_LIMIT:
        return text
    return text[: COMMENTARY_LIMIT - 1] + "…"


def _raise_for_linkedin_error(response: httpx.Response, doing: str) -> dict:
    try:
        body = response.json()
    except ValueError as exc:
        raise ConnectorError(f"LinkedIn sent an unreadable response while {doing}") from exc

    if response.status_code >= 400:
        message = body.get("message", response.text)
        raise ConnectorError(
            f"LinkedIn refused while {doing} (HTTP {response.status_code}): {message}"
        )
    return body
