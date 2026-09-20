# AI Marketing Agent

پلتفرم چندمشتری تولید محتوای SEO/GEO و چندرسانه‌ای — همه مدل‌ها خودمیزبان، روی یک RTX 3090 Ti.

Multi-tenant SEO/GEO and multimedia content platform. Every model is self-hosted
(decision D1) and they all share a single 24 GB GPU (D9), which is what most of
the design here exists to manage.

The full design lives in [`docs/HANDOFF.md`](docs/HANDOFF.md); section numbers
referenced throughout the code point at it.

---

## وضعیت فعلی / Current status

This repository implements **phase 1, weeks 1–2** of the plan — the foundation
everything else is built on:

| Built | Where |
|---|---|
| FastAPI skeleton, error handling, JSON logging, health/readiness | `app/main.py`, `app/core/` |
| Full data model from handoff section 9, with `tenant_id` everywhere | `app/db/models/` |
| Row level security on every tenant table, plus an automated isolation test | `migrations/versions/0002_row_level_security.py`, `tests/test_tenant_isolation.py` |
| Alembic migrations | `migrations/` |
| Auth, roles (owner/admin/editor/viewer), workspaces, versioned brand briefs | `app/services/auth.py`, `app/api/v1/` |
| Celery with a single-threaded `gpu` queue and separate CPU queues | `app/worker/celery_app.py`, `app/worker/queues.py` |
| GPU scheduler: two windows, batching by model, fair rotation, night work | `app/worker/gpu_scheduler.py`, `app/worker/dispatcher.py` |
| Quota mechanism: measured `gpu_seconds` → moving average → daily per-tenant share | `app/services/quota.py` |
| Content package state machine and the two human gates | `app/services/packages.py` |
| JSON Schema contracts for all eight agents, used as both a decoding constraint and a check | `app/agents/`, `schemas/` |
| Agent prompts, context assembly, the validate-and-retry runner, and re-run from any step | `app/agents/prompts.py`, `context.py`, `runner.py` |
| Brand questionnaire wizard: ~25 questions, an SSRF-safe website reader, an async "guess it" assistant | `app/services/questionnaire.py`, `website.py`, `brief_draft.py` |
| Image queue: marketizer-driven FLUX prompts, a **real** HTML/Chromium overlay renderer for Persian/Arabic text, a **real** Pillow compositor, **real** MinIO-compatible storage | `app/services/image_*.py`, `media_jobs.py`, `storage.py` |
| **Real** WordPress and Telegram connectors — REST API / Bot API, encrypted credentials, a three-attempts-then-alert retry state machine | `app/connectors/`, `app/services/publishing.py`, `channel_credentials.py` |
| Hardware probe and automatic configuration, self-correcting from measured switch times | `app/core/platform.py`, `app/services/tuning.py` |

**Not built yet**, and deliberately so:

- **The model adapters.** vLLM/Qwen3, ComfyUI/FLUX, Wan, Whisper and the
  lip-sync pipelines are phase 0 work — their timings have to be measured on
  the real card before they are worth writing down. `GPU_RUNTIME=simulated`
  runs the whole platform end to end without weights present, with plausible
  timings, so everything above is exercisable today. Setting any other value
  currently fails loudly rather than producing placeholder content.
- **Any article or image produced by a real model.** The text chain and the
  image queue both run end to end against simulated backends; whether Qwen3
  and FLUX.1-schnell clear phase 0's bar is the open question that measuring
  them on the real card answers.
- **The wizard's frontend, and every panel screen generally.** The APIs and
  their logic are done (see [`docs/questionnaire.md`](docs/questionnaire.md),
  [`docs/publishing.md`](docs/publishing.md)); the Next.js panel that
  renders them is still to come.
- **hreflang via WPML/Polylang**, and Instagram/LinkedIn/X/YouTube/Aparat
  connectors — phase 2/3 per the handoff's own channel table.
- **Channel connectors** — WordPress and Telegram are phase 1 weeks 7–8.
- **The Next.js panel.**

---

## شروع سریع / Quick start

### With Docker (as deployed)

```bash
cp .env.example .env          # then fill in SECRET_KEY and CREDENTIAL_ENCRYPTION_KEY
docker compose up -d postgres redis minio
docker compose run --rm api alembic upgrade head
docker compose up -d
curl localhost:8000/healthz
```

### Locally

```bash
make install                  # uv venv + dependencies
make db-up                    # throwaway PostgreSQL with the two roles
make migrate
make test
make run                      # http://localhost:8000/docs
```

