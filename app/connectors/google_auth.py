"""Google service-account JWT Bearer OAuth2 (RFC 7523), shared by the Search
Console and GA4 connectors (handoff section 7).

Neither API accepts a static key — both exchange a short-lived, self-signed
JWT for an hour-long access token. A service account rather than a user
OAuth flow is the right shape for a backend-only integration: no browser, no
consent screen, no refresh token to keep alive — the workspace owner adds
the service account's email as a user on the Search Console property or a
viewer on the GA4 property once, and this signs a fresh assertion on every
call.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx
import jwt

from app.connectors.base import ConnectorError

_DEFAULT_TOKEN_URI = "https://oauth2.googleapis.com/token"
#: Google refuses an assertion whose lifetime exceeds one hour.
_ASSERTION_LIFETIME_SECONDS = 3600


@dataclass(frozen=True)
class ServiceAccount:
    client_email: str
    private_key: str
    token_uri: str = _DEFAULT_TOKEN_URI


def fetch_access_token(client: httpx.Client, account: ServiceAccount, scope: str) -> str:
    """Sign a fresh assertion and exchange it for a bearer access token."""
    now = int(time.time())
    claims = {
        "iss": account.client_email,
        "scope": scope,
        "aud": account.token_uri,
        "iat": now,
        "exp": now + _ASSERTION_LIFETIME_SECONDS,
    }
    try:
        assertion = jwt.encode(claims, account.private_key, algorithm="RS256")
    except (ValueError, TypeError, jwt.exceptions.PyJWTError) as exc:
        raise ConnectorError(f"the service account's private key is invalid: {exc}") from exc

    try:
        response = client.post(
            account.token_uri,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
            },
        )
    except httpx.HTTPError as exc:
        raise ConnectorError(f"token exchange failed: {type(exc).__name__}: {exc}") from exc

    if response.status_code >= 400:
        raise ConnectorError(
            f"Google refused the token exchange (HTTP {response.status_code}): {response.text}"
        )
    try:
        token = response.json()["access_token"]
    except (ValueError, KeyError) as exc:
        raise ConnectorError("Google's token response carried no access_token") from exc
    return str(token)
