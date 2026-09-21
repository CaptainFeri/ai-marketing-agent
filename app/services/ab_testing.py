"""A/B hook result tracking (handoff section 11: "نسخه‌های A/B قلاب و ثبت
نتیجه").

The marketizer can produce paired "a"/"b" hook variants for the same
channel (``ChannelVariant.ab_label``, enforced paired by
``MarketizedOutput._ab_arms_are_paired`` in ``app.agents.contracts``). Once
both arms are published and have measurable data, this compares them and
records a winner.

The metric compared is click-through rate, not raw clicks: a hook changes
whether someone clicks a result that already ranks where it ranks, not the
ranking itself, so CTR isolates the hook's own effect in a way raw clicks
(confounded by whichever arm happened to draw more impressions) would not.
CTR needs both clicks and impressions, which Search Console
(``app.services.analytics._pull_search_console``) and LinkedIn's own share
statistics (``_pull_linkedin``, ``docs/social-insights.md``) both provide —
GA4 and Instagram's insights carry neither, so a package's GA4-only or
Instagram channel simply has no A/B result yet. That is an honest gap in
the data those two APIs expose, not a bug in this comparison.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.enums import Channel
from app.db.models import ABTestResult, ContentPackage, MetricSnapshot, Publication, Variant


def _variant_ctr(session: Session, variant_id: uuid.UUID) -> tuple[int, int] | None:
    """Aggregate clicks/impressions across every metric snapshot recorded
    against this variant's own publication(s). ``None`` until there is at
    least one snapshot carrying impressions — CTR is undefined otherwise,
    and a 0/0 "tie" would be a false signal rather than a real one."""
    rows = session.execute(
        select(MetricSnapshot.clicks, MetricSnapshot.impressions)
        .join(Publication, MetricSnapshot.publication_id == Publication.id)
        .where(Publication.variant_id == variant_id, MetricSnapshot.impressions.is_not(None))
    ).all()
    if not rows:
        return None
    total_impressions = sum(impressions for _, impressions in rows)
    if total_impressions == 0:
        return None
    total_clicks = sum(clicks or 0 for clicks, _ in rows)
    return total_clicks, total_impressions


def _ab_pairs(session: Session, package_id: uuid.UUID) -> list[tuple[Channel, Variant, Variant]]:
    variants = session.scalars(
        select(Variant).where(Variant.package_id == package_id, Variant.ab_label.is_not(None))
    ).all()
    by_channel: dict[Channel, dict[str | None, Variant]] = {}
    for variant in variants:
        by_channel.setdefault(variant.channel, {})[variant.ab_label] = variant
    return [
        (channel, arms["a"], arms["b"])
        for channel, arms in by_channel.items()
        if "a" in arms and "b" in arms
    ]


def evaluate_package(session: Session, package: ContentPackage) -> list[ABTestResult]:
    """Compare every A/B pair this package has that now has enough data on
    both arms, and upsert the ``(package, channel)`` result. Called after
    each metrics pull (``app.services.analytics.pull_metrics_for_workspace``)
    — re-evaluated every time rather than decided once, since more data
    keeps arriving daily. A pair with no data yet on one or both arms is
    left alone; no row is written until it is decidable."""
    results: list[ABTestResult] = []
    for channel, variant_a, variant_b in _ab_pairs(session, package.id):
        ctr_a = _variant_ctr(session, variant_a.id)
        ctr_b = _variant_ctr(session, variant_b.id)
        if ctr_a is None or ctr_b is None:
            continue
        a_clicks, a_impressions = ctr_a
        b_clicks, b_impressions = ctr_b
        a_rate = a_clicks / a_impressions
        b_rate = b_clicks / b_impressions
        if a_rate > b_rate:
            winner_id: uuid.UUID | None = variant_a.id
        elif b_rate > a_rate:
            winner_id = variant_b.id
        else:
            winner_id = None

        now = datetime.now(UTC)
        statement = (
            pg_insert(ABTestResult)
            .values(
                tenant_id=package.tenant_id,
                package_id=package.id,
                channel=channel,
                a_variant_id=variant_a.id,
                b_variant_id=variant_b.id,
                winner_variant_id=winner_id,
                a_clicks=a_clicks,
                a_impressions=a_impressions,
                b_clicks=b_clicks,
                b_impressions=b_impressions,
                decided_at=now,
            )
            .on_conflict_do_update(
                constraint="package_channel_ab",
                set_={
                    "a_variant_id": variant_a.id,
                    "b_variant_id": variant_b.id,
                    "winner_variant_id": winner_id,
                    "a_clicks": a_clicks,
                    "a_impressions": a_impressions,
                    "b_clicks": b_clicks,
                    "b_impressions": b_impressions,
                    "decided_at": now,
                },
            )
            .returning(ABTestResult)
        )
        results.append(session.scalars(statement).one())
    return results


__all__ = ["evaluate_package"]
