# Hook A/B testing: paired variants, compared by CTR

Handoff section 11 (phase 2): "نسخه‌های A/B قلاب و ثبت نتیجه" — A/B versions
of a hook, and recording the result. Generating the paired variants was
already part of the marketizer's contract (`ChannelVariant.ab_label`,
`app/agents/contracts.py`); what this adds is the "ثبت نتیجه" half —
actually comparing the two arms once they have real data and recording a
winner.

```
MarketizedOutput.variants          "a"/"b" pair per channel, paired-or-none
  └─ _replace_variants()           Variant(ab_label="a"/"b", ...)
       └─ two Publications, same channel, two different posts

analytics.pull_metrics_for_workspace()   daily sweep (existing, section 7)
  └─ writes MetricSnapshot rows per publication
  └─ app.services.ab_testing.evaluate_package()
       └─ upserts ABTestResult(package_id, channel)
```

## Why CTR, not raw clicks

A hook changes whether someone clicks a result that already ranks where it
ranks — it does not change the ranking itself. Comparing raw clicks would be
confounded by whichever arm happened to draw more impressions (a post
published an hour earlier, a slightly different indexing time). Comparing
**click-through rate** — `clicks / impressions`, aggregated across every
`MetricSnapshot` recorded for each arm's own publication — isolates the
hook's own effect. `tests/test_ab_testing.py`'s
`test_a_clear_winner_is_decided_by_ctr_not_raw_clicks` locks this in: arm A
gets more raw clicks but a much lower CTR, and B still wins.

CTR needs both clicks and impressions, which today only Search Console
provides (`app.services.analytics._pull_search_console`) — GA4 carries
neither, and the not-yet-built social insights connectors (task #45) do
not either. A package's non-WordPress channels simply have no A/B result
yet; that is an honest gap in the data available today, not a bug in the
comparison itself. Once #45 lands with real click/impression numbers for a
social channel, `evaluate_package()` needs no changes to pick it up.

## Re-evaluated, not decided once

`ABTestResult` is one row per `(package_id, channel)`, upserted on every
metrics pull rather than written once and frozen. A pair with no data yet
on one or both arms is left alone — no row is written until it is
decidable. A pair with equal CTR gets a row with `winner_variant_id = NULL`
(a real tie, not "no data yet"). As more days of data arrive the row is
recomputed from scratch each time, so an early lead can flip; there is no
separate "final" state — the package's own status/lifecycle decides when
it is done, this table just always reflects the current standing.

Both variant foreign keys cascade on delete: a marketizer re-run deletes
and recreates a package's variants (`_replace_variants`), and a stale
comparison against variants that no longer exist disappears with them
rather than dangling.

## Panel

The package detail page shows an A/B results card (`ABTestResultsPanel`)
per channel that has a decidable pair — each arm's hook, CTR and raw
clicks/impressions, with the current winner highlighted (or a "tied so
far" note if genuinely equal).
