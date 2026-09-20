"""The agent output contracts (handoff section 11, phase 0).

These need no database: the contracts are pure Pydantic.
"""

from __future__ import annotations

import json

import pytest

from app.agents import AgentOutputError, json_schema_for, model_for, validate_agent_output
from app.agents.registry import AGENT_OUTPUTS, export_schemas, schema_path
from app.db.enums import PipelineStep

# ---------------------------------------------------------------------------
# minimal valid payloads, one per agent
# ---------------------------------------------------------------------------
VALID: dict[PipelineStep, dict] = {
    PipelineStep.RESEARCHER: {
        "summary": "بررسی بازار دریل برقی",
        "keywords": [
            {"term": "دریل برقی", "locale": "fa", "intent": "commercial", "is_primary": True},
            {"term": "بهترین دریل", "locale": "fa"},
        ],
        "audience_questions": ["کدام دریل برای کار خانگی مناسب است؟"],
        "claims": [
            {
                "id": "c1",
                "statement": "فروش ابزار برقی در ۱۴۰۴ رشد داشت",
                "source_url": "https://example.com/report",
                "confidence": 0.8,
            }
        ],
        "competitors": [{"name": "رقیب الف", "url": "https://example.com", "gaps": ["قیمت"]}],
        "entities": ["دریل", "ابزار برقی"],
    },
    PipelineStep.STRATEGIST: {
        "angle": "راهنمای خرید بر اساس کاربرد",
        "target_persona": "خریدار خانگی",
        "funnel_stage": "consideration",
        "primary_keyword": "دریل برقی",
        "secondary_keywords": ["دریل شارژی"],
        "outline": [{"heading": "انواع دریل", "level": 2, "talking_points": ["چکشی", "شارژی"]}],
        "word_count_target": 1400,
    },
    PipelineStep.WRITER: {
        "title": "راهنمای خرید دریل برقی",
        "excerpt": "چطور دریل مناسب را انتخاب کنیم.",
        "sections": [{"heading": "انواع دریل", "level": 2, "body": "متن بخش اول."}],
        "claim_ids_used": ["c1"],
        "word_count": 1420,
    },
    PipelineStep.GEO_OPTIMIZER: {
        "answer_blocks": [
            {
                "question": "بهترین دریل برای خانه کدام است؟",
                "answer": "دریل شارژی ۱۲ ولت برای کار خانگی کافی است.",
                "claim_ids": ["c1"],
            }
        ],
        "key_takeaways": ["برای کار خانگی دریل شارژی کافی است"],
        "schema_org": {"@type": "Article"},
    },
    PipelineStep.SEO_OPTIMIZER: {
        "meta_title": "راهنمای خرید دریل برقی",
        "meta_description": "انتخاب دریل مناسب بر اساس کاربرد و بودجه.",
        "slug": "راهنمای-خرید-دریل-برقی",
        "h1": "راهنمای خرید دریل برقی",
        "primary_keyword": "دریل برقی",
        "image_alts": [{"slot": "hero", "alt_text": "یک دریل برقی روی میز کار"}],
    },
    PipelineStep.QA: {
        "score": 0.82,
        "verdict": "pass",
        "issues": [{"severity": "minor", "category": "readability", "description": "جمله بلند"}],
        "brand_voice_match": 0.8,
        "readability": 0.75,
    },
    PipelineStep.MARKETIZER: {
        "variants": [
            {
                "channel": "telegram",
                "hook": "دریل مناسب را چطور بخریم؟",
                "body": "متن پست تلگرام.",
                "hashtags": ["#ابزار"],
            }
        ],
        "video_script": [{"text": "سلام، امروز درباره دریل حرف می‌زنیم.", "seconds": 4.0}],
    },
    PipelineStep.TOPIC_PLANNER: {
        "topics": [
            {
                "title": "راهنمای خرید دریل برقی",
                "locale": "fa",
                "keywords": ["دریل برقی"],
                "priority": 10,
                "planned_for": "2026-10-01",
            }
        ]
    },
}


