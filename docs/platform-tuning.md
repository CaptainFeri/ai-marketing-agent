# Configuring the platform from the hardware

The platform probes the machine it is on and derives its own settings, rather
than asking an operator to translate a spec sheet into eleven environment
variables.

```bash
make analyze                      # or: python scripts/analyze_platform.py
python scripts/analyze_platform.py --benchmark --write .env.tuned
```

It reports the hardware, the model choices that follow, the switch strategy,
every finding with a remedy, and an `.env` fragment. It exits non-zero on a
blocker, so it can gate a deploy. Operators can get the same thing over the
API at `GET /api/v1/platform/analysis`.

**Nothing is applied automatically.** The fragment is for a person to read
first: a wrong model choice is expensive and would otherwise be silent.

## Three kinds of number, always labelled

| | Meaning |
|---|---|
| `measured` | Observed by the running system — the switch time the GPU worker recorded. Always preferred. |
| `estimated` | Derived from the probe and the constants in `app/services/tuning.py`. Right to within a factor, wrong in the third digit. |
| `assumed` | A planning figure from the handoff, such as 20 hours of daily GPU uptime. |

## What it decides

**Which language model.** By VRAM, in the handoff's order of preference:
Qwen3-30B-A3B 4-bit needs ~22 GB, Qwen3-14B ~13 GB, Gemma 3 12B ~11 GB. A
fallback produces a warning naming phase 0's acceptance criterion, because a
smaller model may no longer clear "7 of 10 articles publishable per language".
A non-Apache licence produces a second warning against decision D6.

**How the GPU switches windows.** This is the decision that hangs on RAM:

| Strategy | When | Cost |
|---|---|---|
| `none` | Two or more GPUs — one holds the LLM permanently | 0 |
| `vllm_sleep_ram` | Weights fit in host RAM beside everything else | seconds |
| `vllm_sleep_disk` | They do not; weights are re-read on each switch | tens of seconds |
| `vllm_restart` | Documented fallback if vLLM sleep proves unstable in phase 0 | a minute or more |

The RAM budget is `total − 6 GB services − 8 GB ComfyUI ≥ weights × 1.15`. The
ComfyUI term is what usually gets forgotten: it keeps image and video weights
in host RAM between runs so it does not re-read them every time.

**How long to batch.** Batching exists to amortise the switch, so the batch
length is derived from the switch cost: long enough that switching takes at
most 4% of the day. A switch that turns out to cost two minutes rather than
five seconds means batches roughly twenty times longer.

**Everything else.** Daily capacity discounted for switch overhead, CPU worker
concurrency (cores − 2, leaving room for the GPU worker and the database),
connection pool sizes, and whether AI video is possible at all — Wan 2.2
TI2V-5B needs 20 GB, so a smaller card gets `GPU_NIGHTLY_VIDEO_ENABLED=false`
and the scheduler stops treating those jobs as eligible.

## It corrects itself

The estimate is only the starting point. Every time the GPU worker switches
windows it records how long it took; after three switches the running average
replaces the estimate, and the scheduler re-derives its batch length from the
real number (`app/worker/dispatcher.py`: `record_switch`,
`effective_scheduler_config`). Re-running the analyzer then reports
`measured` rather than `estimated`.

This is what closes the loop the handoff opens in section 7: the platform
measures itself rather than being told.

## The 32 GB case

With a 24 GB card and 32 GB of RAM the analyzer picks Qwen3-30B-A3B but has to
fall back to `vllm_sleep_disk`: `32 − 6 − 8 = 18 GB` available, against
`18 × 1.15 = 21 GB` needed. The same shortfall denies the page cache room to
hold the weight files, so the re-read really does reach the device each time.

It reports three ways out, in the order they cost:

1. **64 GB of RAM** — weights stay parked in memory, switches drop to seconds.
   This is what the handoff asks for.
2. **Accept the slower switch** — workable on NVMe. Batches grow, the card
   switches less often, and interactive latency for a single package rises.
3. **Drop to Qwen3-14B** — 9 GB fits in RAM comfortably, at the cost of output
   quality, which is the thing phase 0 is measuring.

On a spinning disk the same configuration is a **blocker**, not a warning:
re-reading 18 GB from a platter on every switch is not a slow system, it is a
stopped one.

## Where the judgement calls live

All of them are named constants at the top of `app/services/tuning.py` —
`SERVICES_RESERVE_GB`, `COMFY_HOST_RESIDENT_GB`, `PCIE_EFFECTIVE_GBPS`,
`LOAD_PATH_FACTOR`, `TARGET_SWITCH_OVERHEAD` and the rest — each with the
reasoning next to it. Phase 0 measurements should replace them, not
accumulate beside them.
