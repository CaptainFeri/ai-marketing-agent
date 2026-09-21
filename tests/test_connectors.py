"""The connector layer: credential contracts, and both connectors against
mocked HTTP that mirrors the real WordPress and Telegram APIs.

No live site or bot token is needed to test these — unlike the language and
image models, WordPress's REST API and Telegram's Bot API are fixed, fully
documented HTTP contracts. What is tested here is that this code speaks that
contract correctly, using ``httpx.MockTransport`` rather than a live server.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.connectors import (
    CONNECTORS,
    CREDENTIAL_SCHEMAS,
    ConnectorError,
    CredentialValidationError,
    MediaForPublish,
    PublishContent,
    TelegramCredential,
    WordPressCredential,
    build_connector,
    validate_credential,
)
from app.connectors.telegram import CAPTION_LIMIT, MESSAGE_LIMIT, TelegramConnector
from app.connectors.wordpress import WordPressConnector
from app.db.enums import Channel


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


def article(**overrides) -> dict:
    defaults = {
        "title": "راهنمای خرید دریل برقی",
        "slug": "drill-guide",
        "meta_title": "خرید دریل برقی",
        "meta_description": "راهنمای کامل خرید",
        "excerpt": "خلاصه مقاله",
        "sections": [{"heading": "مقدمه", "level": 2, "body": "متن معرفی."}],
        "answer_blocks": [{"question": "بهترین دریل کدام است؟", "answer": "بستگی دارد."}],
        "schema_org": {"@type": "Article", "headline": "x"},
        "image_alts": [{"slot": "hero", "alt_text": "یک دریل برقی"}],
    }
    defaults.update(overrides)
    return defaults


def image(**overrides) -> MediaForPublish:
    defaults = {"data": b"\x89PNG\r\n\x1a\n...", "mime_type": "image/png", "filename": "x.png"}
    defaults.update(overrides)
    return MediaForPublish(**defaults)


# ---------------------------------------------------------------------------
# credential contracts
# ---------------------------------------------------------------------------
def test_every_implemented_connector_has_a_matching_credential_schema() -> None:
    assert set(CONNECTORS) == set(CREDENTIAL_SCHEMAS)


def test_a_wordpress_credential_needs_a_real_url() -> None:
    with pytest.raises(CredentialValidationError):
        validate_credential(
            Channel.WORDPRESS,
            {"site_url": "not a url", "username": "b", "application_password": "p"},
        )


def test_a_wordpress_credential_rejects_unknown_fields() -> None:
    """Same rule as the agent contracts: a typo'd field should fail loudly,
    not be silently ignored and leave the connector half-configured."""
    with pytest.raises(CredentialValidationError):
        validate_credential(
            Channel.WORDPRESS,
            {
                "site_url": "https://example.com",
                "username": "b",
                "application_password": "p",
                "extra_field": "x",
            },
        )


def test_a_telegram_credential_needs_a_token_and_a_chat() -> None:
    with pytest.raises(CredentialValidationError):
        validate_credential(Channel.TELEGRAM, {"bot_token": "1:a"})


def test_an_unimplemented_channel_is_a_clear_error() -> None:
    """X has no connector yet (handoff section 11: manual export instead —
    see app.services.x_export), so it must fail loudly rather than silently
    accept a credential or a publish nothing will ever use."""
    from app.core.errors import AppError

    with pytest.raises(AppError, match="'x' yet"):
        validate_credential(Channel.X, {})
    with pytest.raises(AppError, match="'x' yet"):
        build_connector(Channel.X)


# ---------------------------------------------------------------------------
# WordPress
# ---------------------------------------------------------------------------
def wp_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def wp_credential(**overrides) -> WordPressCredential:
    defaults = {
        "site_url": "https://example.com",
        "username": "bot",
        "application_password": "abcd efgh",
    }
    defaults.update(overrides)
    return WordPressCredential(**defaults)


def test_wordpress_creates_a_post_with_the_articles_title_and_slug() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/wp-json/wp/v2/posts":
            seen["body"] = json.loads(request.content)
            return httpx.Response(201, json={"id": 7, "link": "https://example.com/?p=7"})
        return httpx.Response(404)

    result = WordPressConnector(client=wp_client(handler)).publish(
        content(article=article()), wp_credential()
    )
    assert result.external_id == "7"
    assert result.external_url == "https://example.com/?p=7"
    assert seen["body"]["title"] == "راهنمای خرید دریل برقی"
    assert seen["body"]["slug"] == "drill-guide"


def test_wordpress_renders_sections_and_answer_blocks_into_the_content() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": 1, "link": None})

    WordPressConnector(client=wp_client(handler)).publish(
        content(article=article()), wp_credential()
    )
    html = seen["body"]["content"]
    assert "<h2>مقدمه</h2>" in html
    assert "متن معرفی" in html
    assert "بهترین دریل کدام است؟" in html


def test_wordpress_falls_back_to_the_variant_body_with_no_article() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": 1, "link": None})

    WordPressConnector(client=wp_client(handler)).publish(content(article=None), wp_credential())
    assert "متن کوتاه پست." in seen["body"]["content"]
    # No article means no slug to send.
    assert "slug" not in seen["body"]


def test_wordpress_uploads_the_first_selected_image_as_featured_media() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path == "/wp-json/wp/v2/media":
            assert request.content == b"\x89PNG\r\n\x1a\n..."
            assert request.headers["content-type"] == "image/png"
            return httpx.Response(201, json={"id": 42})
        if request.url.path == "/wp-json/wp/v2/media/42":
            body = json.loads(request.content)
            assert body["alt_text"] == "یک دریل"
            return httpx.Response(200, json={"id": 42})
        body = json.loads(request.content)
        assert body["featured_media"] == 42
        return httpx.Response(201, json={"id": 1, "link": None})

    WordPressConnector(client=wp_client(handler)).publish(
        content(article=article(), media=(image(alt_text="یک دریل"),)), wp_credential()
    )
    assert ("POST", "/wp-json/wp/v2/media") in calls
    assert ("POST", "/wp-json/wp/v2/media/42") in calls


def test_wordpress_writes_yoast_meta_by_default() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": 1, "link": None})

    WordPressConnector(client=wp_client(handler)).publish(
        content(article=article()), wp_credential()
    )
    meta = seen["body"]["meta"]
    assert meta["_yoast_wpseo_title"] == "خرید دریل برقی"
    assert meta["_yoast_wpseo_metadesc"] == "راهنمای کامل خرید"


def test_wordpress_writes_rankmath_meta_when_configured() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": 1, "link": None})

    cred = wp_credential(seo_plugin="rankmath")
    WordPressConnector(client=wp_client(handler)).publish(content(article=article()), cred)
    meta = seen["body"]["meta"]
    assert "rank_math_title" in meta
    assert "_yoast_wpseo_title" not in meta


def test_wordpress_skips_seo_meta_when_told_the_site_has_no_plugin() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": 1, "link": None})

    cred = wp_credential(seo_plugin="none")
    WordPressConnector(client=wp_client(handler)).publish(content(article=article()), cred)
    assert "meta" not in seen["body"] or not seen["body"].get("meta")


def test_wordpress_carries_the_geo_agents_schema_org_as_meta() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": 1, "link": None})

    WordPressConnector(client=wp_client(handler)).publish(
        content(article=article()), wp_credential()
    )
    stored = json.loads(seen["body"]["meta"]["ai_marketing_schema_org"])
    assert stored["@type"] == "Article"


def test_wordpress_carries_hreflang_alternates_as_meta() -> None:
    """No native REST field for it (docs/publishing.md) — a theme snippet
    reads this the same way it would for schema_org."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": 1, "link": None})

    WordPressConnector(client=wp_client(handler)).publish(
        content(article=article(), hreflang_alternates={"en": "https://acme.example/en/x"}),
        wp_credential(),
    )
    stored = json.loads(seen["body"]["meta"]["ai_marketing_hreflang"])
    assert stored == {"en": "https://acme.example/en/x"}