# ---------------------------------------------------------------------------
# coverage and schema hygiene
# ---------------------------------------------------------------------------
def test_every_pipeline_step_has_a_contract() -> None:
    """A step without one would write unvalidated JSON into StepRun.output."""
    assert set(AGENT_OUTPUTS) == set(PipelineStep)


def test_every_contract_has_a_sample_payload() -> None:
    assert set(VALID) == set(AGENT_OUTPUTS)


@pytest.mark.parametrize("step", list(AGENT_OUTPUTS))
def test_the_sample_payload_validates(step: PipelineStep) -> None:
    result = validate_agent_output(step, VALID[step])
    assert isinstance(result, model_for(step))


@pytest.mark.parametrize("step", list(AGENT_OUTPUTS))
def test_a_json_string_is_accepted(step: PipelineStep) -> None:
    """Agents return text; the caller should not have to parse it first."""
    validate_agent_output(step, json.dumps(VALID[step], ensure_ascii=False))


@pytest.mark.parametrize("step", list(AGENT_OUTPUTS))
def test_unknown_fields_are_rejected(step: PipelineStep) -> None:
    """extra='forbid' is what stops a drifting agent smuggling fields along."""
    payload = dict(VALID[step], surprise_field="x")
    with pytest.raises(AgentOutputError):
        validate_agent_output(step, payload)


def _walk(schema: dict, seen: set[int] | None = None):
    seen = seen if seen is not None else set()
    if id(schema) in seen:
        return
    seen.add(id(schema))
    yield schema
    for value in schema.values():
        if isinstance(value, dict):
            yield from _walk(value, seen)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    yield from _walk(item, seen)


@pytest.mark.parametrize("step", list(AGENT_OUTPUTS))
def test_every_object_in_the_schema_is_closed(step: PipelineStep) -> None:
    schema = json_schema_for(step)
    for node in _walk(schema):
        if node.get("type") == "object" and "properties" in node:
            assert node.get("additionalProperties") is False, (
                f"{step.value} has an open object in its schema"
            )


@pytest.mark.parametrize("step", list(AGENT_OUTPUTS))
def test_the_schema_avoids_formats_grammar_backends_handle_badly(
    step: PipelineStep,
) -> None:
    """Dates and URLs are patterns or plain strings, not `format` keywords.

    Guided decoding backends support `format` inconsistently; a schema that
    silently stops constraining is worse than one that never tried.
    """
    for node in _walk(json_schema_for(step)):
        assert node.get("format") not in {"date", "date-time", "uri", "email"}, (
            f"{step.value} uses an unreliable format keyword"
        )


# ---------------------------------------------------------------------------
# exported files must not drift from the models
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("step", list(AGENT_OUTPUTS))
def test_the_committed_schema_matches_the_model(step: PipelineStep) -> None:
    path = schema_path(step)
    assert path.exists(), f"run scripts/export_schemas.py — {path.name} is missing"
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk == json_schema_for(step), f"{path.name} is stale; run scripts/export_schemas.py"


def test_export_writes_one_file_per_agent(tmp_path) -> None:
    written = export_schemas(tmp_path)
    assert len(written) == len(AGENT_OUTPUTS)
    assert all(path.exists() for path in written)


# ---------------------------------------------------------------------------
# the rules worth having
# ---------------------------------------------------------------------------
def test_malformed_json_is_reported_with_its_position() -> None:
    with pytest.raises(AgentOutputError) as excinfo:
        validate_agent_output(PipelineStep.QA, '{"score": 0.5, ')
    assert "valid JSON" in excinfo.value.message
    assert "position" in excinfo.value.details


def test_a_json_array_is_not_an_agent_output() -> None:
    with pytest.raises(AgentOutputError) as excinfo:
        validate_agent_output(PipelineStep.QA, "[1, 2, 3]")
    assert "expected a JSON object" in excinfo.value.message


