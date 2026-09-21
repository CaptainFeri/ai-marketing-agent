# Suggested publish time: rule-of-thumb, then real data

Handoff section 11 (phase 2): "پیشنهاد زمان انتشار (اول قاعده‌محور، بعد از
روی داده تعامل)" — a suggested time to schedule a post, rule-based at
first, switching to the workspace's own engagement data once there is
enough of it.

```
GET /workspaces/{id}/publish-time-suggestion?channel=wordpress
  └─ app.services.publish_time.suggest_publish_time()
       ├─ _historical_best_hour()   enough of this workspace's own data? use it
       ├─ else _RULE_OF_THUMB_HOUR[channel]
       └─ push forward past a known holiday or a spacing conflict
```

## Two tiers, same as the handoff asks for

**Rule of thumb** (`_RULE_OF_THUMB_HOUR`): morning for content meant to be
indexed or shared during the business day (WordPress, LinkedIn — 09:00),
evening for apps people check outside work (Telegram 19:00, Instagram
18:00). These are commonly cited general defaults, not audience-specific
tuning — a starting point, exactly what "قاعده‌محور" means, not a claim
about *this* workspace's actual audience.

**Historical engagement** (`_historical_best_hour`): once a channel has at
least `_MIN_SAMPLES_PER_HOUR` (3) data points in some hour of the day (in
the workspace's own timezone), the suggestion switches to whichever hour
has the best average engagement score among hours that clear that bar. A
single lucky post is noise, not a pattern, so an hour under the threshold
never overrides the rule of thumb no matter how good its one data point
looks.

The engagement score reuses the same reasoning
`app.services.ab_testing` already established: click-through rate
(`clicks / impressions`) when both are available (Search Console,
LinkedIn's share statistics), since that isolates a real audience signal
from raw volume — falling back to the sum of whatever numeric engagement
counts a channel does report (Instagram's reach/likes/comments/..., GA4's
page views/sessions) when it has no clicks/impressions concept at all. That
fallback is not perfectly comparable *across* channels, but a suggestion
for one channel only ever ranks that channel's own hours against each
other, which stay on the same metric source.

## Never lands on a bad slot

The chosen hour's next occurrence is pushed forward, one day at a time, up
to 30 days, past any date that is a known holiday (`docs/calendar.md`) or
that would violate the workspace's spacing rule
(`app.services.publishing.find_spacing_conflict` —
`docs/publishing.md`) on the requested channel. If nothing in that window
is free (an extremely tight spacing setting, in practice), it returns the
plain next occurrence anyway rather than failing outright — a suggestion
that occasionally still needs a second look beats one that refuses to
answer.

## Panel

The package detail page's scheduling form has a "Suggest a time" button,
enabled once a channel variant is selected. It calls the endpoint for that
variant's channel and fills the date/time field with the result, along
with a one-line note on which tier produced it (and, for the data-driven
tier, how many data points backed it).
