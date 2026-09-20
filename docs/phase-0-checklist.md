# Phase 0 — technical validation

Two weeks. The goal is to prove the model quality is acceptable in all three
languages and that a 3090 Ti carries the load, *before* more product is built
on top. Handoff section 11.

The platform scaffolding is already in place, so phase 0 can use it: run with
`GPU_RUNTIME=simulated` to exercise the queue and the accounting, and replace
the runtime with real adapters as each model is validated.

## Infrastructure

- [ ] NVIDIA driver, CUDA and Docker on the server
- [ ] `docker compose up -d postgres redis minio` and `alembic upgrade head`
- [ ] Confirm `pgvector` is present (the compose image has it)
- [x] Record the server's actual RAM, CPU and disk — run `make analyze` on the
      box. It probes them and derives the configuration; section 12's open
      question is answered by its output.

## Language model

- [ ] vLLM with Qwen3-30B-A3B (4-bit) and with Qwen3-14B
- [ ] Five sample prompts per language (fa/en/ar), compared side by side
- [ ] **Measure the sleep/wake cycle time.** `make analyze` estimates it and
      the running system corrects it after three real switches, but the first
      deliberate measurement belongs here — and decides whether sleep level 1,
      sleep level 2 or a full restart is the strategy
- [ ] Measure the LLM ↔ FLUX switch specifically — it is the switch the
      scheduler makes most often

## Text pipeline

- [x] JSON Schema for each of the six text agents and the marketizer — done,
      see `schemas/` and `docs/agent-contracts.md`. Hand `json_schema_for(step)`
      to vLLM as a guided decoding constraint.
- [x] A script from brand brief to `article.json` — the pipeline does this.
      Point `LLM_CLIENT=vllm` at the server and drive a package through
      `advance_text`; the assembled article lands in `ContentPackage.article`.
- [ ] Ten trial articles per language
- [ ] **Acceptance: at least 7 of 10 per language rated "publishable with minor
      edits" by an editor**

## Media

- [ ] FLUX.1-schnell on ComfyUI
- [ ] An HTML overlay template for Persian and Arabic type — image models mangle
      the script, so type is rendered over the image, never generated into it
- [ ] Piper (Persian, CPU) and Chatterbox (English/Arabic) voice samples
- [ ] One `voice` video assembled with FFmpeg
- [ ] Wan 2.2 5B and LatentSync on the 3090 Ti: time, VRAM and quality recorded

## Feeding the numbers back

- [ ] Replace `DEFAULT_ESTIMATES` in `app/services/quota.py` with the measured
      per-job times
- [ ] Check `PACKAGE_RECIPES` against what a real package actually queues
- [ ] Set `GPU_DAILY_CAPACITY_SECONDS` from observed sustained throughput, not
      from the 20h planning assumption
- [ ] **Settle the section 7 discrepancy**: the per-job timings imply ~110
      `voice` packages a day, the summary says 15–30. Measure which is right
      and correct whichever number is wrong — the panel shows this to customers
- [ ] Fill in one real brand brief through `POST /api/v1/workspaces/{id}/briefs`

## Acceptance

- [ ] 7 of 10 articles publishable with minor edits, per language
- [ ] A full chain (text + images + `voice` video) for one package with no OOM
- [ ] Final model choices recorded in the internal mirror with version and
      licence (decision D6)

## Deliverables

A model comparison report, a real capacity table, and the pipeline scripts —
which become the `GpuRuntime` implementation described in
[`gpu-scheduling.md`](gpu-scheduling.md).
