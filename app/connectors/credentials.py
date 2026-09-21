"""What a channel credential must contain (handoff section 10).

Kept separate from ``app.schemas`` because these shapes are never returned by
the API — they exist only to validate what goes into
:func:`app.core.crypto.encrypt_payload` and what comes back out of
:func:`~app.core.crypto.decrypt_payload`. Nothing here reaches an HTTP
response; ``app.schemas.channel`` is the API-facing side, and it deliberately
does not include the secret fields.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, ValidationError

from app.core.errors import AppError
from app.db.enums import Channel


class CredentialPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WordPressCredential(CredentialPayload):
    """A WordPress Application Password (handoff section 10).

    Not an admin password: created under *Users → Profile → Application
    Passwords* on the target site, scoped to one integration and revocable
    without touching the account's login password.
    """

    site_url: HttpUrl
    username: str = Field(min_length=1, max_length=200)
    application_password: str = Field(min_length=1, max_length=500)
    # Which SEO plugin's meta keys to write, if any. "none" still publishes
    # the post; it just skips the meta title/description fields, since a
    # site without either plugin would silently ignore them anyway but there
    # is no harm in being explicit about what was actually attempted.
    seo_plugin: Literal["yoast", "rankmath", "none"] = "yoast"


class TelegramCredential(CredentialPayload):
    """A bot token and the chat it posts to (handoff section 10)."""

    bot_token: str = Field(min_length=1, max_length=200)
    # "@channelname" or a numeric chat id ("-1001234567890"); Telegram
    # accepts both as the sendMessage/sendPhoto `chat_id` parameter.
    chat_id: str = Field(min_length=1, max_length=64)


class InstagramCredential(CredentialPayload):
    """A long-lived access token and the Instagram Business Account it posts
    to (handoff section 11, phase 2).

    ``ig_user_id`` is the Instagram *Business Account* id, not the
    `@handle` — obtained from the Facebook Page linked to the account via
    ``GET /{page-id}?fields=instagram_business_account``, a one-time lookup
    the operator does in Meta's own Graph API Explorer when connecting the
    channel; this platform never performs that lookup itself.
    """

    access_token: str = Field(min_length=1, max_length=1000)
    ig_user_id: str = Field(min_length=1, max_length=64)


class LinkedInCredential(CredentialPayload):
    """An access token and the organization it posts as (handoff section
    11, phase 2). ``organization_urn`` is the full URN
    (``urn:li:organization:12345678``), which is what the UGC Posts API's
    ``author`` field expects verbatim.
    """

    access_token: str = Field(min_length=1, max_length=2000)
    organization_urn: str = Field(min_length=1, max_length=128)


#: Which contract validates a credential for each channel. A channel with no
#: connector yet (X, ...) has none, which is what makes storing a credential
#: for it a clear error instead of silently accepted junk nothing will ever
#: read.
CREDENTIAL_SCHEMAS: dict[Channel, type[CredentialPayload]] = {
    Channel.WORDPRESS: WordPressCredential,
    Channel.TELEGRAM: TelegramCredential,
    Channel.INSTAGRAM: InstagramCredential,
    Channel.LINKEDIN: LinkedInCredential,
}


class CredentialValidationError(AppError):
    """A channel credential payload does not match its schema.

    Kept as an ``AppError`` subclass so it turns into a clean 4xx through
    ``app.main``'s handler rather than an unhandled 500 — the same
    conversion ``QuestionnaireError`` and ``AgentOutputError`` make
    elsewhere in this codebase for exactly the same reason: a raw
    ``pydantic.ValidationError`` from deep inside a service must never reach
    an HTTP client unconverted.
    """

    status_code = 422
    code = "credential_invalid"


def validate_credential(channel: Channel, payload: dict) -> CredentialPayload:
    schema = CREDENTIAL_SCHEMAS.get(channel)
    if schema is None:
        raise AppError(
            f"no connector is implemented for {channel.value!r} yet",
            details={
                "channel": channel.value,
                "implemented": [c.value for c in CREDENTIAL_SCHEMAS],
            },
        )
    try:
        return schema.model_validate(payload)
    except ValidationError as exc:
        raise CredentialValidationError(
            f"the {channel.value!r} credential payload is invalid",
            details={
                "channel": channel.value,
                "errors": [
                    {"loc": [str(part) for part in error["loc"]], "msg": error["msg"]}
                    for error in exc.errors()
                ],
            },
        ) from exc