### Before you hand-tune anything

```bash
make analyze
```

It probes the GPU, RAM, disk and CPU, picks the models that fit, works out how
the GPU should switch between its two windows, and prints the settings that
follow — with the reasoning for each. See
[`docs/platform-tuning.md`](docs/platform-tuning.md).

The test suite needs a real PostgreSQL: row level security is the thing under
test, and no in-memory substitute has it. `make db-up` provides one; the tests
skip with an explanation if none is reachable.

### Creating the first tenant

Tenant creation is an operator action until self-service onboarding arrives in
phase 2:

```bash
.venv/bin/python -c "
from app.schemas.tenant import TenantCreate
from app.services.auth import create_tenant_with_owner
print(create_tenant_with_owner(TenantCreate(
    slug='pilot', name='Pilot Customer',
    owner_email='owner@example.com', owner_password='change-this-password',
)).id)"
```

---

## معماری / How the pieces fit

```
  panel / API  ──▶  FastAPI  ──▶  PostgreSQL (RLS on tenant_id)
                       │
                       ├──▶  pipeline queue   (orchestration, CPU)
                       ├──▶  publish queue    (channel connectors, CPU)
                       ├──▶  media_cpu queue  (Piper TTS, FFmpeg)
                       │
                       └──▶  gpu queue  ──▶  GPU scheduler  ──▶  ONE RTX 3090 Ti
                                              two windows:
                                                text  = vLLM + Qwen3
                                                media = FLUX / Wan / Whisper / lip-sync
```

Three ideas carry most of the weight:

**One owner for the card.** The `gpu` queue has exactly one worker at
concurrency 1. That is not a tuning knob — two processes on one 24 GB card
means an out-of-memory error, not throughput. Everything else runs on CPU
queues so a thirteen-minute Wan clip never blocks a Telegram post.

**Switching models costs real time, so the scheduler avoids it.** It stays in
the window it is already in while that window has work, batches jobs that share
a model into one load, and only switches when the idle side has been starving.
`select_batch` is a pure function over dataclasses, so the policy is tested
without a GPU or a database (`tests/test_gpu_scheduler.py`).

**Volume follows capacity.** Every job records the seconds it actually used;
those feed a moving average per job kind; the day's capacity is split between
tenants by plan weight; and the panel turns the remainder into "you can still
make N packages of this shape today". Adding a second GPU in phase 4 raises
every allocation by moving one number.

Further reading:

- [`docs/architecture.md`](docs/architecture.md) — how each handoff decision landed in code
- [`docs/agent-contracts.md`](docs/agent-contracts.md) — the eight agent output schemas
- [`docs/agents.md`](docs/agents.md) — prompts, context, retries and re-runs
- [`docs/questionnaire.md`](docs/questionnaire.md) — the wizard, the "guess it" assistant, and the SSRF guard
- [`docs/image-queue.md`](docs/image-queue.md) — visual briefs, the real overlay renderer, storage, and gate 2's selection requirement
- [`docs/publishing.md`](docs/publishing.md) — the WordPress and Telegram connectors, credentials, and the retry/alert state machine
- [`docs/platform-tuning.md`](docs/platform-tuning.md) — hardware probing and automatic configuration
- [`docs/gpu-scheduling.md`](docs/gpu-scheduling.md) — the scheduler and the quota mechanism in detail
- [`docs/deployment.md`](docs/deployment.md) — database roles, secrets, backups
- [`docs/phase-0-checklist.md`](docs/phase-0-checklist.md) — what has to be measured before the model adapters are written

---

## جداسازی مشتری‌ها / Tenant isolation

Decision D8 is enforced by PostgreSQL, not by remembering to add a `WHERE`
clause. Every tenant table has a policy comparing `tenant_id` against a
`app.tenant_id` setting that is bound per transaction, and the application's
database role does **not** hold `BYPASSRLS`. Forgetting to bind a tenant
returns zero rows; it never returns someone else's.

`tests/test_tenant_isolation.py` covers this against a real database — reads,
writes, updates, an unbound session, a stale pooled connection, and a check
that the policy list has not drifted from the model list.

---

## توسعه / Development

```bash
make lint          # ruff
make typecheck     # mypy
make test          # pytest
make test-cov      # with coverage
make revision m="add publication retry columns"
```

526 tests today, 92% line coverage. Anything touching tenant scoping, the scheduler or the quota
ledger should arrive with tests — those three are where a quiet bug is most
expensive.
