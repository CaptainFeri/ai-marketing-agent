"""Contract-valid output without a model.

Lets the whole line run — the gates, the panel, the queue, the quota — before
phase 0 has produced any weights. The payloads are minimal but real: they
validate against the same schemas the live agents are held to, so a simulated
run exercises the identical code path.

They are recognisably placeholder text on purpose. A simulated run that looked
like finished work would be worse than one that obviously is not.
"""

from __future__ import annotations

from typing import Any

from app.db.enums import PipelineStep

_MARK = "[simulated]"


def sample_output(step: PipelineStep) -> dict[str, Any]:
    """A minimal payload that validates against ``step``'s contract."""
    return dict(_SAMPLES[step])


def sample_for_schema_name(schema_name: str) -> dict[str, Any]:
    """Look a sample up by the schema name the LLM client was given."""
    try:
        return sample_output(PipelineStep(schema_name))
    except ValueError:  # pragma: no cover - the caller always passes a step
        raise KeyError(f"no simulated output for schema {schema_name!r}") from None


_SAMPLES: dict[PipelineStep, dict[str, Any]] = {
    PipelineStep.RESEARCHER: {
        "summary": f"{_MARK} research summary",
        "keywords": [
            {"term": f"{_MARK} primary keyword", "locale": "fa", "is_primary": True},
            {"term": f"{_MARK} secondary keyword", "locale": "fa"},
        ],
        "audience_questions": [f"{_MARK} what do buyers ask?"],
        "claims": [
            {
                "id": "sim-1",
                "statement": f"{_MARK} a sourced statement",
                "source_url": "https://example.com/simulated-source",
                "confidence": 0.5,
            }
        ],
        "competitors": [{"name": f"{_MARK} competitor", "gaps": ["pricing"]}],
        "entities": [f"{_MARK} entity"],
        "open_questions": [f"{_MARK} nothing was really researched"],
    },
    PipelineStep.STRATEGIST: {
        "angle": f"{_MARK} angle",
        "target_persona": f"{_MARK} persona",
        "funnel_stage": "consideration",
        "primary_keyword": f"{_MARK} primary keyword",
        "secondary_keywords": [f"{_MARK} secondary keyword"],
        "outline": [
            {
                "heading": f"{_MARK} first section",
                "level": 2,
                "talking_points": [f"{_MARK} point"],
            }
        ],
        "differentiators": [f"{_MARK} differentiator"],
        "word_count_target": 1200,
    },
    PipelineStep.WRITER: {
        "title": f"{_MARK} article title",
        "excerpt": f"{_MARK} excerpt",
        "sections": [
            {"heading": f"{_MARK} first section", "level": 2, "body": f"{_MARK} body text."}
        ],
        "claim_ids_used": ["sim-1"],
        "word_count": 1200,
    },
    PipelineStep.GEO_OPTIMIZER: {
        "answer_blocks": [
            {
                "question": f"{_MARK} what do buyers ask?",
                "answer": f"{_MARK} a standalone answer.",
                "claim_ids": ["sim-1"],
            }
        ],
        "faq": [],
        "key_takeaways": [f"{_MARK} takeaway"],
        "brand_mentions": [],
        "entities": [f"{_MARK} entity"],
        "schema_org": {"@type": "Article"},
        "sections": [
            {"heading": f"{_MARK} first section", "level": 2, "body": f"{_MARK} body text."}
        ],
        "changes_made": [f"{_MARK} added an answer block"],
    },
    PipelineStep.SEO_OPTIMIZER: {
        "meta_title": f"{_MARK} meta title",
        "meta_description": f"{_MARK} meta description",
        "slug": "simulated-slug",
        "h1": f"{_MARK} article title",
        "primary_keyword": f"{_MARK} primary keyword",
        "secondary_keywords": [f"{_MARK} secondary keyword"],
        "internal_links": [],
        "external_links": [],
        "image_alts": [{"slot": "hero", "alt_text": f"{_MARK} alt text"}],
        "sections": [
            {"heading": f"{_MARK} first section", "level": 2, "body": f"{_MARK} body text."}
        ],
        "changes_made": [f"{_MARK} wrote the meta fields"],
    },
    PipelineStep.QA: {
        # Above the pass threshold, so a simulated run reaches gate 1 rather
        # than looping through the writer three times.
        "score": 0.8,
        "verdict": "pass",
        "issues": [
            {
                "severity": "minor",
                "category": "readability",
                "description": f"{_MARK} nothing was really reviewed",
            }
        ],
        "unsourced_claims": [],
        "brand_voice_match": 0.8,
        "readability": 0.8,
        "language_is_correct": True,
        "summary": f"{_MARK} simulated QA report",
    },
    PipelineStep.MARKETIZER: {
        "variants": [
            {
                "channel": "wordpress",
                "hook": f"{_MARK} hook",
                "body": f"{_MARK} post body",
                "hashtags": [],
                "visual_brief": {
                    "scene": f"{_MARK} a scene with no text in it",
                    "style_keywords": ["clean"],
                    "aspect_ratio": "16:9",
                    "render_text_separately": True,
                    "overlay_text": f"{_MARK} overlay",
                },
            },
            {
                "channel": "telegram",
                "hook": f"{_MARK} hook",
                "body": f"{_MARK} post body",
                "hashtags": ["#simulated"],
            },
        ],
        "video_script": [{"text": f"{_MARK} spoken line.", "seconds": 5.0}],
    },
    PipelineStep.TOPIC_PLANNER: {
        "topics": [
            {
                "title": f"{_MARK} planned topic",
                "locale": "fa",
                "keywords": [f"{_MARK} keyword"],
                "priority": 100,
                "rationale": f"{_MARK} no planning actually happened",
            }
        ],
        "coverage_notes": f"{_MARK} simulated plan",
    },
}
