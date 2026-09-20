"""What each agent must return.

These models are the contract between the pipeline stages.  They are written
with guided decoding in mind, because the same schema that validates an output
is what constrains the model producing it:

* ``extra="forbid"`` everywhere, so the JSON Schema carries
  ``additionalProperties: false`` and a drifting agent fails loudly instead of
  smuggling fields past the next stage;
* no ``format`` keywords the grammar backends handle inconsistently — dates
  and URLs are plain strings with an explicit pattern or validator;
* short, flat structures. A deeply nested schema costs tokens on every call
  and is where small models start failing.

Language note (decision D3): every text field carries whatever locale the
package is in. Nothing here is translated between languages — a Persian
package is researched, written and optimised in Persian throughout.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: Locales the platform supports (decision D3).
Locale = Literal["fa", "en", "ar"]

#: ISO date as a plain string; models produce these far more reliably than
#: they produce a datetime the JSON parser will accept.
IsoDate = Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]

SearchIntent = Literal["informational", "commercial", "transactional", "navigational"]
FunnelStage = Literal["awareness", "consideration", "decision", "retention"]

_URL_RE = re.compile(r"^https?://\S+$", re.IGNORECASE)


class AgentModel(BaseModel):
    """Base for every agent output: strict, and stripped of stray whitespace."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ---------------------------------------------------------------------------
# shared pieces
# ---------------------------------------------------------------------------
class Claim(AgentModel):
    """A factual statement together with where it came from.

    Handoff section 13 lists unsourced claims as a main risk: "every statistic
    needs a source and QA checks it".  This type is how that rule is carried
    through the pipeline — the writer may only use claims the researcher
    sourced, and QA rejects any that lost their source on the way.
    """

    id: str = Field(min_length=1, max_length=64, description="Referenced later as claim_id")
    statement: str = Field(min_length=1, max_length=1000)
    source_url: str = Field(min_length=1, max_length=2048)
    source_title: str | None = Field(default=None, max_length=300)
    published_on: IsoDate | None = None
    confidence: float = Field(ge=0.0, le=1.0, default=0.7)

    @field_validator("source_url")
    @classmethod
    def _looks_like_a_url(cls, value: str) -> str:
        if not _URL_RE.match(value):
            raise ValueError("source_url must be an http(s) URL")
        return value


class KeywordCandidate(AgentModel):
    """Researched per language, never translated (decision D3)."""

    term: str = Field(min_length=1, max_length=200)
    locale: Locale
    intent: SearchIntent = "informational"
    # The platform has no paid keyword tool in the MVP (decision D2), so these
    # are the model's judgement on a 0-1 scale, not volumes from an API.
    demand_hint: float = Field(ge=0.0, le=1.0, default=0.5)
    difficulty_hint: float = Field(ge=0.0, le=1.0, default=0.5)
    is_primary: bool = False


class CompetitorFinding(AgentModel):
    name: str = Field(min_length=1, max_length=200)
    url: str | None = Field(default=None, max_length=2048)
    angle: str = Field(default="", max_length=500, description="How they cover the topic")
    gaps: list[str] = Field(default_factory=list, max_length=10)


class OutlineSection(AgentModel):
    heading: str = Field(min_length=1, max_length=300)
    level: int = Field(ge=2, le=4, default=2)
    intent: str = Field(default="", max_length=300)
    talking_points: list[str] = Field(default_factory=list, max_length=12)
    target_keywords: list[str] = Field(default_factory=list, max_length=10)


class Section(AgentModel):
    heading: str = Field(min_length=1, max_length=300)
    level: int = Field(ge=2, le=4, default=2)
    #: Markdown, in the package's locale.
    body: str = Field(min_length=1)


class QaIssue(AgentModel):
    severity: Literal["blocker", "major", "minor"]
    category: Literal[
        "factual",
        "unsourced_claim",
        "brand_voice",
        "readability",
        "structure",
        "seo",
        "geo",
        "language",
        "plagiarism_risk",
    ]
    location: str = Field(default="", max_length=300, description="Heading or excerpt")
    description: str = Field(min_length=1, max_length=1000)
    suggestion: str | None = Field(default=None, max_length=1000)


# ---------------------------------------------------------------------------
# 1. researcher
# ---------------------------------------------------------------------------
class ResearchOutput(AgentModel):
    """پژوهشگر — gathers keywords, sourced facts and competitor context."""

    summary: str = Field(min_length=1, max_length=3000)
    keywords: list[KeywordCandidate] = Field(min_length=1, max_length=40)
    #: Questions real readers ask; the GEO optimiser turns these into answer
    #: blocks, so they are collected up front.
    audience_questions: list[str] = Field(default_factory=list, max_length=20)
    claims: list[Claim] = Field(default_factory=list, max_length=30)
    competitors: list[CompetitorFinding] = Field(default_factory=list, max_length=10)
    #: Named entities the brand should be associated with in AI answers.
    entities: list[str] = Field(default_factory=list, max_length=30)
    #: Said plainly rather than guessed at: what the agent could not source.
    open_questions: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("keywords")
    @classmethod
    def _at_most_one_primary(cls, value: list[KeywordCandidate]) -> list[KeywordCandidate]:
        if sum(1 for keyword in value if keyword.is_primary) > 1:
            raise ValueError("at most one keyword may be marked primary")
        return value


