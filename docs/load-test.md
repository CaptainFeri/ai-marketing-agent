# Load test: concurrent tenants

Handoff section 7, phase 1 week 9-10: "آزمون بار: ۳ مشتری هم‌زمان" — a load
test with 3 concurrent tenants. `scripts/load_test.py` drives N tenants
concurrently, each creating packages over real HTTP and taking them through
gate 1, against a live stack (real Postgres, real Celery dispatch, real HTTP)
— not an in-process simulation. It also re-checks the handoff's isolation
acceptance criterion under actual concurrent load, not just in the static
RLS tests (`tests/test_tenant_isolation.py`).

```bash
.venv/bin/python scripts/load_test.py --tenants 3 --packages-per-tenant 3
```

See the script's own docstring for the prerequisites (Postgres, Redis, the
API, one combined Celery worker + beat, all with `GPU_RUNTIME=simulated` and
`LLM_CLIENT=simulated` so it needs no GPU or model weights).

## Verified

Run in this repo's own dev sandbox (`scripts/dev_postgres.sh`, a local
`redis-server`, `uvicorn`, one combined worker on every queue, and beat):

```
3 tenants, 9 packages, 155.5s wall clock
  loadtest-085241a2: 3/3 packages reached gate 1 + approval
  loadtest-38e5b067: 3/3 packages reached gate 1 + approval
  loadtest-f1587b52: 3/3 packages reached gate 1 + approval

Succeeded: 9/9
Per-package time: min=140.4s mean=150.0s max=154.8s
Tenant isolation held: True
```

## Why per-package time is dominated by the dispatch cadence, not the model

`gpu.dispatch` (`app/worker/tasks/gpu.py`) is fired by Celery beat every 15
seconds and claims a batch of up to `GPU_MAX_BATCH_SIZE` (8) jobs at a time
(handoff section 6). Nine packages through the six-agent text pipeline is 54
jobs — about seven dispatch cycles, ~105s, before gate 1 is even reachable,
regardless of how fast the (simulated) model itself responds. This is the
real architecture, not a load-test artifact: phase 0's actual model timings
replace `DEFAULT_ESTIMATES` (`app/services/quota.py`), but the 15-second
cadence and the 8-job batch cap are scheduler decisions, not measurements —
tune `GPU_MAX_BATCH_SIZE` and the beat schedule once real throughput numbers
from phase 0 are in.
