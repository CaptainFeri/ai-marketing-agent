"""The agent runner, the prompts and the context they are built from."""

from __future__ import annotations

import json

import pytest

from app.agents.context import (
    BRIEF_FIELDS,
    DEPENDENCIES,
    AgentContext,
)
from app.agents.llm import LlmError, LlmResponse, SimulatedLlmClient, UnavailableLlmClient
from app.agents.prompts import LANGUAGE_NAMES, PROMPTS, budget, system_prompt, task_prompt
from app.agents.registry import AgentOutputError
from app.agents.runner import run_agent
from app.agents.simulation import sample_for_schema_name, sample_output
from app.agents.tracing import set_client as set_tracer
from app.db.enums import PipelineStep


def context(step: PipelineStep = PipelineStep.WRITER, **overrides) -> AgentContext:
    defaults = {
        "step": step,
        "locale": "fa",
        "title": "راهنمای خرید دریل برقی",
        "brief": {"brand": "آکمه", "voice": {"tone": ["حرفه‌ای"]}},
        "previous": {"strategist": {"angle": "راهنمای کاربردی"}},
    }
    defaults.update(overrides)
    return AgentContext(**defaults)


def client(**kwargs) -> SimulatedLlmClient:
    return SimulatedLlmClient(sample_factory=sample_for_schema_name, **kwargs)


# ---------------------------------------------------------------------------
# prompts
# ---------------------------------------------------------------------------
def test_every_step_has_a_prompt() -> None:
    assert set(PROMPTS) == set(PipelineStep)


@pytest.mark.parametrize("locale", ["fa", "en", "ar"])
def test_the_language_is_named_in_the_system_prompt(locale: str) -> None:
    """Decision D3: a package is written in its own language, not translated."""
    prompt = system_prompt(PipelineStep.WRITER, locale)
    assert LANGUAGE_NAMES[locale] in prompt
    assert "Do not translate" in prompt


def test_the_evidence_rule_reaches_every_agent() -> None:
    """Section 13's main risk: unsourced claims."""
    for step in PipelineStep:
        prompt = system_prompt(step, "fa")
        assert "source" in prompt.lower()
        assert "invent" in prompt.lower()


def test_generation_runs_hotter_than_checking() -> None:
    _, writer_temp = budget(PipelineStep.WRITER)
    _, qa_temp = budget(PipelineStep.QA)
    assert writer_temp > qa_temp


def test_the_writer_gets_the_largest_budget() -> None:
    writer_tokens, _ = budget(PipelineStep.WRITER)
    assert writer_tokens == max(budget(step)[0] for step in PipelineStep)


def test_the_marketizer_is_told_why_text_stays_off_the_image() -> None:
    """FLUX cannot render Persian or Arabic script (handoff section 5)."""
    assert "overlay_text" in task_prompt(PipelineStep.MARKETIZER, "fa")


# ---------------------------------------------------------------------------
# context
# ---------------------------------------------------------------------------
def test_every_step_declares_its_dependencies() -> None:
    assert set(DEPENDENCIES) == set(PipelineStep)
    assert set(BRIEF_FIELDS) == set(PipelineStep)


def test_the_first_step_depends_on_nothing() -> None:
    assert DEPENDENCIES[PipelineStep.RESEARCHER] == ()


def test_dependencies_only_point_backwards() -> None:
    """A stage cannot be shown the output of one that has not run yet."""
    from app.services.packages import TEXT_PIPELINE

    order = {step: index for index, step in enumerate(TEXT_PIPELINE)}
    for step, deps in DEPENDENCIES.items():
        if step not in order:
            continue
        for dep in deps:
            assert order[dep] < order[step], f"{step.value} depends on later {dep.value}"


def test_the_rendered_prompt_carries_the_brief_and_the_dependencies() -> None:
    rendered = context().render()
    assert "آکمه" in rendered
    assert "strategist" in rendered
    assert "راهنمای خرید دریل برقی" in rendered


def test_feedback_is_rendered_as_something_to_fix() -> None:
    rendered = context(feedback=["آمار بدون منبع حذف شود"]).render()
    assert "Must be fixed" in rendered
    assert "آمار بدون منبع" in rendered


def test_no_feedback_section_appears_on_a_first_pass() -> None:
    assert "Must be fixed" not in context().render()


def test_the_planner_is_shown_what_was_already_published() -> None:
    rendered = context(step=PipelineStep.TOPIC_PLANNER, existing_titles=["مقاله قبلی"]).render()
    assert "Already published" in rendered
    assert "مقاله قبلی" in rendered


def test_the_marketizer_is_told_the_video_mode() -> None:
    rendered = context(step=PipelineStep.MARKETIZER, video_mode="face").render()
    assert "Video mode: face" in rendered


