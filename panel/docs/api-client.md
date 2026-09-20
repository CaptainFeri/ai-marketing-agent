# The API client

`src/lib/api-client.ts` is the only place in the panel that calls `fetch`
against the backend. Everything else goes through `apiClient` from that
module, typed against `src/lib/api-schema.ts`.

## Regenerating the types

```
npm run gen:api
```

runs `scripts/dump_openapi.py` from the repo root (imports `app.main:app`
and writes `app.openapi()` to `panel/openapi.json` — no server needs to be
running, no network call is made) and then `openapi-typescript` against that
file into `src/lib/api-schema.ts`.

Run it whenever a backend route, request body, or response model changes.
`api-schema.ts` is generated — never hand-edit it; a hand fix will be
silently overwritten and, worse, will have hidden a real drift between the
panel and the backend until the next regeneration surfaces it as a type
error somewhere else.

Two response fields are typed as `{ [key: string]: unknown }` in the
generated output and stay that way on purpose: the questionnaire catalogue
and draft state (`QuestionnaireState`, `AcceptResponse`) are Python data from
`app/services/questionnaire.py`, not a pydantic model, so there is nothing
more specific for `openapi-typescript` to emit. `src/lib/questionnaire-types.ts`
mirrors that shape by hand for the panel's own use — keep the two in sync if
the catalogue's shape changes.

## Auth

`apiClient` is a single `openapi-fetch` client shared across the app, with
one `Middleware` (`authMiddleware`) doing two things:

- `onRequest` — attaches `Authorization: Bearer <access token>` from
  `src/lib/token-storage.ts` when one is held.
- `onResponse` — on any 401 that isn't from `/auth/login` or `/auth/refresh`
  itself, triggers one refresh attempt (deduplicated across concurrent
  401s via a shared in-flight promise) and retries the original request
  once. A failed refresh clears the stored tokens; the next render's
  `useAuth()` then reports `unauthenticated` and the app redirects to
  `/login`.

## Error handling

Every non-2xx response is thrown as an `ApiError` by `unwrap()`, carrying the
backend's own `error.code`, `error.message` and `error.details` (see
`app/main.py`'s exception handler) rather than a generic "request failed" —
call sites catch `ApiError` and show `err.message` directly, since the
backend already writes it for a human to read.

## Adding a new call

Call `apiClient.GET/POST/PATCH/DELETE("/api/v1/...")` with the exact path
string from `api-schema.ts`'s `paths` type — TypeScript checks the path,
params and body against the live backend contract. Wrap the result in
`unwrap()` to get the typed body or throw an `ApiError`. There is
deliberately no per-resource wrapper layer (no `packagesApi.list()`-style
module) — with the client already fully typed against the backend, a
wrapper would only rename what `apiClient.GET(...)` already says.
