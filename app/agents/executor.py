"""Running an agent as a GPU job.

The bridge between the queue and the agents: the GPU worker hands this a
leased ``llm_text`` job, and it rebuilds the context from the database, runs
the agent, validates the output and writes it back to the ``StepRun``.

Rebuilding the context from the database rather than carrying it in the job
payload is what makes "re-run from any step" work (handoff section 11, phase 1
week 3): a step run again a week later sees exactly what it would have seen
the first time.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.agents.context import build_context
from app.agents.llm import LlmClient
from app.agents.runner import run_agent
from app.core.config import settings
from app.db.enums import PipelineStep, StepStatus
from app.db.models import ContentPackage, GpuJob, StepRun
from app.services.packages import apply_agent_output

logger = logging.getLogger(__name__)

#: Prompts are kept for debugging and for Langfuse, but a 7000-token writer
#: prompt in every row adds up; keep the head, which is where the instructions
#: and the brief are.
PROMPT_LOG_LIMIT = 20_000


@dataclass
class StepResult:
    step: PipelineStep
    #: GPU time to charge: the model's generation time across every attempt.
    seconds: float
    payload: dict
    attempts: int
    model: str
    #: Set when the output sends the package back to an earlier step.
    rewound_to: PipelineStep | None = None


def execute_step_job(session: Session, job: GpuJob, client: LlmClient | None = None) -> StepResult:
    """Run the agent this job was queued for.

    Raises rather than swallowing: the caller books the failure against the
    job, which is what decides retry versus give up.
    """
    if job.step_run_id is None:
        raise ValueError(f"gpu job {job.id} has no step run to execute")

    step_run = session.get(StepRun, job.step_run_id)
    if step_run is None:
        raise LookupError(f"step run {job.step_run_id} not found")
    package = session.get(ContentPackage, step_run.package_id)
    if package is None:
        raise LookupError(f"content package {step_run.package_id} not found")

    step = step_run.step
    context = build_context(session, package, step)

    step_run.status = StepStatus.RUNNING
    step_run.started_at = step_run.started_at or datetime.now(UTC)
    session.flush()

    try:
        run = run_agent(step, context, client)
    except Exception as exc:
        step_run.status = StepStatus.FAILED
        step_run.error = f"{type(exc).__name__}: {exc}"[:4000]
        step_run.finished_at = datetime.now(UTC)
        session.flush()
        raise

    payload = run.payload
    step_run.status = StepStatus.SUCCEEDED
    step_run.output_json = payload
    rendered = context.render()
    step_run.input_json = {
        "dependencies": list(context.previous),
        "feedback": context.feedback,
        "prompt": rendered[:PROMPT_LOG_LIMIT] if settings.debug else None,
        "prompt_chars": len(rendered),
    }
    step_run.model = run.response.model
    step_run.prompt_tokens = run.response.prompt_tokens or None
    step_run.completion_tokens = run.response.completion_tokens or None
    step_run.finished_at = datetime.now(UTC)
    step_run.error = None

    rewound = apply_agent_output(session, package, step, payload)
    session.flush()

    logger.info(
        "agent finished",
        extra={
            "step": step.value,
            "package_id": str(package.id),
            "attempts": run.attempts,
            "seconds": round(run.seconds, 2),
            "rewound_to": rewound.value if rewound else None,
        },
    )
    return StepResult(
        step=step,
        seconds=run.model_seconds,
        payload=payload,
        attempts=run.attempts,
        model=run.response.model,
        rewound_to=rewound,
    )
