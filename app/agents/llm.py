"""Talking to the language model.

The platform runs vLLM locally (decision D1 — nothing leaves the server), and
the one thing worth insisting on is that the schema goes *with* the request:
vLLM constrains generation to it, so the model is steered into the contract
rather than merely checked against it afterwards.

Three implementations, mirroring ``app.worker.gpu_runtime``:

``VllmClient``
    The real one. Speaks the OpenAI-compatible API vLLM serves.
``SimulatedLlmClient``
    Returns contract-valid output built from the context, so the whole
    pipeline runs before any weights exist.
``UnavailableLlmClient``
    Fails loudly when a real client was expected but none is configured.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class LlmResponse:
    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0
    #: True when the server confirmed it constrained generation to the schema.
    guided: bool = False


class LlmError(RuntimeError):
    """The model server could not be reached, or refused the request."""


class LlmClient(Protocol):
    def complete(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any] | None = None,
        schema_name: str = "output",
        max_tokens: int = 2048,
        temperature: float = 0.4,
    ) -> LlmResponse: ...


# ---------------------------------------------------------------------------
# the real client
# ---------------------------------------------------------------------------
class VllmClient:
    """vLLM's OpenAI-compatible endpoint.

    Structured output is requested as ``response_format: json_schema``, which
    is what recent vLLM accepts. Older builds want ``guided_json`` instead, so
    the mode is configurable rather than hard-coded — getting it wrong means
    unconstrained generation, which fails later and further away.
    """

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        guided_mode: str | None = None,
    ) -> None:
        self.base_url = (base_url or settings.llm_base_url).rstrip("/")
        self.model = model or settings.llm_model
        self.timeout = timeout or settings.llm_timeout_seconds
        self.guided_mode = guided_mode or settings.llm_guided_mode

    def complete(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any] | None = None,
        schema_name: str = "output",
        max_tokens: int = 2048,
        temperature: float = 0.4,
    ) -> LlmResponse:
        import httpx

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        guided = False
        if schema is not None:
            if self.guided_mode == "guided_json":
                payload["guided_json"] = schema
            else:
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": schema_name, "schema": schema, "strict": True},
                }
            guided = True

        started = time.monotonic()
        try:
            response = httpx.post(
                f"{self.base_url}/v1/chat/completions",
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            body = response.json()
        except Exception as exc:  # noqa: BLE001 - surfaced as LlmError
            raise LlmError(f"vLLM request failed: {type(exc).__name__}: {exc}") from exc
        elapsed = time.monotonic() - started

        try:
            text = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LlmError(f"unexpected response shape from vLLM: {body!r}") from exc

        usage = body.get("usage") or {}
        return LlmResponse(
            text=text or "",
            model=body.get("model", self.model),
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
            seconds=elapsed,
            guided=guided,
        )


# ---------------------------------------------------------------------------
# the stand-in
# ---------------------------------------------------------------------------
@dataclass
class SimulatedLlmClient:
    """Returns contract-valid output without a model.

    The payload is built from the context it was handed, so a simulated run
    produces something recognisably derived from the brand brief rather than
    lorem ipsum — which is what makes it useful for demonstrating the gates
    and the panel before phase 0 finishes.

    ``sample_factory`` is injected by :mod:`app.agents.runner`; this module
    stays unaware of the contracts so the dependency runs one way only.
    """

    sample_factory: Any = None
    model: str = "simulated"
    #: Set to fail the first N calls, to exercise the retry path.
    fail_first: int = 0
    #: Roughly what a 4-bit Qwen3-30B-A3B sustains on a 3090 Ti. Used to
    #: report a plausible duration without actually waiting for it, so the
    #: quota mechanism sees lifelike numbers in a simulated run.
    tokens_per_second: float = 45.0
    _calls: int = field(default=0, init=False)

    def complete(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any] | None = None,
        schema_name: str = "output",
        max_tokens: int = 2048,
        temperature: float = 0.4,
    ) -> LlmResponse:
        self._calls += 1
        if self._calls <= self.fail_first:
            # Truncated JSON: exactly what a hit token limit looks like.
            # It still cost GPU time, which is why it carries a duration.
            return LlmResponse(
                text='{"score": 0.4, ',
                model=self.model,
                completion_tokens=max_tokens,
                seconds=round(max_tokens / self.tokens_per_second, 2),
                guided=False,
            )

        if self.sample_factory is None:
            raise LlmError("SimulatedLlmClient needs a sample_factory")
        payload = self.sample_factory(schema_name)
        text = json.dumps(payload, ensure_ascii=False)
        completion_tokens = max(1, len(text) // 4)
        return LlmResponse(
            text=text,
            model=self.model,
            prompt_tokens=len(system) // 4 + len(user) // 4,
            completion_tokens=completion_tokens,
            seconds=round(completion_tokens / self.tokens_per_second, 2),
            guided=schema is not None,
        )


class UnavailableLlmClient:
    """No model server configured; say so rather than produce nothing useful."""

    _MESSAGE = (
        "no language model is configured. Start vLLM and set LLM_BASE_URL, or "
        "set LLM_CLIENT=simulated to run the pipeline without a model."
    )

    def complete(self, **_: Any) -> LlmResponse:
        raise LlmError(self._MESSAGE)


_client: LlmClient | None = None


def build_client(name: str | None = None) -> LlmClient:
    name = name or settings.llm_client
    if name == "vllm":
        return VllmClient()
    if name == "simulated":
        from app.agents.simulation import sample_for_schema_name

        return SimulatedLlmClient(sample_factory=sample_for_schema_name)
    return UnavailableLlmClient()


def get_client() -> LlmClient:
    global _client
    if _client is None:
        _client = build_client()
    return _client


def set_client(client: LlmClient | None) -> None:
    """Test hook, and how the GPU worker installs a warmed client."""
    global _client
    _client = client
