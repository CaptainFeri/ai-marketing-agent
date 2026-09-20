"""Which model validates which pipeline step, and the schemas derived from it.

Two callers:

* the agent runner, which passes ``json_schema_for(step)`` to vLLM as a guided
  decoding constraint so the model is steered into the shape;
* :func:`validate_agent_output`, which checks what came back before it is
  written to ``StepRun.output_json``.

Guided decoding makes a malformed output unlikely, not impossible — a
truncated generation still produces invalid JSON — so both run.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from app.agents.contracts import (
    DraftOutput,
    GeoOptimizedOutput,
    MarketizedOutput,
    QaReport,
    ResearchOutput,
    SeoOptimizedOutput,
    StrategyOutput,
    TopicPlanOutput,
)
from app.core.errors import AppError
from app.db.enums import PipelineStep

#: The contract for every step of the pipeline.
AGENT_OUTPUTS: dict[PipelineStep, type[BaseModel]] = {
    PipelineStep.RESEARCHER: ResearchOutput,
    PipelineStep.STRATEGIST: StrategyOutput,
    PipelineStep.WRITER: DraftOutput,
    PipelineStep.GEO_OPTIMIZER: GeoOptimizedOutput,
    PipelineStep.SEO_OPTIMIZER: SeoOptimizedOutput,
    PipelineStep.QA: QaReport,
    PipelineStep.MARKETIZER: MarketizedOutput,
    PipelineStep.TOPIC_PLANNER: TopicPlanOutput,
}

#: Where ``scripts/export_schemas.py`` writes the generated files.
SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas"


class AgentOutputError(AppError):
    """An agent returned something that does not match its contract."""

    status_code = 422
    code = "agent_output_invalid"


def model_for(step: PipelineStep) -> type[BaseModel]:
    try:
        return AGENT_OUTPUTS[step]
    except KeyError:  # pragma: no cover - every step is mapped
        raise AgentOutputError(f"no output contract is defined for step {step.value!r}") from None


def json_schema_for(step: PipelineStep) -> dict[str, Any]:
    """The JSON Schema to constrain generation with."""
    return model_for(step).model_json_schema()


def schema_path(step: PipelineStep) -> Path:
    return SCHEMA_DIR / f"{step.value}.schema.json"


def validate_agent_output(step: PipelineStep, payload: Any) -> BaseModel:
    """Parse and validate one agent's output.

    Accepts a dict or the raw JSON string the model produced.  Raises
    :class:`AgentOutputError` with the offending fields listed, so a failing
    step can be retried with the errors fed back into the prompt rather than
    with the same prompt again.
    """
    model = model_for(step)

    if isinstance(payload, (str, bytes)):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise AgentOutputError(
                f"{step.value} did not return valid JSON: {exc}",
                details={"step": step.value, "position": exc.pos},
            ) from exc

    if not isinstance(payload, dict):
        raise AgentOutputError(
            f"{step.value} returned {type(payload).__name__}, expected a JSON object",
            details={"step": step.value},
        )

    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise AgentOutputError(
            f"{step.value} output does not match its contract",
            details={
                "step": step.value,
                "errors": [
                    {"loc": [str(part) for part in error["loc"]], "msg": error["msg"]}
                    for error in exc.errors()
                ],
            },
        ) from exc


def export_schemas(directory: Path | None = None) -> list[Path]:
    """Write every schema to disk.  Used by ``scripts/export_schemas.py``.

    The files are committed so the agent prompts, the panel and any external
    tooling can read the contract without importing the application.
    ``tests/test_agent_contracts.py`` fails if they fall out of date.
    """
    directory = directory or SCHEMA_DIR
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for step in AGENT_OUTPUTS:
        path = directory / f"{step.value}.schema.json"
        path.write_text(
            json.dumps(json_schema_for(step), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        written.append(path)
    return written