def test_wordpress_omits_hreflang_meta_with_no_alternates() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": 1, "link": None})

    WordPressConnector(client=wp_client(handler)).publish(
        content(article=article()), wp_credential()
    )
    assert "ai_marketing_hreflang" not in seen["body"].get("meta", {})


def test_wordpress_error_response_becomes_a_connector_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "invalid application password"})

    with pytest.raises(ConnectorError, match="invalid application password"):
        WordPressConnector(client=wp_client(handler)).publish(content(), wp_credential())


def test_wordpress_network_failure_becomes_a_connector_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(ConnectorError, match="ConnectError"):
        WordPressConnector(client=wp_client(handler)).publish(content(), wp_credential())


def test_wordpress_refuses_the_wrong_credential_type() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("should never be called")

    with pytest.raises(ConnectorError, match="WordPressCredential"):
        WordPressConnector(client=wp_client(handler)).publish(
            content(), TelegramCredential(bot_token="1:a", chat_id="@x")
        )


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------
def tg_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _multipart_field(body: bytes, name: str) -> str:
    """Pull one text field's value out of a multipart/form-data body."""
    import re

    match = re.search(rf'name="{name}"\r\n\r\n(.*?)\r\n--'.encode(), body, re.DOTALL)
    assert match, f"field {name!r} not found in multipart body"
    return match.group(1).decode("utf-8")