def test_validation_errors_name_the_offending_field() -> None:
    """The errors are fed back into the retry prompt, so they have to be usable."""
    payload = dict(VALID[PipelineStep.QA], score=5.0)
    with pytest.raises(AgentOutputError) as excinfo:
        validate_agent_output(PipelineStep.QA, payload)
    locs = [error["loc"] for error in excinfo.value.details["errors"]]
    assert ["score"] in locs


def test_a_claim_without_a_real_url_is_rejected() -> None:
    """Section 13: every statistic needs a source, and 'internal' is not one."""
    payload = json.loads(json.dumps(VALID[PipelineStep.RESEARCHER]))
    payload["claims"][0]["source_url"] = "internal knowledge"
    with pytest.raises(AgentOutputError):
        validate_agent_output(PipelineStep.RESEARCHER, payload)


def test_two_primary_keywords_are_rejected() -> None:
    payload = json.loads(json.dumps(VALID[PipelineStep.RESEARCHER]))
    payload["keywords"][1]["is_primary"] = True
    with pytest.raises(AgentOutputError):
        validate_agent_output(PipelineStep.RESEARCHER, payload)


def test_qa_cannot_pass_a_draft_that_has_a_blocker() -> None:
    payload = json.loads(json.dumps(VALID[PipelineStep.QA]))
    payload["issues"][0]["severity"] = "blocker"
    with pytest.raises(AgentOutputError) as excinfo:
        validate_agent_output(PipelineStep.QA, payload)
    assert "blocker" in str(excinfo.value.details)


def test_qa_may_report_a_blocker_when_it_asks_for_revision() -> None:
    payload = json.loads(json.dumps(VALID[PipelineStep.QA]))
    payload["issues"][0]["severity"] = "blocker"
    payload["verdict"] = "revise"
    assert validate_agent_output(PipelineStep.QA, payload).verdict == "revise"


def test_an_unpaired_ab_arm_is_rejected() -> None:
    """A lone 'a' arm is an experiment that can never be concluded."""
    payload = json.loads(json.dumps(VALID[PipelineStep.MARKETIZER]))
    payload["variants"][0]["ab_label"] = "a"
    with pytest.raises(AgentOutputError):
        validate_agent_output(PipelineStep.MARKETIZER, payload)


def test_a_paired_ab_experiment_is_accepted() -> None:
    payload = json.loads(json.dumps(VALID[PipelineStep.MARKETIZER]))
    payload["variants"][0]["ab_label"] = "a"
    payload["variants"].append(dict(payload["variants"][0], ab_label="b", hook="قلاب دوم"))
    result = validate_agent_output(PipelineStep.MARKETIZER, payload)
    assert {variant.ab_label for variant in result.variants} == {"a", "b"}


def test_a_persian_slug_is_allowed_but_whitespace_is_not() -> None:
    payload = json.loads(json.dumps(VALID[PipelineStep.SEO_OPTIMIZER]))
    assert validate_agent_output(PipelineStep.SEO_OPTIMIZER, payload).slug.startswith("راهنما")

    payload["slug"] = "راهنمای خرید"
    with pytest.raises(AgentOutputError):
        validate_agent_output(PipelineStep.SEO_OPTIMIZER, payload)


def test_a_bad_planned_date_is_rejected() -> None:
    payload = json.loads(json.dumps(VALID[PipelineStep.TOPIC_PLANNER]))
    payload["topics"][0]["planned_for"] = "۱۴۰۵/۰۷/۰۹"
    with pytest.raises(AgentOutputError):
        validate_agent_output(PipelineStep.TOPIC_PLANNER, payload)


def test_visual_briefs_keep_script_out_of_the_image_by_default() -> None:
    """FLUX mangles Persian and Arabic type, so text is overlaid afterwards."""
    from app.agents.contracts import VisualBrief

    assert VisualBrief(scene="a drill on a workbench").render_text_separately is True
