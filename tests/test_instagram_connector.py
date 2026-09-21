"""The Instagram connector against mocked HTTP that mirrors the real Graph
API Content Publishing contract — container create, poll, publish.

No live Instagram Business Account or access token is needed: the contract
(``POST /{ig-user-id}/media``, ``GET /{container-id}``,
``POST /{ig-user-id}/media_publish``) is fixed and documented, the same
reasoning ``tests/test_connectors.py`` gives for WordPress and Telegram.
"""

from __future__ import annotations

import httpx
import pytest

from app.connectors.base import ConnectorError, MediaForPublish, PublishContent
from app.connectors.credentials import InstagramCredential, TelegramCredential
from app.connectors.instagram import InstagramConnector, InstagramInsightsClient


def content(**overrides) -> PublishContent:
    defaults = {
        "title": "راهنمای خرید دریل برقی",
        "body": "متن کوتاه پست.",
        "hook": "قلاب جذاب",
        "hashtags": ("ابزار", "دریل"),
        "call_to_action": "همین حالا بخوانید",
        "article": None,
        "media": (image(),),
        "utm": {},
    }
    defaults.update(overrides)
    return PublishContent(**defaults)


def image(**overrides) -> MediaForPublish:
    defaults = {
        "data": b"\x89PNG\r\n\x1a\n...",
        "mime_type": "image/png",
        "filename": "x.png",
        "url": "https://cdn.example.com/x.png",
    }
    defaults.update(overrides)
    return MediaForPublish(**defaults)


def credential(**overrides) -> InstagramCredential:
    defaults = {"access_token": "IGQ...token", "ig_user_id": "17841400000000000"}
    defaults.update(overrides)
    return InstagramCredential(**defaults)


def client(handler, sleeps: list[float] | None = None) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def connector(handler) -> InstagramConnector:
    return InstagramConnector(client=client(handler), sleep=lambda _: None)


def test_a_full_publish_creates_polls_and_publishes_the_container() -> None:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path == "/v21.0/17841400000000000/media":
            body = dict(httpx.QueryParams(request.content.decode()))
            assert body["image_url"] == "https://cdn.example.com/x.png"
            assert "قلاب جذاب" in body["caption"]
            assert "#ابزار" in body["caption"]
            return httpx.Response(200, json={"id": "container-1"})
        if request.url.path == "/v21.0/container-1":
            return httpx.Response(200, json={"status_code": "FINISHED"})
        if request.url.path == "/v21.0/17841400000000000/media_publish":
            return httpx.Response(200, json={"id": "media-1"})
        raise AssertionError(f"unexpected call: {request.method} {request.url.path}")

    result = connector(handler).publish(content(), credential())
    assert result.external_id == "media-1"
    assert result.external_url == "https://www.instagram.com/p/media-1/"
    assert result.details["container_id"] == "container-1"
    assert ("POST", "/v21.0/17841400000000000/media") in calls
    assert ("GET", "/v21.0/container-1") in calls
    assert ("POST", "/v21.0/17841400000000000/media_publish") in calls


def test_the_caption_is_the_hook_body_hashtags_and_cta_in_order() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/media") and request.method == "POST":
            body = dict(httpx.QueryParams(request.content.decode()))
            seen["caption"] = body["caption"]
            return httpx.Response(200, json={"id": "container-1"})
        if request.url.path == "/v21.0/container-1":
            return httpx.Response(200, json={"status_code": "FINISHED"})
        return httpx.Response(200, json={"id": "media-1"})

    connector(handler).publish(content(), credential())
    caption = seen["caption"]
    assert caption.index("قلاب جذاب") < caption.index("متن کوتاه پست.")
    assert caption.index("متن کوتاه پست.") < caption.index("#ابزار")
    assert caption.rstrip().endswith("همین حالا بخوانید")


def test_a_long_caption_is_truncated_to_the_instagram_limit() -> None:
    from app.connectors.instagram import CAPTION_LIMIT

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/media") and request.method == "POST":
            body = dict(httpx.QueryParams(request.content.decode()))
            seen["caption"] = body["caption"]
            return httpx.Response(200, json={"id": "container-1"})
        if request.url.path == "/v21.0/container-1":
            return httpx.Response(200, json={"status_code": "FINISHED"})
        return httpx.Response(200, json={"id": "media-1"})

    connector(handler).publish(content(body="a" * 3000), credential())
    assert len(seen["caption"]) == CAPTION_LIMIT


