"""The LinkedIn connector against mocked HTTP that mirrors the real Posts
API contract — an optional image upload, then POST /rest/posts.

No live LinkedIn organization or access token is needed: the contract is
fixed and documented, the same reasoning ``tests/test_connectors.py`` gives
for WordPress and Telegram.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.connectors.base import ConnectorError, MediaForPublish, PublishContent
from app.connectors.credentials import LinkedInCredential, TelegramCredential
from app.connectors.linkedin import LinkedInConnector


def content(**overrides) -> PublishContent:
    defaults = {
        "title": "راهنمای خرید دریل برقی",
        "body": "متن کوتاه پست.",
        "hook": "قلاب جذاب",
        "hashtags": ("ابزار", "دریل"),
        "call_to_action": "همین حالا بخوانید",
        "article": None,
        "media": (),
        "utm": {},
    }
    defaults.update(overrides)
    return PublishContent(**defaults)


def image(**overrides) -> MediaForPublish:
    defaults = {"data": b"\x89PNG\r\n\x1a\n...", "mime_type": "image/png", "filename": "x.png"}
    defaults.update(overrides)
    return MediaForPublish(**defaults)


def credential(**overrides) -> LinkedInCredential:
    defaults = {"access_token": "AQV...token", "organization_urn": "urn:li:organization:12345"}
    defaults.update(overrides)
    return LinkedInCredential(**defaults)


def client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def connector(handler) -> LinkedInConnector:
    return LinkedInConnector(client=client(handler))


def test_a_text_only_post_skips_the_image_upload() -> None:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        assert request.url.path == "/rest/posts"
        body = json.loads(request.content)
        assert body["author"] == "urn:li:organization:12345"
        assert "content" not in body
        return httpx.Response(201, headers={"x-restli-id": "urn:li:share:1"})

    result = connector(handler).publish(content(), credential())
    assert result.external_id == "urn:li:share:1"
    assert result.external_url == "https://www.linkedin.com/feed/update/urn:li:share:1/"
    assert calls == [("POST", "/rest/posts")]


def test_a_post_with_media_uploads_the_image_first() -> None:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path == "/rest/images" and request.method == "POST":
            body = json.loads(request.content)
            assert body["initializeUploadRequest"]["owner"] == "urn:li:organization:12345"
            return httpx.Response(
                200,
                json={
                    "value": {
                        "uploadUrl": "https://upload.linkedin.com/put/abc",
                        "image": "urn:li:image:abc123",
                    }
                },
            )
        if request.url.path == "/put/abc":
            assert request.content == b"\x89PNG\r\n\x1a\n..."
            return httpx.Response(201)
        if request.url.path == "/rest/posts":
            body = json.loads(request.content)
            assert body["content"]["media"]["id"] == "urn:li:image:abc123"
            return httpx.Response(201, headers={"x-restli-id": "urn:li:share:2"})
        raise AssertionError(f"unexpected call: {request.method} {request.url.path}")

    result = connector(handler).publish(content(media=(image(),)), credential())
    assert result.external_id == "urn:li:share:2"
    assert result.details["image_urn"] == "urn:li:image:abc123"
    assert ("POST", "/rest/images") in calls
    assert ("PUT", "/put/abc") in calls
    assert ("POST", "/rest/posts") in calls


def test_the_commentary_is_the_hook_body_hashtags_and_cta_in_order() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, headers={"x-restli-id": "urn:li:share:1"})

    connector(handler).publish(content(), credential())
    commentary = seen["body"]["commentary"]
    assert commentary.index("قلاب جذاب") < commentary.index("متن کوتاه پست.")
    assert commentary.index("متن کوتاه پست.") < commentary.index("#ابزار")
    assert commentary.rstrip().endswith("همین حالا بخوانید")


def test_a_long_commentary_is_truncated_to_the_linkedin_limit() -> None:
    from app.connectors.linkedin import COMMENTARY_LIMIT

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, headers={"x-restli-id": "urn:li:share:1"})

    connector(handler).publish(content(body="a" * 5000), credential())
    assert len(seen["body"]["commentary"]) == COMMENTARY_LIMIT


def test_every_call_carries_the_required_headers() -> None:
    from app.connectors.linkedin import API_VERSION

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = request.headers
        return httpx.Response(201, headers={"x-restli-id": "urn:li:share:1"})

    connector(handler).publish(content(), credential())
    headers = seen["headers"]
    assert headers["authorization"] == "Bearer AQV...token"
    assert headers["x-restli-protocol-version"] == "2.0.0"
    assert headers["linkedin-version"] == API_VERSION


def test_a_failed_image_upload_is_a_connector_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/rest/images":
            return httpx.Response(
                200,
                json={
                    "value": {
                        "uploadUrl": "https://upload.linkedin.com/put/abc",
                        "image": "urn:li:image:x",
                    }
                },
            )
        if request.url.path == "/put/abc":
            return httpx.Response(403)
        raise AssertionError("should not reach /rest/posts")

    with pytest.raises(ConnectorError, match="refused the image upload"):
        connector(handler).publish(content(media=(image(),)), credential())


def test_a_missing_post_id_is_a_connector_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201)  # no x-restli-id header

    with pytest.raises(ConnectorError, match="returned no id"):
        connector(handler).publish(content(), credential())


def test_an_api_error_becomes_a_connector_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"message": "commentary is required"})

    with pytest.raises(ConnectorError, match="commentary is required"):
        connector(handler).publish(content(), credential())


def test_a_network_failure_becomes_a_connector_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(ConnectorError, match="ConnectError"):
        connector(handler).publish(content(), credential())


def test_refuses_the_wrong_credential_type() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("should never be called")

    with pytest.raises(ConnectorError, match="LinkedInCredential"):
        connector(handler).publish(content(), TelegramCredential(bot_token="1:a", chat_id="@x"))