# ---------------------------------------------------------------------------
# 2. strategist
# ---------------------------------------------------------------------------
class StrategyOutput(AgentModel):
    """استراتژیست — decides the angle and the shape of the article."""

    angle: str = Field(min_length=1, max_length=500)
    target_persona: str = Field(min_length=1, max_length=200)
    funnel_stage: FunnelStage = "awareness"
    primary_keyword: str = Field(min_length=1, max_length=200)
    secondary_keywords: list[str] = Field(default_factory=list, max_length=15)
    outline: list[OutlineSection] = Field(min_length=1, max_length=20)
    #: Why this piece beats what the competitors already published.
    differentiators: list[str] = Field(default_factory=list, max_length=8)
    word_count_target: int = Field(ge=200, le=8000, default=1200)
    pillar: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)


# ---------------------------------------------------------------------------
# 3. writer
# ---------------------------------------------------------------------------
class DraftOutput(AgentModel):
    """نویسنده — writes the article itself."""

    title: str = Field(min_length=1, max_length=300)
    excerpt: str = Field(min_length=1, max_length=500)
    sections: list[Section] = Field(min_length=1, max_length=25)
    #: Ids of the researcher's claims actually used, so QA can check that
    #: every statistic in the body still has a source behind it.
    claim_ids_used: list[str] = Field(default_factory=list, max_length=30)
    call_to_action: str | None = Field(default=None, max_length=500)
    word_count: int = Field(ge=0, default=0)


# ---------------------------------------------------------------------------
# 4. GEO optimiser
# ---------------------------------------------------------------------------
class AnswerBlock(AgentModel):
    """A self-contained answer an AI engine can lift verbatim."""

    question: str = Field(min_length=1, max_length=300)
    answer: str = Field(min_length=1, max_length=1200)
    claim_ids: list[str] = Field(default_factory=list, max_length=5)


class GeoOptimizedOutput(AgentModel):
    """بهینه‌ساز GEO — makes the piece quotable by generative search."""

    answer_blocks: list[AnswerBlock] = Field(default_factory=list, max_length=15)
    faq: list[AnswerBlock] = Field(default_factory=list, max_length=15)
    key_takeaways: list[str] = Field(default_factory=list, max_length=10)
    #: Where the brand should appear so an AI answer attributes it.
    brand_mentions: list[str] = Field(default_factory=list, max_length=10)
    entities: list[str] = Field(default_factory=list, max_length=30)
    #: JSON-LD, emitted into the page by the WordPress connector.
    schema_org: dict = Field(default_factory=dict)
    sections: list[Section] = Field(default_factory=list, max_length=25)
    changes_made: list[str] = Field(default_factory=list, max_length=20)


# ---------------------------------------------------------------------------
# 5. SEO optimiser
# ---------------------------------------------------------------------------
class ImageAlt(AgentModel):
    slot: str = Field(min_length=1, max_length=100)
    alt_text: str = Field(min_length=1, max_length=300)


class LinkSuggestion(AgentModel):
    anchor: str = Field(min_length=1, max_length=200)
    target: str = Field(min_length=1, max_length=2048)
    rationale: str | None = Field(default=None, max_length=300)


class SeoOptimizedOutput(AgentModel):
    """بهینه‌ساز SEO — the fields the WordPress connector fills into Yoast or Rank Math."""

    meta_title: str = Field(min_length=1, max_length=70)
    meta_description: str = Field(min_length=1, max_length=180)
    slug: str = Field(min_length=1, max_length=120)
    h1: str = Field(min_length=1, max_length=300)
    primary_keyword: str = Field(min_length=1, max_length=200)
    secondary_keywords: list[str] = Field(default_factory=list, max_length=15)
    internal_links: list[LinkSuggestion] = Field(default_factory=list, max_length=15)
    external_links: list[LinkSuggestion] = Field(default_factory=list, max_length=10)
    image_alts: list[ImageAlt] = Field(default_factory=list, max_length=15)
    sections: list[Section] = Field(default_factory=list, max_length=25)
    changes_made: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("slug")
    @classmethod
    def _slug_is_url_safe(cls, value: str) -> str:
        # Persian and Arabic slugs are valid but must be percent-encodable, so
        # the only hard rule is: no whitespace, no slashes.
        if re.search(r"[\s/\\?#]", value):
            raise ValueError("slug must not contain whitespace or URL separators")
        return value


