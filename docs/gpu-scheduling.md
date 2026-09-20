# GPU scheduling and capacity

Implements handoff sections 6 and 7. The single RTX 3090 Ti is the bottleneck
of the entire platform, so this is the part most worth understanding.

## The problem

Qwen3-30B at 4-bit needs roughly 18 GB. FLUX.1-schnell needs 12–16 GB. Wan 2.2
TI2V-5B needs 20–24 GB. They cannot coexist on one 24 GB card, so the card
alternates between two **windows**:

- **text** — vLLM holds the LLM; the six text agents and the marketizer run here.
- **media** — FLUX, Wan, Whisper, Chatterbox and the lip-sync models run here.

A switch costs a model reload (`GPU_WINDOW_SWITCH_SECONDS`, to be replaced with
the number phase 0 measures for a vLLM sleep/wake cycle). Paying that cost
between every job would waste most of the day.

## The policy

`select_batch()` in `app/worker/gpu_scheduler.py` is a pure function. Given the
pending jobs, the current window, the tenant weights and what each tenant has
already consumed today, it returns the next batch or `None`.

1. **Eligibility.** Jobs marked `nightly_only` — Wan clips and lip-sync — are
   invisible outside the night window (`GPU_NIGHT_WINDOW_START_HOUR` to
   `..._END_HOUR`, which may wrap midnight).
2. **Window choice.** Stay where the card already is while that window has
   work. Switch only when the idle window's oldest job has waited longer than
   `GPU_WINDOW_STARVATION_SECONDS`, or when the current window is empty.
3. **Fairness.** Each waiting tenant gets a deficit:
   `weight_share − consumed_share`. The most under-served tenant's job leads
   the batch. A tenant with an unknown weight is treated as weight 1, never
   excluded — a missing row must not park someone's work forever.
4. **Batching.** Everything in a batch shares one `GpuJobKind`, so one model
   load serves all of it. Jobs are then taken round-robin across tenants, so a
   customer with fifty queued images cannot fill the batch while another waits
   for one.
5. **Caps.** `GPU_MAX_BATCH_SIZE` jobs or `GPU_MAX_BATCH_SECONDS` of estimated
   work, whichever comes first — except that the lead job always goes in, so a
   thirteen-minute Wan clip stays runnable.

Because it is pure, `tests/test_gpu_scheduler.py` exercises all of this without
a GPU or a database.

## Leases

`claim_next_batch()` marks the chosen jobs `leased` with an owner and an expiry.
If the GPU worker dies, `reclaim_expired_leases()` (every 5 minutes on the
maintenance queue) returns them to `pending`. Without it a crashed worker would
park the only card indefinitely.

The claim is a conditional `UPDATE ... WHERE status = 'pending'`, so a second
scheduler instance racing for the same batch claims nothing and backs off.

## Retries

A failed job is retried up to `max_attempts` with a linear backoff, keeping its
quota reservation — the work still has to happen. A final failure releases the
reservation, so a tenant is not charged for a job that produced nothing.

## The quota mechanism

Handoff section 7, step by step:

1. **Measure.** `GpuJob.gpu_seconds` is written on completion and mirrored onto
   `StepRun`, `MediaAsset` and `ContentPackage`.
2. **Learn.** `record_actual()` folds each measurement into an exponential
   moving average per job kind and locale (α = 0.2), in a single
   `INSERT ... ON CONFLICT DO UPDATE` so concurrent workers cannot lose one.
   The seeds in `DEFAULT_ESTIMATES` are planning numbers from the handoff and
   are replaced within a few runs.
3. **Split.** `allocate_day()` divides `GPU_DAILY_CAPACITY_SECONDS` between
   active tenants by plan weight, nightly and again on demand. Re-running it
   preserves what has already been consumed, so a mid-day plan change takes
   effect immediately without erasing history.
4. **Show.** `quota_status()` turns the remaining seconds into "you can still
   make N of each package shape today" using `PACKAGE_RECIPES`. Work beyond the
   day's share is deferred to tomorrow, not failed — `enqueue_job` raises
   `QuotaExceededError` and the pipeline task records the deferral.
5. **Scale.** A second GPU in phase 4 means raising
   `GPU_DAILY_CAPACITY_SECONDS`; every allocation follows.

### An open discrepancy in the planning numbers

Handoff section 7 gives both per-job timings and a summary of "15 to 30 full
packages per day". The two do not agree: the per-job numbers add up to about
ten minutes for a `voice` package, which over 20 hours of capacity is roughly
110 packages, not 15–30.

`PACKAGE_RECIPES` follows the per-job table, because that is the number the
scheduler can actually act on. The summary presumably folds in several
languages per package, more media per package, QA retries and switching
overhead. Until phase 0 measures the real cost, **the panel's affordance
figures read optimistically** — worth saying out loud to a pilot customer
rather than discovering at the end of a day.

## Running without models

`GPU_RUNTIME=simulated` installs `SimulatedGpuRuntime`, which sleeps for a
fraction of each job's estimated time and reports realistic `gpu_seconds`. The
queue, the leases, the batching and the quota bookkeeping are all the real
code — only the model call is stubbed. This is what lets the panel, the gates
and the capacity mechanism be built and demonstrated before phase 0 finishes.

Any other value installs `UnavailableGpuRuntime`, which fails loudly. That is
deliberate: a server misconfigured to expect real models should stop, not
quietly emit placeholder content.

## What phase 0 must still supply

`GpuRuntime` has two methods. A real implementation needs:

- `ensure_window(window, kind)` — vLLM sleep/wake (or stop/start if sleep turns
  out to be unstable), plus ComfyUI model loading. Return the seconds it cost.
- `run(kind, payload)` — dispatch to the right model and return `RunResult`
  with the measured GPU time.

Everything around those two methods already works and is tested.
