# Observability: Langfuse and per-step GPU time

Handoff section 4: "مشاهده‌پذیری: Langfuse خودمیزبان + لاگ زمان GPU هر
مرحله" — self-hosted Langfuse, and GPU time logged per step. Both are real:
a thin HTTP client against Langfuse's own public ingestion API contract
(`app/agents/tracing.py`), the same style as the WordPress/Telegram/Search
Console/GA4 connectors — no third-party SDK dependency, tested against
`httpx.MockTransport`.

```
run_agent() (per pipeline step)
  ├─ trace-create   — once per step, id = the package's own id, so every
  │                    step of one package shows up as one trace in Langfuse
  └─ generation-create — once per LLM attempt, including a rejected one
                          (level="WARNING") and an unreachable model server
                          (level="ERROR")

gpu.dispatch (per completed GPU job, every kind — image, video, TTS, LLM)
  └─ span-create — name "gpu:<kind>", duration = the job's own gpu_seconds,
                   the same number the quota ledger charges
```

## Why not the official Langfuse SDK

The platform's own connector convention (`app/connectors/search_console.py`,
`app/connectors/ga4.py`) already prefers a small, tested HTTP client over a
third-party SDK for a fixed, documented API contract — one dependency less,
and the request shape is visible and tested rather than hidden inside a
library. Langfuse's ingestion API (`POST /api/public/ingestion`, Basic Auth
with the project's public/secret key pair) is exactly such a contract.

## What's real, and what an operator still has to supply

| Piece | Status |
|---|---|
| The ingestion HTTP client (`LangfuseClient`) | **real** — tested against `httpx.MockTransport` |
| Instrumentation of every agent call and every GPU job | **real** — `tests/test_agent_runner.py`'s tracing tests, `app/worker/tasks/gpu.py` |
| Failure isolation | **real** — any Langfuse failure is logged and swallowed, never raised; tracing must not be able to fail a customer's package |
| A running self-hosted Langfuse instance | **operator-supplied** — `docker-compose.langfuse.yml` |

## Running it

Langfuse needs its own Postgres, Redis, ClickHouse and MinIO — it is not
safe to share this platform's own instances of those (different schemas,
different failure domains), and pulling in six extra containers by default
would eat into the 3090 Ti box's tight RAM budget (handoff section 12) for
every developer who does not need tracing running locally. It is therefore
a separate compose file, not part of `docker compose up -d`:

```bash
docker compose -f docker-compose.yml -f docker-compose.langfuse.yml up -d
```

Passing both `-f` flags puts every service in one Compose project and one
network, so the app can reach Langfuse by service name. Open
`http://localhost:3001` (Langfuse's own web UI; port 3000 is left free for a
locally-run Next.js panel) and set every `# CHANGEME` in
`docker-compose.langfuse.yml` before running this anywhere but a throwaway
dev box — the file bootstraps one org/project/user with a fixed key pair on
first boot (`LANGFUSE_INIT_PROJECT_PUBLIC_KEY`/`..._SECRET_KEY`) precisely so
those keys can be copied straight into the app's own `.env` without a
manual click-through:

```bash
# app's own .env
LANGFUSE_HOST=http://langfuse-web:3000
LANGFUSE_PUBLIC_KEY=<LANGFUSE_INIT_PROJECT_PUBLIC_KEY from docker-compose.langfuse.yml>
LANGFUSE_SECRET_KEY=<LANGFUSE_INIT_PROJECT_SECRET_KEY from docker-compose.langfuse.yml>
```

Until both `LANGFUSE_HOST` and the key pair are set, `app.agents.tracing`
picks `NoopTracingClient` — every dev and test run today, silently, with no
Langfuse instance required.
