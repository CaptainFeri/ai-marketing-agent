"""Langfuse tracing: the real ingestion client and its no-op stand-in.

No live Langfuse instance is needed — the real client is exercised against
``httpx.MockTransport``, the same way ``tests/test_connectors.py`` and
``tests/test_analytics_connectors.py`` test WordPress/Telegram/Search
Console/GA4.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from app.agents.tracing import (
    LangfuseClient,
    NoopTracingClient,
    build_client,
    get_client,
    set_client,
)
from app.core.config import settings


@pytest.fixture(autouse=True)
def _reset_tracer():
    set_client(None)
    yield
    set_client(None)


def _capturing_client(sink: list[httpx.Request]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        sink.append(request)
        return httpx.Response(207, json={"successes": [], "errors": []})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_a_trace_event_carries_the_right_shape() -> None:
    requests: list[httpx.Request] = []
    client = LangfuseClient(
        host="http://langfuse.local",
        public_key="pk-1",
        secret_key="sk-1",
        http_client=_capturing_client(requests),
    )
    client.trace(
        trace_id="pkg-1", name="package pkg-1", user_id="tenant-1", metadata={"locale": "fa"}
    )

    assert len(requests) == 1
    request = requests[0]
    assert request.url == "http://langfuse.local/api/public/ingestion"
    assert request.headers["authorization"].startswith("Basic ")

    import json

    body = json.loads(request.content)
    [event] = body["batch"]
    assert event["type"] == "trace-create"
    assert event["body"] == {
        "id": "pkg-1",
        "name": "package pkg-1",
        "userId": "tenant-1",
        "metadata": {"locale": "fa"},
    }


def test_a_generation_event_carries_usage_and_timing() -> None:
    requests: list[httpx.Request] = []
    client = LangfuseClient(
        host="http://langfuse.local",
        public_key="pk-1",
        secret_key="sk-1",
        http_client=_capturing_client(requests),
    )
    started = datetime(2026, 1, 1, tzinfo=UTC)
    ended = datetime(2026, 1, 1, 0, 0, 5, tzinfo=UTC)
    client.generation(
        trace_id="pkg-1",
        name="writer attempt 1",
        model="Qwen/Qwen3-30B-A3B",
        input="system+user",
        output="{}",
        started=started,
        ended=ended,
        prompt_tokens=100,
        completion_tokens=50,
    )

    import json

    [event] = json.loads(requests[0].content)["batch"]
    assert event["type"] == "generation-create"
    assert event["body"]["traceId"] == "pkg-1"
    assert event["body"]["usage"] == {"input": 100, "output": 50, "unit": "TOKENS"}
    assert event["body"]["startTime"] == started.isoformat()
    assert event["body"]["endTime"] == ended.isoformat()
    assert event["body"]["level"] == "DEFAULT"


def test_a_span_event_reports_gpu_seconds() -> None:
    requests: list[httpx.Request] = []
    client = LangfuseClient(
        host="http://langfuse.local",
        public_key="pk-1",
        secret_key="sk-1",
        http_client=_capturing_client(requests),
    )
    started = datetime(2026, 1, 1, tzinfo=UTC)
    ended = datetime(2026, 1, 1, 0, 0, 3, tzinfo=UTC)
    client.span(
        trace_id="pkg-1",
        name="gpu:image_flux",
        started=started,
        ended=ended,
        metadata={"gpu_seconds": 3.0},
    )

    import json

    [event] = json.loads(requests[0].content)["batch"]
    assert event["type"] == "span-create"
    assert event["body"]["name"] == "gpu:image_flux"
    assert event["body"]["metadata"] == {"gpu_seconds": 3.0}


def test_missing_configuration_sends_nothing() -> None:
    requests: list[httpx.Request] = []
    client = LangfuseClient(
        host="", public_key="", secret_key="", http_client=_capturing_client(requests)
    )
    client.trace(trace_id="x", name="x")
    assert requests == []


def test_an_unreachable_langfuse_is_swallowed_not_raised() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = LangfuseClient(
        host="http://langfuse.local",
        public_key="pk-1",
        secret_key="sk-1",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    # Must not raise: tracing is a diagnostic side channel, not part of the
    # contract with the customer.
    client.trace(trace_id="x", name="x")


def test_the_noop_client_does_nothing_and_never_raises() -> None:
    client = NoopTracingClient()
    client.trace(trace_id="x", name="x")
    client.generation(
        trace_id="x",
        name="x",
        model="m",
        input="i",
        output="o",
        started=datetime.now(UTC),
        ended=datetime.now(UTC),
    )
    client.span(trace_id="x", name="x", started=datetime.now(UTC), ended=datetime.now(UTC))


def test_build_client_is_noop_until_fully_configured(monkeypatch) -> None:
    monkeypatch.setattr(settings, "langfuse_host", None)
    monkeypatch.setattr(settings, "langfuse_public_key", None)
    monkeypatch.setattr(settings, "langfuse_secret_key", None)
    assert isinstance(build_client(), NoopTracingClient)

    monkeypatch.setattr(settings, "langfuse_host", "http://langfuse.local")
    monkeypatch.setattr(settings, "langfuse_public_key", "pk-1")
    monkeypatch.setattr(settings, "langfuse_secret_key", "sk-1")
    assert isinstance(build_client(), LangfuseClient)


def test_set_client_is_the_test_hook() -> None:
    sentinel = NoopTracingClient()
    set_client(sentinel)
    assert get_client() is sentinel
