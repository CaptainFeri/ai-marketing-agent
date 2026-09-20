"""What a Search Console / GA4 credential must contain (handoff section 7).

Same reasoning and shape as ``app.connectors.credentials``, kept in its own
module because an ``AnalyticsProvider`` is not a ``Channel`` — nothing is
ever published "to" Search Console or GA4, so sharing one dict keyed the
same way would blur two different concepts together.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.connectors.google_auth import ServiceAccount
from app.core.errors import AppError
from app.db.enums import AnalyticsProvider


class GoogleServiceAccountKey(BaseModel):
    """The JSON key file Google Cloud Console hands you when a service
    account's key is created — accepted as downloaded, so a customer never
    has to hand-extract fields from it. Only the two this platform actually
    uses are validated; the rest (``type``, ``project_id``, ``client_id``,
    ``universe_domain``, ...) pass through unchecked.
    """

    model_config = ConfigDict(extra="allow")

    client_email: str = Field(min_length=1, max_length=320)
    private_key: str = Field(min_length=1)
    token_uri: str = "https://oauth2.googleapis.com/token"

    def to_service_account(self) -> ServiceAccount:
        return ServiceAccount(
            client_email=self.client_email,
            private_key=self.private_key,
            token_uri=self.token_uri,
        )


class AnalyticsCredentialPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchConsoleCredential(AnalyticsCredentialPayload):
    """The workspace owner adds ``service_account.client_email`` as a user
    on this property in Search Console before this can read anything."""

    site_url: str = Field(min_length=1, max_length=2048)
    service_account: GoogleServiceAccountKey


class Ga4Credential(AnalyticsCredentialPayload):
    """The workspace owner grants ``service_account.client_email`` at least
    Viewer access on this GA4 property before this can read anything."""

    property_id: str = Field(min_length=1, max_length=32)
    service_account: GoogleServiceAccountKey


ANALYTICS_CREDENTIAL_SCHEMAS: dict[AnalyticsProvider, type[AnalyticsCredentialPayload]] = {
    AnalyticsProvider.SEARCH_CONSOLE: SearchConsoleCredential,
    AnalyticsProvider.GA4: Ga4Credential,
}


class AnalyticsCredentialValidationError(AppError):
    """An analytics credential payload does not match its schema — the same
    conversion ``CredentialValidationError`` makes for channel credentials."""

    status_code = 422
    code = "analytics_credential_invalid"


def validate_analytics_credential(
    provider: AnalyticsProvider, payload: dict
) -> AnalyticsCredentialPayload:
    schema = ANALYTICS_CREDENTIAL_SCHEMAS[provider]
    try:
        return schema.model_validate(payload)
    except ValidationError as exc:
        raise AnalyticsCredentialValidationError(
            f"the {provider.value!r} credential payload is invalid",
            details={
                "provider": provider.value,
                "errors": [
                    {"loc": [str(part) for part in error["loc"]], "msg": error["msg"]}
                    for error in exc.errors()
                ],
            },
        ) from exc
