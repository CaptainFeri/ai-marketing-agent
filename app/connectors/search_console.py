"""Google Search Console — per-page search performance (handoff section 7).

Read-only, and a fixed, fully documented HTTP contract like WordPress and
Telegram — no model, no weights, so this is real and tested against
``httpx.MockTransport`` rather than simulated.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from urllib.parse import quote

import httpx

from app.connectors.base import ConnectorError
from app.connectors.google_auth import ServiceAccount, fetch_access_token

SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
_TIMEOUT = 30.0
#: Search Console's own ceiling per query.
_ROW_LIMIT = 25000


@dataclass(frozen=True)
class PageMetrics:
    page: str
    clicks: int
    impressions: int
    ctr: float
    position: float


class SearchConsoleClient:
    def __init__(self, client: httpx.Client | None = None) -> None:
        # Tests inject a client built on httpx.MockTransport; production
        # gets a real one per call.
        self._client = client

    def daily_page_metrics(
        self, account: ServiceAccount, site_url: str, day: date
    ) -> list[PageMetrics]:
        """One row per page Search Console has impressions for on ``day``."""
        client = self._client or httpx.Client(timeout=_TIMEOUT)
        owns_client = self._client is None
        try:
            token = fetch_access_token(client, account, SCOPE)
            response = client.post(
                f"https://www.googleapis.com/webmasters/v3/sites/"
                f"{quote(site_url, safe='')}/searchAnalytics/query",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "startDate": day.isoformat(),
                    "endDate": day.isoformat(),
                    "dimensions": ["page"],
                    "rowLimit": _ROW_LIMIT,
                },
            )
        except httpx.HTTPError as exc:
            raise ConnectorError(
                f"Search Console request failed: {type(exc).__name__}: {exc}"
            ) from exc
        finally:
            if owns_client:
                client.close()

        if response.status_code >= 400:
            raise ConnectorError(
                f"Search Console refused the query (HTTP {response.status_code}): {response.text}"
            )
        body = response.json()
        return [
            PageMetrics(
                page=row["keys"][0],
                clicks=int(row.get("clicks", 0)),
                impressions=int(row.get("impressions", 0)),
                ctr=float(row.get("ctr", 0.0)),
                position=float(row.get("position", 0.0)),
            )
            for row in body.get("rows", [])
        ]