# ---------------------------------------------------------------------------
# the runner
# ---------------------------------------------------------------------------
def test_a_clean_run_takes_one_attempt() -> None:
    run = run_agent(PipelineStep.WRITER, context(), client())
    assert run.attempts == 1
    assert run.payload["title"]
    assert run.response.guided is True


def test_a_malformed_answer_is_retried_with_the_errors_attached() -> None:
    """Retrying an identical prompt usually fails the same way."""
    llm = client(fail_first=1)
    run = run_agent(PipelineStep.WRITER, context(), llm)
    assert run.attempts == 2


def test_the_retry_prompt_explains_what_was_wrong() -> None:
    seen: list[str] = []

    class Recording(SimulatedLlmClient):
        def complete(self, **kwargs):
            seen.append(kwargs["user"])
            return super().complete(**kwargs)

    run_agent(
        PipelineStep.WRITER,
        context(),
        Recording(sample_factory=sample_for_schema_name, fail_first=1),
    )
    assert len(seen) == 2
    assert "previous answer was rejected" in seen[1]
    assert "Return the corrected JSON object only." in seen[1]


def test_a_retry_samples_more_conservatively() -> None:
    temperatures: list[float] = []

    class Recording(SimulatedLlmClient):
        def complete(self, **kwargs):
            temperatures.append(kwargs["temperature"])
            return super().complete(**kwargs)

    run_agent(
        PipelineStep.WRITER,
        context(),
        Recording(sample_factory=sample_for_schema_name, fail_first=1),
    )
    assert temperatures[1] < temperatures[0]


def test_giving_up_names_the_attempts_and_the_last_error() -> None:
    with pytest.raises(AgentOutputError) as excinfo:
        run_agent(PipelineStep.WRITER, context(), client(fail_first=99), max_attempts=2)
    assert excinfo.value.details["attempts"] == 2
    assert "2 attempts" in excinfo.value.message


def test_the_schema_is_sent_with_the_request() -> None:
    """Guided decoding is the point: the model is steered, not just checked."""
    captured: dict = {}

    class Recording(SimulatedLlmClient):
        def complete(self, **kwargs):
            captured.update(kwargs)
            return super().complete(**kwargs)

    run_agent(
        PipelineStep.QA, context(PipelineStep.QA), Recording(sample_factory=sample_for_schema_name)
    )
    assert captured["schema_name"] == PipelineStep.QA.value
    assert captured["schema"]["additionalProperties"] is False


def test_an_unconfigured_model_server_says_so() -> None:
    with pytest.raises(LlmError) as excinfo:
        run_agent(PipelineStep.WRITER, context(), UnavailableLlmClient())
    assert "LLM_BASE_URL" in str(excinfo.value)


@pytest.mark.parametrize("step", list(PipelineStep))
def test_every_agent_runs_end_to_end_against_the_simulator(step: PipelineStep) -> None:
    run = run_agent(step, context(step), client())
    # The payload is the validated model dumped back out, so it carries the
    # contract's defaults as well as what the sample supplied. Every field the
    # sample did set must survive the round trip unchanged.
    sample = sample_output(step)
    assert set(sample) <= set(run.payload), f"{step.value} lost fields in the round trip"
    # Re-validating the dumped payload is what the next stage will do with it.
    from app.agents.registry import validate_agent_output

    validate_agent_output(step, run.payload)


