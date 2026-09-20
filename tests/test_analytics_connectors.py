"""Search Console and GA4: a real signed JWT, exchanged for a token, against
mocked HTTP that mirrors both APIs' documented contracts — the same posture
``tests/test_connectors.py`` takes toward WordPress and Telegram.
"""

from __future__ import annotations

import json
import urllib.parse
from datetime import date

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    load_pem_private_key,
)

from app.connectors.base import ConnectorError
from app.connectors.ga4 import Ga4Client
from app.connectors.google_auth import ServiceAccount, fetch_access_token
from app.connectors.search_console import SearchConsoleClient


@pytest.fixture(scope="module")
def private_key_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=Encoding.PEM,
        format=PrivateFormat.PKCS8,
        encryption_algorithm=NoEncryption(),
    ).decode()


@pytest.fixture
def account(private_key_pem) -> ServiceAccount:
    return ServiceAccount(
        client_email="svc@acme.iam.gserviceaccount.com", private_key=private_key_pem
    )


def client_for(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def token_response() -> httpx.Response:
    return httpx.Response(200, json={"access_token": "fake-token", "expires_in": 3600})


# ---------------------------------------------------------------------------
# google_auth
# ---------------------------------------------------------------------------
def test_the_assertion_is_a_real_verifiable_jwt(account, private_key_pem) -> None:
    """Not a placeholder string: a JWT any real verifier — Google's included
    — can check, signed with the actual service account key."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        form = urllib.parse.parse_qs(request.content.decode())
        seen["assertion"] = form["assertion"][0]
        seen["grant_type"] = form["grant_type"][0]
        return token_response()

    fetch_access_token(client_for(handler), account, "https://example.com/scope")

    public_key = load_pem_private_key(private_key_pem.encode(), password=None).public_key()
    claims = jwt.decode(
        seen["assertion"], key=public_key, algorithms=["RS256"], audience=account.token_uri
    )
    assert claims["iss"] == account.client_email
    assert claims["scope"] == "https://example.com/scope"
    assert claims["aud"] == account.token_uri
    assert seen["grant_type"] == "urn:ietf:params:oauth:grant-type:jwt-bearer"


def test_a_refused_token_exchange_raises_connector_error(account) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="invalid_grant")

    with pytest.raises(ConnectorError, match="401"):
        fetch_access_token(client_for(handler), account, "scope")


def test_a_response_with_no_access_token_raises_connector_error(account) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": "unexpected"})

    with pytest.raises(ConnectorError, match="access_token"):
        fetch_access_token(client_for(handler), account, "scope")


def test_an_unsignable_key_raises_connector_error() -> None:
    bad_account = ServiceAccount(client_email="x@example.com", private_key="not a real key")
    with pytest.raises(ConnectorError, match="private key"):
        fetch_access_token(client_for(lambda r: token_response()), bad_account, "scope")


# ---------------------------------------------------------------------------
# search console
# ---------------------------------------------------------------------------
def test_search_console_matches_rows_to_the_requested_day(account) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "searchAnalytics" not in request.url.path:
            return token_response()
        assert "/sites/" in request.url.path
        body = json.loads(request.content)
        assert body["startDate"] == "2026-01-15"
        assert body["endDate"] == "2026-01-15"
        return httpx.Response(
            200,
            json={
                "rows": [
                    {
                        "keys": ["https://acme.example/drill-guide"],
                        "clicks": 12,
                        "impressions": 340,
                        "ctr": 0.035,
                        "position": 8.4,
                    }
                ]
            },
        )

    rows = SearchConsoleClient(client_for(handler)).daily_page_metrics(
        account, "https://acme.example/", date(2026, 1, 15)
    )
    assert rows[0].page == "https://acme.example/drill-guide"
    assert rows[0].clicks == 12
    assert rows[0].impressions == 340
    assert rows[0].position == pytest.approx(8.4)


def test_search_console_site_url_is_percent_encoded_on_the_wire(account) -> None:
    """``request.url.path`` decodes for convenience; ``raw_path`` is what
    actually goes over the wire, and that must not contain a raw, unencoded
    slash-heavy site URL splicing extra path segments into the request."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if "searchAnalytics" not in request.url.path:
            return token_response()
        seen["raw_path"] = request.url.raw_path.decode()
        return httpx.Response(200, json={"rows": []})

    SearchConsoleClient(client_for(handler)).daily_page_metrics(
        account, "https://acme.example/", date(2026, 1, 15)
    )
    assert "%2F" in seen["raw_path"]
    assert "/sites/https://acme.example/" not in seen["raw_path"]


def test_search_console_refusal_raises_connector_error(account) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "searchAnalytics" in request.url.path:
            return httpx.Response(403, text="site not verified")
        return token_response()

    with pytest.raises(ConnectorError, match="403"):
        SearchConsoleClient(client_for(handler)).daily_page_metrics(
            account, "https://acme.example/", date(2026, 1, 15)
        )


def test_search_console_no_rows_is_an_empty_list(account) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "searchAnalytics" in request.url.path:
            return httpx.Response(200, json={})
        return token_response()

    rows = SearchConsoleClient(client_for(handler)).daily_page_metrics(
        account, "https://acme.example/", date(2026, 1, 15)
    )
    assert rows == []


# ---------------------------------------------------------------------------
# ga4
# ---------------------------------------------------------------------------
def test_ga4_parses_a_report_row_positionally(account) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "runReport" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "rows": [
                        {
                            "dimensionValues": [{"value": "/drill-guide"}],
                            "metricValues": [
                                {"value": "150"},
                                {"value": "120"},
                                {"value": "90"},
                                {"value": "64.5"},
                            ],
                        }
                    ]
                },
            )
        return token_response()

    rows = Ga4Client(client_for(handler)).daily_page_metrics(
        account, "123456789", date(2026, 1, 15)
    )
    assert rows[0].page_path == "/drill-guide"
    assert rows[0].screen_page_views == 150
    assert rows[0].sessions == 120
    assert rows[0].engaged_sessions == 90
    assert rows[0].average_session_duration == pytest.approx(64.5)


def test_ga4_property_id_is_in_the_request_path(account) -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if "runReport" in request.url.path:
            seen["path"] = request.url.path
            return httpx.Response(200, json={"rows": []})
        return token_response()

    Ga4Client(client_for(handler)).daily_page_metrics(account, "999", date(2026, 1, 15))
    assert "999" in seen["path"]


def test_ga4_refusal_raises_connector_error(account) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "runReport" in request.url.path:
            return httpx.Response(403, text="permission denied")
        return token_response()

    with pytest.raises(ConnectorError, match="403"):
        Ga4Client(client_for(handler)).daily_page_metrics(account, "999", date(2026, 1, 15))
