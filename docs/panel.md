# The Next.js panel

Handoff section 4: "پنل: Next.js با پشتیبانی RTL/LTR" — the customer-facing
screens for the brand questionnaire wizard, the two human approval gates, and
publishing. Lives in `panel/`, Next.js 16 (App Router, TypeScript, Tailwind
v4, Turbopack), talking to the FastAPI service over plain client-side HTTP.

## Why no Server Actions

The panel is a pure client of a separate service — the data layer is
`app/`, not `panel/`. Next.js 16's Server Functions exist to mutate data a
server component already has a connection to (a database, a filesystem);
here every mutation is a request to a different origin the browser already
knows how to reach directly. Routing it through a Next.js server action would
add a hop with no benefit and a second place auth state could drift out of
sync. CORS for this is already open in `app/main.py` for
`http://localhost:3000` when `ENVIRONMENT=local`.

## Generated types, never hand-edited

```
npm run gen:api
```

dumps the FastAPI app's own `app.openapi()` to `panel/openapi.json`
(`scripts/dump_openapi.py`, no running server needed) and pipes it through
`openapi-typescript` into `src/lib/api-schema.ts`. A route or field the
backend renames shows up here as a type error, not a runtime one. See
`panel/docs/api-client.md` for the client that wraps it.

Two backend endpoints return `dict[str, Any]` on purpose — the questionnaire
catalogue and draft state (`QuestionnaireState`) — because that shape is
Python data (`app/services/questionnaire.py`), not a pydantic model. Their
real shape is mirrored by hand in `src/lib/questionnaire-types.ts`; keep it
in sync if the catalogue's shape changes.

## Auth

JWT pair in `localStorage`, not a cookie — nothing here is read by a Next.js
server component, so a cookie's CSRF surface would be pure cost. A single
401 anywhere triggers one deduplicated refresh attempt
(`src/lib/api-client.ts`); a second failure clears the session and the next
render redirects to `/login`. `src/lib/auth-context.tsx` re-reads
`GET /auth/me` on every mount rather than trusting the JWT's own claims, so a
revoked membership or a role change takes effect on the next load instead of
whenever the access token happens to expire.

## RTL/LTR

`src/lib/locale-context.tsx` is the one place that decides `dir`: Persian and
Arabic are `rtl`, English is `ltr`, set on `<html>` and everything else
follows through Tailwind's logical properties (`ms-`/`me-`, `text-start`)
rather than components branching on locale themselves. UI chrome strings live
in `src/lib/translations.ts` for the three shipped locales; question and
section text comes from the backend catalogue, which already carries
fa/en/ar labels per question.

## Screens

- `/login` — email + password, optional tenant slug for a user in more than
  one tenant.
- `/workspaces` — every workspace the caller has a role on.
- `/workspaces/{id}/questionnaire` — the wizard. Renders whatever
  `GET .../questionnaire` returns rather than hard-coding a form, so a
  question added to `app/services/questionnaire.py` appears with no panel
  change. All seven question types (`text`, `url`, `textarea`, `list`,
  `choice`, `multi_choice`, `pairs`) render through
  `src/components/question-field.tsx`. "Guess it" polls every 3s while
  `suggestion_status` is `running`; nothing is merged into the answers until
  a suggestion is explicitly accepted per question or all at once.
- `/workspaces/{id}/packages` — create and list content packages.
- `/workspaces/{id}/packages/{id}` — pipeline step history with per-step
  re-run, gate 1 (text review) when the package is `text_review`, the
  generated channel variants and media gallery, gate 2 (media selection)
  when the package is `selection`, and publication scheduling once a variant
  is selected. A selected X variant shows a copy-to-post thread instead
  (`docs/x-export.md`) — X has no publish connector to schedule against.
- `/workspaces/{id}/channels` — create and deactivate WordPress, Telegram,
  Instagram and LinkedIn credentials. Only the channels with a real connector
  (`app/connectors/credentials.py`) are offered; a credential is never
  editable, only deactivated, so an audit trail of what published survives.
- `/workspaces/{id}/quota` — today's GPU allocation and what it still buys,
  from `GET /gpu/quota`.
- `/workspaces/{id}/analytics` — create and deactivate Search Console/GA4
  credentials (a pasted Google service account key), and a manual "pull now"
  for backfilling a day the nightly sweep missed. See `docs/analytics.md`.
  The package detail page's own metrics table (`GET
  /packages/{id}/metrics`) is the "basic dashboard" the handoff asks for.

## Running it locally

```
cd panel
npm install
NEXT_PUBLIC_API_URL=http://127.0.0.1:8000 npm run dev
```

The backend must be reachable at `NEXT_PUBLIC_API_URL` (defaults to
`http://127.0.0.1:8000`) with `ENVIRONMENT=local` so its CORS allows
`http://localhost:3000` — open the panel at `http://localhost:3000`, not
`127.0.0.1:3000`, or the browser's request is same-origin-blocked before it
ever reaches the API. `next.config.ts` also allowlists both hosts for the
dev server's own HMR socket.

Verified end to end against the real FastAPI service and a real Postgres
database in this sandbox: login, RTL default with a live switch to LTR, the
questionnaire wizard reading the live catalogue and saving answers, package
creation and detail rendering, and channel credential creation. The pieces
that need infrastructure this sandbox doesn't have — a running GPU pipeline
producing a package past `drafting`, so gate 1/2 and publishing can be
exercised with real generated content — are unverified beyond the API
contracts the generated types already pin down.
