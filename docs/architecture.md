# Architecture — how the handoff decisions landed in code

Cross-reference for reviewers: each decision from
[`HANDOFF.md`](HANDOFF.md) section 2, and where it lives.

| Decision | Implementation |
|---|---|
| **D1** — every model self-hosted, no data leaves the server | No outbound model client exists anywhere in `app/`. The only network calls the platform will ever make are the channel connectors (phase 1+) and the readiness probe. |
| **D2** — no subscription or API cost in the MVP | Nothing in `pyproject.toml` requires a paid service. |
| **D3** — Persian, English, Arabic; keywords researched per language | `SUPPORTED_LOCALES`, `BrandBriefData.seed_keywords` is a per-language structure rather than one list, `GpuCostEstimate.locale` keeps timings apart because Persian generation is slower. |
| **D4** — `face` / `voice` / `none` | `VideoMode`; the default comes from the brand brief and `PackageCreate.video_mode` lets an editor override it per package. `_voice_chain()` in `app/worker/tasks/pipeline.py` turns the mode into GPU steps. |
| **D5** — Celery + Redis | `app/worker/celery_app.py`. The queue names are in `app/worker/queues.py` so a Temporal migration in phase 4 has one list to port. |
| **D6** — commercially licensed models only | Recorded per model in the handoff table; `app/services/quota.py` names the chosen model beside each cost estimate. The forbidden ones (FLUX.1-dev, XTTS-v2, Wav2Lip, MMS-TTS) appear nowhere. |
| **D7** — real faces and voices need written consent | `SpeakerProfile.consent_document_key` / `consent_signed_at` / `consent_expires_on`, and `is_usable()` is the single check media tasks call. `MediaAsset.is_ai_labelled` carries the label onto the output. |
| **D8** — multi-tenant from day one | `TenantScopedMixin` on every business table; PostgreSQL row level security in migration 0002; `app/db/tenancy.py` binds the tenant per transaction; channel tokens encrypted via `app/core/crypto.py`; MinIO keys prefixed with `Tenant.storage_prefix`. |
| **D9** — one RTX 3090 Ti, models take turns | `app/worker/gpu_scheduler.py` (policy) and `app/worker/dispatcher.py` (leases and accounting); a single `gpu` queue worker at concurrency 1. |
| **D10** — volume follows capacity | `app/services/quota.py`: measured seconds → EWMA per job kind → daily split by plan weight → affordances shown in the panel. |

## Layout

```
app/
  core/        settings, security, credential encryption, logging, errors
  db/
    models/    the tables from handoff section 9, plus the GPU queue
    tenancy.py the only sanctioned way to open a session
    session.py two engines: tenant-scoped, and BYPASSRLS for cross-tenant work
  schemas/     request/response models; BrandBriefData validates the questionnaire
  api/v1/      routers; deps.py binds the caller's tenant to the request session
  services/    auth, quota, package lifecycle — the logic worth testing directly
  worker/
    gpu_scheduler.py   pure policy: which batch runs next
    dispatcher.py      leases, retries, quota bookkeeping
    gpu_runtime.py     what actually loads models (phase 0 fills this in)
    tasks/             Celery entry points per queue
migrations/    0001 schema · 0002 row level security · 0003 optional pgvector
```

## Two database roles, on purpose

`DATABASE_URL` is the application's connection and its role must not hold
`BYPASSRLS`. `SYSTEM_DATABASE_URL` is a `BYPASSRLS` role used by exactly three
things: Alembic, the GPU scheduler (choosing between tenants is inherently
cross-tenant), and login (a user types an email before any tenant is known).

Splitting them is what makes row level security a real boundary rather than a
comment. If the application role could bypass it, a forgotten `WHERE` clause
would be a data leak instead of an empty result.

## Where the human gates sit

```
Planned → Drafting → TextReview ──gate 1──▶ MediaGenerating → Selection ──gate 2──▶ Scheduled → Published → Measuring → Refresh
                           │                                        │
                           ├─ changes_requested → Drafting          ├─ changes_requested → MediaGenerating
                           └─ rejected → Rejected                   └─ rejected → Rejected
```

`ALLOWED_TRANSITIONS` in `app/services/packages.py` is the whole state machine,
and `transition()` refuses anything not listed. Drafting cannot reach
MediaGenerating directly — that would skip gate 1, which is the main defence
against the bulk-low-value-content risk in handoff section 13.

The QA loop returns a weak draft to the writer at most `QA_MAX_RETRIES` times;
after that the package goes to a human with the QA notes rather than spending
more GPU time on the same draft.

## What the agents will plug into

`StepRun` already stores each agent's input, output, model, token counts and
`gpu_seconds`, which is what makes "re-run from any step" (phase 1, week 3)
possible without re-running the whole chain. The agent implementations write
into that shape; nothing above them has to change.
