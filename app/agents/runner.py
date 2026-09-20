"""Running one agent.

The loop is: render the prompt, ask the model with the schema attached,
validate, and on failure retry with the errors appended to the prompt. Feeding
the errors back matters — retrying an identical prompt against a model that
already failed it usually fails the same way.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from pydantic import BaseModel

from app.agents.context import AgentContext
from app.agents.llm import LlmClient, LlmError, LlmResponse, get_client
from app.agents.prompts import budget, system_prompt
from app.agents.registry import AgentOutputError, json_schema_for
from app.core.config import settings
from app.db.enums import PipelineStep

logger = logging.getLogger(__name__)


@dataclass
class AgentRun:
    step: PipelineStep
    output: BaseModel
    response: LlmResponse
    attempts: int
    #: Wall clock across every attempt, for diagnostics.
    seconds: float
    #: Time the model spent generating, summed over every attempt including
    #: the rejected ones — they occupied the card too. This is what the quota
    #: ledger is charged, not the wall clock, which also covers validation and
    #: whatever the worker was doing between calls.
    model_seconds: float = 0.0

    @property
    def payload(self) -> dict:
        return self.output.model_dump(mode="json")


def run_agent(
    step: PipelineStep,
    context: AgentContext,
    client: LlmClient | None = None,
    *,
    max_attempts: int | None = None,
) -> AgentRun:
    """Run one agent to a validated output, or raise.

    Raises :class:`AgentOutputError` when every attempt produced something the
    contract rejects, and :class:`LlmError` when the model server itself could
    not be reached — the caller treats those differently, because only the
    second is worth retrying later at the queue level.
    """
    client = client or get_client()
    max_attempts = max_attempts or settings.agent_max_attempts
    schema = json_schema_for(step)
    system = system_prompt(step, context.locale)
    base_user = context.render()
    max_tokens, temperature = budget(step)

    started = time.monotonic()
    user = base_user
    last_error: AgentOutputError | None = None
    model_seconds = 0.0

    for attempt in range(1, max_attempts + 1):
        response = client.complete(
            system=system,
            user=user,
            schema=schema,
            schema_name=step.value,
            max_tokens=max_tokens,
            # Nudge the temperature down on a retry: the first sampling
            # already produced something malformed.
            temperature=temperature if attempt == 1 else max(0.1, temperature / 2),
        )
        model_seconds += response.seconds

        try:
            from app.agents.registry import validate_agent_output

            output = validate_agent_output(step, response.text)
        except AgentOutputError as exc:
            last_error = exc
            logger.warning(
                "agent output rejected",
                extra={
                    "step": step.value,
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                    "reason": exc.message,
                },
            )
            user = f"{base_user}\n\n{_retry_note(exc)}"
            continue

        return AgentRun(
            step=step,
            output=output,
            response=response,
            attempts=attempt,
            seconds=time.monotonic() - started,
            model_seconds=model_seconds,
        )

    assert last_error is not None  # the loop runs at least once
    raise AgentOutputError(
        f"{step.value} failed to produce a valid output in {max_attempts} attempts: "
        f"{last_error.message}",
        details={**last_error.details, "attempts": max_attempts},
    )


def _retry_note(error: AgentOutputError) -> str:
    """The correction appended to the prompt on a retry."""
    lines = ["## Your previous answer was rejected", error.message]
    errors = error.details.get("errors")
    if errors:
        lines.append("Problems found:")
        lines.extend(f"- {'.'.join(item['loc'])}: {item['msg']}" for item in errors)
    lines.append("Return the corrected JSON object only.")
    return "\n".join(lines)


__all__ = ["AgentRun", "LlmError", "run_agent"]
