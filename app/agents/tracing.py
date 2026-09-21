"""Sending trace data to Langfuse.

Handoff section 4: "مشاهده‌پذیری: Langfuse خودمیزبان + لاگ زمان GPU هر مرحله"
— self-hosted Langfuse, and GPU time logged per step.

Two implementations, the same shape as :mod:`app.agents.llm`:

``LangfuseClient``
    The real one. Speaks Langfuse's public ingestion API (``POST
    {host}/api/public/ingestion``) — the same contract the official SDKs
    use — over plain ``httpx`` rather than the SDK itself, matching this
    platform's own connector style (``search_console.py``, ``ga4.py``): a
    thin, tested HTTP client rather than a third-party dependency.
``NoopTracingClient``
    Does nothing. The default until ``LANGFUSE_HOST`` and both keys are
    configured, so a dev/test run never depends on a Langfuse instance
    being reachable.

Every call swallows its own failures (logged, never raised): a trace
backend being unreachable must not fail an agent run or a GPU job — this is
a diagnostic side channel, not part of the contract with the customer.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

from app.core.config import settings

logger = logging.getLogger(__name__)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


class TracingClient(Protocol):
    def trace(
        self,
        *,
        trace_id: str,
        name: str,
        user_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None: ...

    def generation(
        self,
        *,
        trace_id: str,
        name: str,
        model: str,
        input: Any,
        output: Any,
        started: datetime,
        ended: datetime,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        level: str = "DEFAULT",
        status_message: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None: ...

    def span(
        self,
        *,
        trace_id: str,
        name: str,
        started: datetime,
        ended: datetime,
        metadata: dict[str, Any] | None = None,
    ) -> None: ...


# ---------------------------------------------------------------------------
# the real client
# ---------------------------------------------------------------------------
class LangfuseClient:
    """A self-hosted (or cloud) Langfuse instance's ingestion endpoint."""

    def __init__(
        self,
        host: str | None = None,
        public_key: str | None = None,
        secret_key: str | None = None,
        timeout: float = 5.0,
        http_client: Any | None = None,
    ) -> None:
        self.host = (host or settings.langfuse_host or "").rstrip("/")
        self.public_key = public_key or settings.langfuse_public_key
        self.secret_key = secret_key or settings.langfuse_secret_key
        self.timeout = timeout
        # Tests inject a client built on httpx.MockTransport; production
        # gets a real one per call, same pattern as the Google connectors.
        self._client = http_client

    def _send(self, event_type: str, body: dict[str, Any]) -> None:
        import httpx

        if not (self.host and self.public_key and self.secret_key):
            return
        event = {
            "id": uuid.uuid4().hex,
            "type": event_type,
            "timestamp": _iso(datetime.now(UTC)),
            "body": body,
        }
        client = self._client or httpx.Client(timeout=self.timeout)
        owns_client = self._client is None
        try:
            response = client.post(
                f"{self.host}/api/public/ingestion",
                json={"batch": [event]},
                auth=(self.public_key, self.secret_key),
            )
            response.raise_for_status()
        except Exception:  # noqa: BLE001 - tracing must never break the pipeline
            logger.warning(
                "langfuse ingestion failed", exc_info=True, extra={"event_type": event_type}
            )
        finally:
            if owns_client:
                client.close()

    def trace(
        self,
        *,
        trace_id: str,
        name: str,
        user_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._send(
            "trace-create",
            {"id": trace_id, "name": name, "userId": user_id, "metadata": metadata or {}},
        )

    def generation(
        self,
        *,
        trace_id: str,
        name: str,
        model: str,
        input: Any,
        output: Any,
        started: datetime,
        ended: datetime,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        level: str = "DEFAULT",
        status_message: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._send(
            "generation-create",
            {
                "id": uuid.uuid4().hex,
                "traceId": trace_id,
                "name": name,
                "model": model,
                "startTime": _iso(started),
                "endTime": _iso(ended),
                "input": input,
                "output": output,
                "usage": {"input": prompt_tokens, "output": completion_tokens, "unit": "TOKENS"},
                "level": level,
                "statusMessage": status_message,
                "metadata": metadata or {},
            },
        )

    def span(
        self,
        *,
        trace_id: str,
        name: str,
        started: datetime,
        ended: datetime,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._send(
            "span-create",
            {
                "id": uuid.uuid4().hex,
                "traceId": trace_id,
                "name": name,
                "startTime": _iso(started),
                "endTime": _iso(ended),
                "metadata": metadata or {},
            },
        )


# ---------------------------------------------------------------------------
# the stand-in
# ---------------------------------------------------------------------------
class NoopTracingClient:
    """No Langfuse configured; every call is a no-op."""

    def trace(self, **_: Any) -> None:
        return None

    def generation(self, **_: Any) -> None:
        return None

    def span(self, **_: Any) -> None:
        return None


_client: TracingClient | None = None


def build_client() -> TracingClient:
    if settings.langfuse_host and settings.langfuse_public_key and settings.langfuse_secret_key:
        return LangfuseClient()
    return NoopTracingClient()


def get_client() -> TracingClient:
    global _client
    if _client is None:
        _client = build_client()
    return _client


def set_client(client: TracingClient | None) -> None:
    """Test hook, and how the GPU worker installs a warmed client."""
    global _client
    _client = client


__all__ = [
    "LangfuseClient",
    "NoopTracingClient",
    "TracingClient",
    "build_client",
    "get_client",
    "set_client",
]