def tg_credential(**overrides) -> TelegramCredential:
    defaults = {"bot_token": "123456:ABC-token", "chat_id": "@mychannel"}
    defaults.update(overrides)
    return TelegramCredential(**defaults)


def test_telegram_sends_a_photo_when_media_is_selected() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(
            200, json={"ok": True, "result": {"message_id": 5, "chat": {"username": "mychannel"}}}
        )

    result = TelegramConnector(client=tg_client(handler)).publish(
        content(media=(image(),)), tg_credential()
    )
    assert calls[0].endswith("/sendPhoto")
    assert result.external_id == "5"
    assert result.external_url == "https://t.me/mychannel/5"


def test_telegram_sends_a_text_message_with_no_media() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 5, "chat": {}}})

    TelegramConnector(client=tg_client(handler)).publish(content(media=()), tg_credential())
    assert calls[0].endswith("/sendMessage")


def test_telegram_composes_hook_body_hashtags_and_cta() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["text"] = httpx.QueryParams(request.content.decode())["text"]
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1, "chat": {}}})

    TelegramConnector(client=tg_client(handler)).publish(content(media=()), tg_credential())
    text = captured["text"]
    assert "قلاب جذاب" in text
    assert "متن کوتاه پست." in text
    assert "#ابزار" in text
    assert "#دریل" in text
    assert "همین حالا بخوانید" in text


def test_telegram_truncates_an_overlong_message() -> None:
    long_body = "x" * 5000
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["text"] = httpx.QueryParams(request.content.decode())["text"]
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1, "chat": {}}})

    result = TelegramConnector(client=tg_client(handler)).publish(
        content(body=long_body, hook=None, hashtags=(), call_to_action=None, media=()),
        tg_credential(),
    )
    assert len(captured["text"]) <= MESSAGE_LIMIT
    assert captured["text"].endswith("…")
    assert result.details["truncated"] is True


def test_telegram_truncates_an_overlong_caption_more_aggressively_than_a_message() -> None:
    long_body = "x" * 5000
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        # A photo request is multipart, so the body cannot be decoded as a
        # whole (it carries raw image bytes) — only the caption field is.
        captured["caption"] = _multipart_field(request.content, "caption")
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1, "chat": {}}})

    TelegramConnector(client=tg_client(handler)).publish(
        content(body=long_body, hook=None, hashtags=(), call_to_action=None, media=(image(),)),
        tg_credential(),
    )
    assert len(captured["caption"]) <= CAPTION_LIMIT


def test_telegram_a_short_message_is_not_marked_truncated() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1, "chat": {}}})

    result = TelegramConnector(client=tg_client(handler)).publish(
        content(media=()), tg_credential()
    )
    assert result.details["truncated"] is False


def test_telegram_api_error_becomes_a_connector_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403, json={"ok": False, "error_code": 403, "description": "bot was blocked"}
        )

    with pytest.raises(ConnectorError, match="bot was blocked"):
        TelegramConnector(client=tg_client(handler)).publish(content(media=()), tg_credential())


def test_telegram_an_unreadable_response_is_a_connector_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    with pytest.raises(ConnectorError):
        TelegramConnector(client=tg_client(handler)).publish(content(media=()), tg_credential())


def test_telegram_refuses_the_wrong_credential_type() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("should never be called")

    with pytest.raises(ConnectorError, match="TelegramCredential"):
        TelegramConnector(client=tg_client(handler)).publish(content(), wp_credential())
