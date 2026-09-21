# Self-service signup

Handoff section 11, phase 2: "ثبت‌نام خودکار مشتری جدید (self-service
onboarding)". Until now, provisioning a tenant meant a platform operator
calling `POST /tenants` (`app/api/v1/tenants.py`) with a superuser token —
`app.services.auth.create_tenant_with_owner` always did the real work, this
just adds a public front door to it.

```
POST /api/v1/auth/register  (no token needed)
  ├─ rate-limited by caller IP and by the email in the request
  ├─ app.services.auth.register()
  │    ├─ refuses an email that already has an account (409) — see below
  │    └─ create_tenant_with_owner(), forced to Plan.TRIAL / weight 1
  └─ logs the new owner straight in, same TokenPair shape as /auth/login
```

Panel: `/register`, linked from `/login` and back.

## Why a new schema instead of reusing `TenantCreate`

`TenantCreate` (used by the operator-only endpoint) accepts `plan` and
`quota_weight` — a self-service caller choosing their own billing plan and
GPU quota weight is not a permission a public, unauthenticated endpoint
should have. `SelfServiceSignup` (`app/schemas/tenant.py`) simply has no
such fields; `app.services.auth.register()` fixes both server-side.

## Why an existing email is refused, not reused

`create_tenant_with_owner` will happily attach an *existing* user as a new
tenant's owner without re-checking their password — a deliberate convenience
for the trusted, operator-only path (one person legitimately administering
several client tenants under one login). Over an unauthenticated endpoint
that same behavior would let anyone type a stranger's email address into the
signup form and grant that stranger owner access to a new tenant they never
asked for. `register()` checks the email first and refuses with a 409
("log in instead") rather than falling into that path.

## Rate limiting

The one genuinely new piece of infrastructure this needed: nothing in the
app rate-limited anything before, because every other endpoint already
requires a bearer token. `app/core/rate_limit.py` follows the same
real/in-memory-stand-in split as `app.services.storage`:

- `RedisRateLimiter` — a real `INCR`/`EXPIRE` fixed window. Redis is already
  a required dependency (the Celery broker), so this is not new
  infrastructure to run — set `RATE_LIMIT_BACKEND=redis` for it.
- `InMemoryRateLimiter` — the default. Correct as long as the API runs as a
  single process, which `docker-compose.yml`'s `api` service does.

Two independent limits apply to `POST /auth/register`: one per caller IP
(`REGISTRATION_RATE_LIMIT_PER_HOUR`, default 5/hour) and one per the email in
the request body (`REGISTRATION_RATE_LIMIT_PER_EMAIL_PER_HOUR`, default
3/hour) — the second catches repeated attempts against one address from
behind a shared or rotating IP.

## Verified

Backend: `tests/test_api.py` (signup, auto-login, forced trial plan, both
rate limits, the duplicate-slug and duplicate-email refusals) and
`tests/test_rate_limit.py`. End to end in this sandbox: a real browser
(Playwright) against a real `uvicorn` + real Postgres + the real Next.js dev
server — filled the `/register` form, landed on `/workspaces` already
signed in.