def test_no_selected_image_is_a_clear_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("should never be called")

    with pytest.raises(ConnectorError, match="at least one selected image"):
        connector(handler).publish(content(media=()), credential())


def test_an_image_with_no_public_url_is_a_clear_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("should never be called")

    with pytest.raises(ConnectorError, match="publicly reachable URL"):
        connector(handler).publish(content(media=(image(url=None),)), credential())


def test_polling_retries_until_the_container_finishes() -> None:
    statuses = iter(["IN_PROGRESS", "IN_PROGRESS", "FINISHED"])
    poll_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal poll_count
        if request.url.path.endswith("/media") and request.method == "POST":
            return httpx.Response(200, json={"id": "container-1"})
        if request.url.path == "/v21.0/container-1":
            poll_count += 1
            return httpx.Response(200, json={"status_code": next(statuses)})
        return httpx.Response(200, json={"id": "media-1"})

    connector(handler).publish(content(), credential())
    assert poll_count == 3


def test_a_container_error_status_is_a_connector_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/media") and request.method == "POST":
            return httpx.Response(200, json={"id": "container-1"})
        if request.url.path == "/v21.0/container-1":
            return httpx.Response(200, json={"status_code": "ERROR"})
        raise AssertionError("should not reach media_publish")

    with pytest.raises(ConnectorError, match="failed to process"):
        connector(handler).publish(content(), credential())


def test_a_container_that_never_finishes_times_out() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/media") and request.method == "POST":
            return httpx.Response(200, json={"id": "container-1"})
        if request.url.path == "/v21.0/container-1":
            return httpx.Response(200, json={"status_code": "IN_PROGRESS"})
        raise AssertionError("should not reach media_publish")

    with pytest.raises(ConnectorError, match="still 'IN_PROGRESS'"):
        connector(handler).publish(content(), credential())


def test_a_graph_api_error_becomes_a_connector_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error": {"message": "Invalid OAuth access token", "type": "OAuthException"}},
        )

    with pytest.raises(ConnectorError, match="Invalid OAuth access token"):
        connector(handler).publish(content(), credential())


def test_a_network_failure_becomes_a_connector_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(ConnectorError, match="ConnectError"):
        connector(handler).publish(content(), credential())


def test_refuses_the_wrong_credential_type() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("should never be called")

    with pytest.raises(ConnectorError, match="InstagramCredential"):
        connector(handler).publish(content(), TelegramCredential(bot_token="1:a", chat_id="@x"))


# --------------------------------------------------------------------------
# InstagramInsightsClient (handoff section 11: social insights pulling)
# --------------------------------------------------------------------------
def test_media_insights_reads_the_requested_metrics() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["metric"] = request.url.params["metric"]
        return httpx.Response(
            200,
            json={
                "data": [
                    {"name": "reach", "values": [{"value": 120}]},
                    {"name": "likes", "values": [{"value": 8}]},
                    {"name": "comments", "values": [{"value": 2}]},
                    {"name": "saved", "values": [{"value": 3}]},
                    {"name": "shares", "values": [{"value": 1}]},
                ]
            },
        )

    result = InstagramInsightsClient(client(handler)).media_insights(credential(), "media-1")
    assert seen["path"] == "/v21.0/media-1/insights"
    assert "reach" in seen["metric"] and "saved" in seen["metric"]
    assert result == {"reach": 120, "likes": 8, "comments": 2, "saved": 3, "shares": 1}


def test_media_insights_ignores_an_entry_with_no_values() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"name": "reach", "values": []}]})

    result = InstagramInsightsClient(client(handler)).media_insights(credential(), "media-1")
    assert result == {}


def test_media_insights_error_becomes_a_connector_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "Unsupported request"}})

    with pytest.raises(ConnectorError, match="Unsupported request"):
        InstagramInsightsClient(client(handler)).media_insights(credential(), "media-1")