# ---------------------------------------------------------------------------
# the vLLM client's request shape
# ---------------------------------------------------------------------------
def test_the_vllm_request_carries_the_schema_in_the_configured_form() -> None:
    """Sending it the wrong way means unconstrained generation, silently."""
    import httpx

    from app.agents.llm import VllmClient

    captured: dict = {}

    def fake_post(url, json=None, timeout=None):  # noqa: A002
        captured["url"] = url
        captured["body"] = json
        return httpx.Response(
            200,
            json={
                "model": "test",
                "choices": [{"message": {"content": "{}"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
            request=httpx.Request("POST", url),
        )

    original = httpx.post
    httpx.post = fake_post
    try:
        modern = VllmClient(base_url="http://x", model="m", guided_mode="response_format")
        modern.complete(system="s", user="u", schema={"type": "object"}, schema_name="qa")
        assert captured["url"].endswith("/v1/chat/completions")
        assert captured["body"]["response_format"]["json_schema"]["name"] == "qa"

        legacy = VllmClient(base_url="http://x", model="m", guided_mode="guided_json")
        legacy.complete(system="s", user="u", schema={"type": "object"}, schema_name="qa")
        assert captured["body"]["guided_json"] == {"type": "object"}
        assert "response_format" not in captured["body"]
    finally:
        httpx.post = original


def test_a_dead_model_server_becomes_an_llm_error() -> None:
    import httpx

    from app.agents.llm import VllmClient

    def fake_post(url, json=None, timeout=None):  # noqa: A002
        raise httpx.ConnectError("connection refused")

    original = httpx.post
    httpx.post = fake_post
    try:
        with pytest.raises(LlmError) as excinfo:
            VllmClient(base_url="http://x", model="m").complete(system="s", user="u")
        assert "ConnectError" in str(excinfo.value)
    finally:
        httpx.post = original


# ---------------------------------------------------------------------------
# the simulator
# ---------------------------------------------------------------------------
def test_simulated_output_is_obviously_simulated() -> None:
    """A simulated run that looked finished would be worse than one that does not."""
    text = json.dumps(sample_output(PipelineStep.WRITER), ensure_ascii=False)
    assert "[simulated]" in text


def test_simulated_qa_passes_so_a_demo_reaches_gate_one() -> None:
    report = sample_output(PipelineStep.QA)
    assert report["verdict"] == "pass"
    assert report["score"] >= 0.7


def test_the_simulator_is_looked_up_by_schema_name() -> None:
    assert sample_for_schema_name("writer") == sample_output(PipelineStep.WRITER)
    with pytest.raises(KeyError):
        sample_for_schema_name("not-a-step")


def test_the_simulated_client_reports_token_counts() -> None:
    response: LlmResponse = client().complete(system="s", user="u", schema_name="qa")
    assert response.completion_tokens > 0
    assert response.model == "simulated"


def test_gpu_time_is_charged_for_rejected_attempts_too() -> None:
    """A retry occupied the card just as much as the attempt that worked."""
    one_shot = run_agent(PipelineStep.WRITER, context(), client())
    retried = run_agent(PipelineStep.WRITER, context(), client(fail_first=1))

    assert retried.attempts == 2
    assert retried.model_seconds > one_shot.model_seconds
    # The last response alone would understate it.
    assert retried.model_seconds > retried.response.seconds


def test_the_simulator_reports_a_plausible_duration() -> None:
    """Zero seconds would leave the quota mechanism untested in a demo."""
    run = run_agent(PipelineStep.WRITER, context(), client())
    assert run.model_seconds > 0


# ---------------------------------------------------------------------------
# tracing
# ---------------------------------------------------------------------------
class _RecordingTracer:
    def __init__(self) -> None:
        self.traces: list[dict] = []
        self.generations: list[dict] = []

    def trace(self, **kwargs):
        self.traces.append(kwargs)

    def generation(self, **kwargs):
        self.generations.append(kwargs)

    def span(self, **kwargs):
        pass


@pytest.fixture(autouse=True)
def _reset_tracer():
    set_tracer(None)
    yield
    set_tracer(None)


def test_a_clean_run_emits_one_trace_and_one_generation() -> None:
    import uuid

    tracer = _RecordingTracer()
    set_tracer(tracer)
    package_id = uuid.uuid4()
    run_agent(PipelineStep.WRITER, context(package_id=package_id), client())

    assert len(tracer.traces) == 1
    assert tracer.traces[0]["trace_id"] == str(package_id)
    assert len(tracer.generations) == 1
    assert tracer.generations[0]["trace_id"] == str(package_id)
    assert tracer.generations[0].get("level", "DEFAULT") == "DEFAULT"


def test_a_rejected_attempt_still_gets_a_generation_marked_as_a_warning() -> None:
    tracer = _RecordingTracer()
    set_tracer(tracer)
    run_agent(PipelineStep.WRITER, context(), client(fail_first=1))

    assert len(tracer.generations) == 2
    assert tracer.generations[0]["level"] == "WARNING"
    assert tracer.generations[0]["status_message"]
    assert tracer.generations[1].get("level", "DEFAULT") == "DEFAULT"


def test_every_step_of_a_package_shares_one_trace_id() -> None:
    import uuid

    tracer = _RecordingTracer()
    set_tracer(tracer)
    package_id = uuid.uuid4()
    run_agent(
        PipelineStep.RESEARCHER, context(PipelineStep.RESEARCHER, package_id=package_id), client()
    )
    run_agent(PipelineStep.WRITER, context(PipelineStep.WRITER, package_id=package_id), client())

    trace_ids = {call["trace_id"] for call in tracer.generations}
    assert trace_ids == {str(package_id)}


def test_a_run_with_no_package_still_gets_a_trace_of_its_own() -> None:
    tracer = _RecordingTracer()
    set_tracer(tracer)
    run_agent(PipelineStep.BRIEF_ASSISTANT, context(PipelineStep.BRIEF_ASSISTANT), client())

    assert len(tracer.traces) == 1
    assert tracer.traces[0]["trace_id"]


def test_an_unreachable_model_server_still_gets_traced_as_an_error() -> None:
    tracer = _RecordingTracer()
    set_tracer(tracer)
    with pytest.raises(LlmError):
        run_agent(PipelineStep.WRITER, context(), UnavailableLlmClient())

    assert len(tracer.generations) == 1
    assert tracer.generations[0]["level"] == "ERROR"
