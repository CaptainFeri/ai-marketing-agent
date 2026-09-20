# The image queue

Handoff sections 5 and 6: image generation runs on the same GPU as the text
agents, so it goes through the same queue and the same scheduler. What is
new here is *what feeds it* and *what happens to the result*.

```
gate 1 approved
  └─ start_media                reads the marketizer's own visual briefs
       └─ GpuJob(image_flux) ×4 per distinct brief, queued with a real prompt
            └─ gpu.dispatch      leases the batch, loads the media window
                 └─ execute_image_job
                      ├─ image_backend.generate()      base picture, no text
                      ├─ image_overlay.render_overlay() overlay text, if any
                      ├─ image_compose.compose()        alpha-blend the two
                      └─ storage.put()                  under the tenant's prefix
       └─ once nothing is left pending → media_finished → gate 2
```

## The prompt comes from gate 1, not from nowhere

Earlier, `start_media` queued four generic, unprompted image jobs per
package. That was wrong against the handoff: section 3 is explicit that what
gate 1 approves is *"channel version + visual_brief + A/B variants"* — the
marketizer's visual brief is supposed to exist before media generation
starts, because media generation is supposed to be built from it.

Fixing that meant moving the marketizer **into** `TEXT_PIPELINE`, after QA,
so it runs before `TEXT_REVIEW` rather than after gate 1 is approved (see
`app/services/packages.py`). `start_media` then reads the resulting
`Variant.visual_brief` rows via `distinct_visual_briefs()`: one gallery of
`IMAGE_OPTIONS_PER_PACKAGE` images per distinct brief among the channels,
deduplicated by content so two channels sharing one scene do not double the
work. A marketizer run that named no visual brief at all — legal, since a
text-only channel needs none — still gets one generic brief built from the
article, so gate 2 is never an empty gallery.

## Text is never in the FLUX prompt

`_flux_prompt()` builds the prompt from `scene` and `style_keywords` only.
`overlay_text` never reaches it — diffusion models mangle Persian and Arabic
script (handoff section 5), so any caption is drawn separately by
`app.services.image_overlay` and composited on top afterward.

## What's real today, and what phase 0 still has to supply

| Piece | Status |
|---|---|
| Queueing, leasing, batching by model, quota accounting | real (same machinery as text) |
| Reading the marketizer's visual brief, building the prompt | real |
| **Overlay rendering** (`image_overlay.py`) | **real** — a headless Chromium renders actual HTML/CSS, with correct RTL shaping for fa/ar |
| **Compositing** (`image_compose.py`) | **real** — Pillow alpha-blends the overlay onto the base |
| **Storage** (`storage.py`) | **real** — an S3-compatible client against MinIO; an in-memory backend for tests and for running with no MinIO yet |
| Base image generation (FLUX.1-schnell itself) | **simulated** — a deterministic gradient placeholder, until phase 0 delivers the ComfyUI workflow graph |

Unlike the language model, the overlay renderer needed no GPU weights to
build for real, so it was — this is the one piece of the media pipeline that
already works exactly as it will in production. It is exercised with actual
Persian and Arabic text in `tests/test_image_overlay.py`, not mocked.

## Why the simulated backend needed a real bug fix

`SimulatedImageBackend.generate()` takes a `seed`, precisely so four options
generated from one prompt look like four different things — the same as a
real diffusion model sampling the same prompt four times. The first version
of this code accepted the argument and then silently ignored it: every
"option" in a gallery rendered as the exact same placeholder. A test
(`test_four_image_options_are_generated_and_stored`) caught it by asserting
the four stored images are byte-distinct, and a second pass caught that the
first fix — a single stripe offset derived from a hash of the prompt — could
still coincide across four seeds and produce two identical images by chance;
the seed's entropy now feeds both the stripe position and a brightness shift,
independently.

## Storage keys are computed before generation, not after

`start_media` computes each `MediaAsset.storage_key` from the asset's own
just-flushed id — `tenant_key(prefix, "packages", pid, "images",
f"{asset.id}.png")` — before the GPU job that fills it in ever runs. The
executor has nowhere else to write. This is also what keeps
`tenant_key()`'s traversal checks (`../`, a bare `..` segment, a null byte)
load-bearing rather than decorative: every image this platform will ever
write goes through that one function.

## Gate 2 needs a selection, not just approval

`record_approval` now refuses to approve the `media` gate unless at least one
`MediaAsset.is_selected` is true for the package — section 3 calls gate 2
*"select option + schedule"*, and approving a gallery nobody picked from is
skipping the gate, not passing it. Rejecting still needs no selection: a
gallery nobody liked should not first demand a pick from it.

```
PATCH /packages/{id}/media/{media_asset_id}   {"is_selected": true}
POST  /packages/{id}/gates/media              {"decision": "approved"}
```

## Auto-advancing to gate 2

Several jobs for one package (four image options, plus TTS and Whisper for a
`voice` package) can finish in different batches, so no single job's
completion can safely decide "media generation is done." `gpu.dispatch`
tracks which packages a batch touched and, once
`dispatcher.package_has_pending_jobs()` says nothing is left outstanding,
queues `media_finished` — which is itself idempotent: a package no longer in
`media_generating` (a duplicate call racing a second batch) is a no-op rather
than an error.

## Running without MinIO or ComfyUI

`STORAGE_BACKEND=memory` (the default) keeps every object in a process-local
dict — the same idea as `GPU_RUNTIME=simulated` and `LLM_CLIENT=simulated`.
`IMAGE_BACKEND=simulated` (also the default) is the placeholder generator.
Both flip to their real counterparts independently, whenever MinIO and
ComfyUI respectively are ready — nothing else in this chain has to change.
