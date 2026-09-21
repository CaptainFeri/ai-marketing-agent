# Agent output contracts

Handoff section 11, phase 0: *"JSON Schema for the six text agents and the
marketing agent"*. Eight contracts, one per pipeline step.

| Step | Model | What it returns |
|---|---|---|
| `researcher` | `ResearchOutput` | keywords per language, sourced claims, competitor gaps, audience questions |
| `strategist` | `StrategyOutput` | angle, persona, funnel stage, outline, differentiators |
| `writer` | `DraftOutput` | title, excerpt, sections, which claims it used |
| `geo_optimizer` | `GeoOptimizedOutput` | answer blocks, FAQ, takeaways, JSON-LD |
| `seo_optimizer` | `SeoOptimizedOutput` | meta title/description, slug, links, image alts |
| `qa` | `QaReport` | score, verdict, issues, unsourced claims |
| `marketizer` | `MarketizedOutput` | per-channel variants, visual briefs, video script |
| `topic_planner` | `TopicPlanOutput` | calendar entries with rationale |

Source of truth is `app/agents/contracts.py`. `schemas/*.schema.json` is
generated from it by `make schemas` and committed so the prompts, the panel
and any external tooling can read a contract without importing the
application. CI fails if the two drift apart.

## Both a constraint and a check

The same schema does two jobs:

```python
json_schema_for(PipelineStep.WRITER)   # → vLLM guided decoding constraint
validate_agent_output(step, raw)       # → parsed model, or AgentOutputError
```

Guided decoding makes a malformed output unlikely, not impossible — a
truncated generation still produces invalid JSON — so both run. When
validation fails, the error carries the offending field paths, which is what
lets a retry feed the errors back into the prompt instead of re-running the
same prompt unchanged.

## Why the schemas look the way they do

**Closed objects.** Every model sets `extra="forbid"`, so the schema carries
`additionalProperties: false`. An agent that starts inventing fields fails
loudly instead of quietly passing them to the next stage.

**No `format` keywords.** Dates are strings with an explicit
`^\d{4}-\d{2}-\d{2}$` pattern and URLs are validated in Python. Grammar
backends support `format` inconsistently, and a constraint that silently stops
constraining is worse than one that was never there. A test enforces this.

**Flat and short.** Deep nesting costs tokens on every call and is where
smaller models start failing. If phase 0 settles on Qwen3-14B rather than the
30B, that margin matters.

## Rules the contracts enforce

These are the places where a schema encodes a decision from the handoff rather
than just a shape:

- **Claims carry their source** (section 13, "every statistic needs a
  source"). `Claim.source_url` must be a real `http(s)` URL — `"internal
  knowledge"` is rejected. The writer records `claim_ids_used`, so QA can check
  that nothing lost its source along the way.
- **QA cannot pass a draft it flagged as blocked.** A `verdict: "pass"`
  alongside a `severity: "blocker"` issue is refused. This is the one automated
  check standing in front of the human gate.
- **A/B arms come in pairs.** A lone `"a"` variant is an experiment that can
  never be concluded, so it is rejected.
- **Text is not generated into images.** `VisualBrief.render_text_separately`
  defaults to true and `overlay_text` is a separate field: FLUX mangles Persian
  and Arabic script, so type is rendered over the image from an HTML template
  (section 5).
- **At most one primary keyword** per research output.
- **Slugs may be Persian** but must not contain whitespace or URL separators.
- **A carousel needs at least 2 slides, or none at all.** `ChannelVariant`'s
  `carousel_slides` (handoff section 11: Instagram carousel captions) is
  empty for a single-image post; a lone slide would not be a carousel, so
  it is rejected the same way an unpaired A/B arm is.

## Instagram carousel and reel fields: captured, not yet posted live

`ChannelVariant.carousel_slides` (each its own `VisualBrief` + overlay
text, own field name matches `VisualBrief.overlay_text`'s reasoning — FLUX
cannot render fa/ar script) and `.reel_script` (beats, same shape as the
package-wide `video_script`) are real, validated, and persisted into
`Variant.body` (`app.services.packages._replace_variants`). What is **not**
built yet: the image queue only ever generates one generic gallery per
package (`IMAGE_OPTIONS_PER_PACKAGE` in `app/worker/tasks/pipeline.py`),
with no per-slide generation or gate-2 selection — so
`app.connectors.instagram`'s live carousel/reel posting flow has nothing to
attach a specific image to a specific slide yet. That is real, separate,
follow-up work (image generation and gate 2 both need to become
carousel-aware), not something this change silently half-does.

## Changing a contract

1. Edit `app/agents/contracts.py`.
2. `make schemas`.
3. Add the case to `VALID` in `tests/test_agent_contracts.py` if you added an
   agent — a test asserts every step has both a contract and a sample payload.
4. Commit the regenerated `schemas/` alongside the code.

Adding a required field is a breaking change for any prompt already in use;
prefer an optional field with a default until the prompt is updated too.
