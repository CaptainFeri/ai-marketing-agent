"""Telegram via the Bot API (handoff section 10).

A bot posts to a channel it has been added to as an administrator — the bot
token identifies the bot, ``chat_id`` (an ``@channelname`` or a numeric id)
identifies where it posts. Both live in the encrypted credential.
"""

from __future__ import annotations

import logging

import httpx

from app.connectors.base import ConnectorError, CredentialPayload, PublishContent, PublishResult
from app.connectors.credentials import TelegramCredential
from app.db.enums import Channel

logger = logging.getLogger(__name__)

#: Telegram's own limits (Bot API docs). A caption that does not fit is
#: shortened rather than left to the API to reject outright — a shortened
#: post is still worth publishing; a rejected one is not.
CAPTION_LIMIT = 1024
MESSAGE_LIMIT = 4096
_TRUNCATION_MARK = "…"
_TIMEOUT = 30.0


class TelegramConnector:
    channel = Channel.TELEGRAM

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client

    def publish(self, content: PublishContent, credential: CredentialPayload) -> PublishResult:
        if not isinstance(credential, TelegramCredential):
            raise ConnectorError(
                f"TelegramConnector needs a TelegramCredential, got {type(credential).__name__}"
            )
        text = _compose_text(content)
        client = self._client or httpx.Client(timeout=_TIMEOUT)
        owns_client = self._client is None
        base_url = f"https://api.telegram.org/bot{credential.bot_token}"

        try:
            if content.media:
                result = self._send_photo(client, base_url, credential, content.media[0], text)
            else:
                result = self._send_message(client, base_url, credential, text)
        except httpx.HTTPError as exc:
            raise ConnectorError(f"Telegram request failed: {type(exc).__name__}: {exc}") from exc
        finally:
            if owns_client:
                client.close()

        return result

    def _send_photo(self, client, base_url, credential, media, text) -> PublishResult:
        caption, truncated = _fit(text, CAPTION_LIMIT)
        response = client.post(
            f"{base_url}/sendPhoto",
            data={"chat_id": credential.chat_id, "caption": caption},
            files={"photo": (media.filename, media.data, media.mime_type)},
        )
        message = _raise_for_telegram_error(response, "sending the photo")
        return _result(message, truncated)

    def _send_message(self, client, base_url, credential, text) -> PublishResult:
        message_text, truncated = _fit(text, MESSAGE_LIMIT)
        response = client.post(
            f"{base_url}/sendMessage",
            data={"chat_id": credential.chat_id, "text": message_text},
        )
        message = _raise_for_telegram_error(response, "sending the message")
        return _result(message, truncated)


def _compose_text(content: PublishContent) -> str:
    parts = [part for part in (content.hook, content.body) if part]
    text = "\n\n".join(parts) if parts else content.body
    if content.hashtags:
        tags = " ".join(f"#{tag.lstrip('#')}" for tag in content.hashtags)
        text = f"{text}\n\n{tags}"
    if content.call_to_action:
        text = f"{text}\n\n{content.call_to_action}"
    return text


def _fit(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[: limit - len(_TRUNCATION_MARK)] + _TRUNCATION_MARK, True


def _result(message: dict, truncated: bool) -> PublishResult:
    chat = message.get("chat", {})
    username = chat.get("username")
    message_id = message["message_id"]
    external_url = f"https://t.me/{username}/{message_id}" if username else None
    return PublishResult(
        external_id=str(message_id),
        external_url=external_url,
        details={"truncated": truncated},
    )


def _raise_for_telegram_error(response: httpx.Response, doing: str) -> dict:
    try:
        body = response.json()
    except ValueError as exc:
        raise ConnectorError(f"Telegram sent an unreadable response while {doing}") from exc

    if not body.get("ok"):
        description = body.get("description", "unknown error")
        raise ConnectorError(
            f"Telegram refused while {doing} "
            f"(HTTP {response.status_code}, code {body.get('error_code')}): {description}"
        )
    return body["result"]