# ---------------------------------------------------------------------------
# 6. QA
# ---------------------------------------------------------------------------
class QaReport(AgentModel):
    """QA — the gate before a human ever sees the draft.

    ``score`` drives the retry loop in ``app.services.packages``: below the
    threshold the package returns to the writer, at most ``QA_MAX_RETRIES``
    times, and then goes to a person regardless.
    """

    score: float = Field(ge=0.0, le=1.0)
    verdict: Literal["pass", "revise", "escalate"]
    issues: list[QaIssue] = Field(default_factory=list, max_length=40)
    #: Statements that read as fact but carry no claim id — the section 13 risk.
    unsourced_claims: list[str] = Field(default_factory=list, max_length=20)
    brand_voice_match: float = Field(ge=0.0, le=1.0, default=0.5)
    readability: float = Field(ge=0.0, le=1.0, default=0.5)
    language_is_correct: bool = True
    summary: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def _verdict_is_consistent(self) -> QaReport:
        # A "pass" that also carries a blocker would let broken work through
        # the one automated check standing before the human gate.  This has to
        # be a model validator: a field validator on ``verdict`` cannot see
        # ``issues``, which is declared after it.
        if self.verdict == "pass" and any(issue.severity == "blocker" for issue in self.issues):
            raise ValueError("verdict cannot be 'pass' while a blocker issue is listed")
        return self


# ---------------------------------------------------------------------------
# 7. marketizer
# ---------------------------------------------------------------------------
class VisualBrief(AgentModel):
    """What the image and video models are asked for.

    ``overlay_text`` is separate from ``scene`` on purpose: FLUX mangles
    Persian and Arabic script, so type is rendered over the image from an HTML
    template rather than generated into it (handoff section 5).
    """

    scene: str = Field(min_length=1, max_length=1000)
    style_keywords: list[str] = Field(default_factory=list, max_length=15)
    palette: list[str] = Field(default_factory=list, max_length=8)
    aspect_ratio: Literal["1:1", "4:5", "16:9", "9:16"] = "1:1"
    #: Always true for fa/ar; the renderer draws the words afterwards.
    render_text_separately: bool = True
    overlay_text: str | None = Field(default=None, max_length=200)
    negative_prompt: str | None = Field(default=None, max_length=500)


class ScriptBeat(AgentModel):
    """One spoken beat of a video script (handoff section 8)."""

    text: str = Field(min_length=1, max_length=600)
    seconds: float = Field(gt=0, le=120, default=5.0)
    visual_cue: str | None = Field(default=None, max_length=300)


class ChannelVariant(AgentModel):
    channel: Literal["wordpress", "telegram", "instagram", "linkedin", "x", "youtube", "aparat"]
    #: "a" / "b" for hook experiments; null when there is a single version.
    ab_label: Literal["a", "b"] | None = None
    hook: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1)
    hashtags: list[str] = Field(default_factory=list, max_length=30)
    call_to_action: str | None = Field(default=None, max_length=300)
    visual_brief: VisualBrief | None = None


class MarketizedOutput(AgentModel):
    """بازاریابی‌ساز — turns one article into per-channel posts."""

    variants: list[ChannelVariant] = Field(min_length=1, max_length=20)
    #: Used when a variant does not carry its own.
    default_visual_brief: VisualBrief | None = None
    #: Present for video_mode voice and face; empty for none.
    video_script: list[ScriptBeat] = Field(default_factory=list, max_length=40)
    utm_campaign: str | None = Field(default=None, max_length=120)

    @field_validator("variants")
    @classmethod
    def _ab_arms_are_paired(cls, value: list[ChannelVariant]) -> list[ChannelVariant]:
        # A lone "a" arm means an experiment that can never be concluded.
        by_channel: dict[str, set[str | None]] = {}
        for variant in value:
            by_channel.setdefault(variant.channel, set()).add(variant.ab_label)
        for channel, labels in by_channel.items():
            if labels & {"a", "b"} and not {"a", "b"} <= labels:
                raise ValueError(f"channel {channel!r} has an A/B arm without its pair")
        return value


# ---------------------------------------------------------------------------
# 8. topic planner
# ---------------------------------------------------------------------------
class PlannedTopic(AgentModel):
    title: str = Field(min_length=1, max_length=500)
    locale: Locale
    pillar: str | None = Field(default=None, max_length=200)
    keywords: list[str] = Field(default_factory=list, max_length=15)
    intent: SearchIntent = "informational"
    funnel_stage: FunnelStage = "awareness"
    priority: int = Field(ge=1, le=1000, default=100)
    planned_for: IsoDate | None = None
    rationale: str = Field(default="", max_length=1000)


class TopicPlanOutput(AgentModel):
    """برنامه‌ریز تقویم موضوعی — fills the editorial calendar."""

    topics: list[PlannedTopic] = Field(min_length=1, max_length=60)
    coverage_notes: str = Field(default="", max_length=2000)
