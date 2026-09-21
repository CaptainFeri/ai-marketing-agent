# Search Console and GA4

Handoff section 7, phase 1 weeks 7-8: "اتصال Search Console و GA4 و داشبورد
پایه" — connect both, and a basic dashboard. Real end to end: real service-
account auth, real HTTP against both APIs' documented contracts, real
per-page matching, and a real state transition into the "measuring" stage
of the seven-stage package lifecycle.

```
nightly beat (06:00 UTC)
  └─ maintenance.pull_daily_metrics
       └─ for every active workspace: analytics.pull_metrics_for_workspace
            ├─ Search Console: POST .../searchAnalytics/query, matched by exact page URL
            ├─ GA4:            POST .../runReport,             matched by URL path
            └─ MetricSnapshot upserted per (publication, source, day)
                 └─ a package's first matched snapshot moves it
                    published → measuring (handoff section 3, step 7)
```

## Why a service account, not user OAuth

Search Console and GA4 both default to a three-legged OAuth flow — a
browser redirect, a consent screen, a refresh token to keep alive. None of
that fits a backend service with no user sitting in front of it at 6am when
the nightly sweep runs. A Google service account (RFC 7523's JWT Bearer
flow) is the shape Google itself documents for exactly this case: the
workspace owner adds the service account's email as a user on the Search
Console property (or a Viewer on the GA4 property) once, in Google's own
console, and `app.connectors.google_auth` signs a fresh, short-lived
assertion on every call after that — no refresh token, no browser, ever.

## Matching rows to publications

Neither API's query syntax makes "give me just these ten URLs" convenient,
and a property's whole day of data is one HTTP response regardless — so
`app.services.analytics` pulls everything for the day and matches it against
this tenant's own `Publication.external_url` rows in Python:

- **Search Console** keys its rows by the full page URL — matched by exact
  string equality against `external_url`.
- **GA4** keys its rows by `pagePath` — `external_url` is parsed down to its
  path first (`urlparse(...).path`) before matching.

A publication with no `external_url` (Telegram has none to measure) is
never included in the query in the first place.

## No separate transcription step for "did this reach measuring"

A package can carry more than one channel's publication. Rather than model
per-channel measurement state, the package-wide status follows the same
coarse seven-stage handoff model everywhere else does: the *first*
publication to succeed moves the package `scheduled → published`
(`app.services.publishing.attempt`), and the first one to get an actual
metric snapshot moves it `published → measuring`
(`app.services.analytics._advance_measured_packages`). Both were real gaps
this closed — the state machine already allowed both transitions
(`app.services.packages.ALLOWED_TRANSITIONS`), but nothing had ever
triggered either one.

The same sweep also re-runs any hook A/B comparison a measured package has
(`app.services.analytics._evaluate_ab_results` → `app.services.ab_testing`)
— see `docs/ab-testing.md` — and pulls Instagram/LinkedIn post insights
alongside Search Console/GA4 — see `docs/social-insights.md`.

## What's real today, and what an operator still has to supply

| Piece | Status |
|---|---|
| Service-account JWT signing and token exchange | **real** — `PyJWT` + `cryptography`, no model, no phase-0 dependency |
| Search Console / GA4 HTTP clients | **real** — fixed, documented REST contracts, tested against `httpx.MockTransport` the same way `app.connectors.wordpress`/`telegram` are |
| Credential storage | **real** — encrypted the same way a channel credential is (`app.core.crypto`), its own table (`analytics_credential`) since nothing is ever published "to" an analytics provider |
| Matching, upserting, the state transition | **real** and covered end to end in `tests/test_analytics.py` |
| A live Search Console/GA4 property to actually query | **operator-supplied** — this platform never creates one; the workspace owner adds the service account to their own existing property |

## Setting one up

1. In Google Cloud Console, create a service account and download its JSON
   key.
2. In Search Console, add the service account's `client_email` as a user on
   the property (Settings → Users and permissions). For GA4, add it as a
   Viewer on the property (Admin → Property access management).
3. In the panel, under a workspace's **Analytics** page, paste the
   downloaded JSON key and the site URL (Search Console) or property ID
   (GA4). `POST /workspaces/{id}/analytics-credentials` validates and
   encrypts it; nothing about the key is ever returned by the API again.
4. The nightly sweep picks it up automatically. `POST
   /workspaces/{id}/analytics-credentials/pull` backfills a specific day on
   demand, for an operator who needs a day the sweep missed.
