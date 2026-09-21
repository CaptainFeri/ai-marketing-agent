# Instagram and LinkedIn post insights

Handoff section 11 (phase 2): "دریافت آمار Insights از کانال‌های اجتماعی"
— pulling engagement numbers for a published Instagram or LinkedIn post,
alongside the existing Search Console/GA4 sweep (`docs/analytics.md`).

```
analytics.pull_metrics_for_workspace()
  ├─ Search Console / GA4     AnalyticsCredential, matched by URL/path
  └─ Instagram / LinkedIn     ChannelCredential (the same one publishing used),
                               matched by external_id (the post's own id/urn)
```

## No separate credential

Search Console and GA4 read from a Google service account configured once
per workspace, independent of any publishing channel — a separate
`AnalyticsCredential`. Instagram and LinkedIn need nothing new: the same
access token already stored to *publish* a post (`ChannelCredential`,
`docs/publishing.md`) is what reads that post's own statistics back, since
it is the same account calling the same API.
`channel_credentials.active_credential_or_none()` is the quiet-lookup
counterpart to `active_credential_for()` (which the publishing path uses
and which raises when nothing is configured) — an unconfigured social
channel is the normal case for the metrics sweep, not an error.

## What each connector actually reads

**Instagram** (`app.connectors.instagram.InstagramInsightsClient`):
`GET /{media-id}/insights?metric=reach,likes,comments,saved,shares`. Scoped
to what a single FEED image post — the only shape actually wired up to
publish today — supports; a carousel or reel (task #50, not yet wired to
posting) would need different metric names (e.g. `plays`), added when that
task lands. No `impressions`/`clicks` here: Meta's Insights API dropped
`impressions` for individual media some versions back, and there is no
click-through concept for a feed post the way there is for a search
result — so Instagram's numbers land entirely in `MetricSnapshot.metrics`
(the free-form JSONB column), with `impressions`/`clicks` left `NULL`.

**LinkedIn** (`app.connectors.linkedin.LinkedInInsightsClient`):
`GET /rest/organizationalEntityShareStatistics?q=organizationalEntity&organizationalEntity={org}&shares[0]={post_urn}`
— the long-standing Organization Share Statistics API, still current under
the versioned `/rest` surface. Unlike Instagram, this *does* carry real
`impressionCount`/`clickCount`, which map straight onto
`MetricSnapshot.impressions`/`.clicks` — the same columns Search Console
fills. That is what makes a LinkedIn hook A/B pair automatically
CTR-comparable (`docs/ab-testing.md`) the moment both arms have this data;
no change to `app.services.ab_testing` was needed to pick it up.

## Matched by post id, not URL

Search Console/GA4 match a provider's row to a `Publication` by URL or
path, because that is the only handle a *search/analytics* property has on
a page. Instagram and LinkedIn's own APIs, by contrast, key insights by the
post's own id — `Publication.external_id` (the Graph API media id for
Instagram, the `urn:li:share:...` for LinkedIn), already stored by
`InstagramConnector`/`LinkedInConnector` at publish time — so `_pull_instagram`/
`_pull_linkedin` match on that instead of `external_url`.

## Tests

`tests/test_instagram_connector.py` and `tests/test_linkedin_connector.py`
test each `*InsightsClient` against `httpx.MockTransport` fixtures that
mirror the documented contract — no live Meta/LinkedIn credentials needed,
the same reasoning every other connector's tests already follow.
`tests/test_analytics.py` tests the sweep's wiring: matching by
`external_id`, writing the snapshot, and (for LinkedIn) that a real
impressions/clicks value flows all the way through to a decided A/B result.
