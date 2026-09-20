"""Google Analytics 4 — the Data API's ``runReport``, per page (handoff
section 7). Same posture as ``search_console.py``: a fixed HTTP contract,
real and tested against ``httpx.MockTransport``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import httpx

from app.connectors.base import ConnectorError
from app.connectors.google_auth import ServiceAccount, fetch_access_token

SCOPE = "https://www.googleapis.com/auth/analytics.readonly"
_TIMEOUT = 30.0
#: Order matters: matched positionally against each row's metricValues.
_METRICS = ("screenPageViews", "sessions", "engagedSessions", "averageSessionDuration")


@dataclass(frozen=True)
class PageMetrics:
    page_path: str
    screen_page_views: int
    sessions: int
    engaged_sessions: int
    average_session_duration: float


class Ga4Client:
    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client

    def daily_page_metrics(
        self, account: ServiceAccount, property_id: str, day: date
    ) -> list[PageMetrics]:
        """One row per page GA4 recorded activity for on ``day``."""
        client = self._client or httpx.Client(timeout=_TIMEOUT)
        owns_client = self._client is None
        try:
            token = fetch_access_token(client, account, SCOPE)
            response = client.post(
                f"https://analyticsdata.googleapis.com/v1beta/properties/{property_id}:runReport",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "dateRanges": [{"startDate": day.isoformat(), "endDate": day.isoformat()}],
                    "dimensions": [{"name": "pagePath"}],
                    "metrics": [{"name": name} for name in _METRICS],
                },
            )
        except httpx.HTTPError as exc:
            raise ConnectorError(f"GA4 request failed: {type(exc).__name__}: {exc}") from exc
        finally:
            if owns_client:
                client.close()

        if response.status_code >= 400:
            raise ConnectorError(
                f"GA4 refused the report (HTTP {response.status_code}): {response.text}"
            )
        body = response.json()
        results: list[PageMetrics] = []
        for row in body.get("rows", []):
            values = [metric_value["value"] for metric_value in row["metricValues"]]
            results.append(
                PageMetrics(
                    page_path=row["dimensionValues"][0]["value"],
                    screen_page_views=int(values[0]),
                    sessions=int(values[1]),
                    engaged_sessions=int(values[2]),
                    average_session_duration=float(values[3]),
                )
            )
        return results
