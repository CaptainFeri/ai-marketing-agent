"""Suggested publish time (handoff section 11: "پیشنهاد زمان انتشار (اول
قاعده‌محور، بعد از روی داده تعامل)") — rule-based by default, switching to
the workspace's own historical engagement once there is enough of it.

Neither tier guesses at a specific audience's behaviour without evidence:
the rule-of-thumb hours below are commonly cited general defaults (morning
for search/business content, evening for social apps) — a starting point,
not a tuned recommendation — and are used only until a channel has real
data to replace them with. Once it does, the suggestion is the hour of day
(in the workspace's own timezone) with the best historical engagement among
this workspace's own published posts on that channel, requiring at least
``_MIN_SAMPLES_PER_HOUR`` data points before an hour is trusted — a single
lucky post is noise, not a pattern.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.enums import Channel
from app.db.models import ContentPackage, MetricSnapshot, Publication, Workspace
from app.services.holidays import holiday_on
from app.services.publishing import find_spacing_conflict

#: General, not audience-specific, publishing-time conventions: morning for
#: content meant to be indexed/shared during the business day (WordPress,
#: LinkedIn), evening for apps people check outside work (Telegram,
#: Instagram). X has no connector to schedule against
#: (``app.services.x_export``) but still gets a sensible reference hour for
#: the manual-publish export. Any channel not listed falls back to 10:00.
_RULE_OF_THUMB_HOUR: dict[Channel, int] = {
    Channel.WORDPRESS: 9,
    Channel.TELEGRAM: 19,
    Channel.INSTAGRAM: 18,
    Channel.LINKEDIN: 9,
    Channel.X: 12,
    Channel.YOUTUBE: 17,
    Channel.APARAT: 17,
}
_DEFAULT_HOUR = 10

#: An hour needs this many of the workspace's own data points before its
#: average engagement is trusted over the rule of thumb.
_MIN_SAMPLES_PER_HOUR = 3

#: How far forward to search for a slot with no holiday and no spacing
#: conflict before giving up and returning the plain next occurrence anyway.
_MAX_DAYS_AHEAD = 30

Basis = Literal["rule_of_thumb", "historical_engagement"]


@dataclass(frozen=True)
class SuggestedTime:
    scheduled_at: datetime
    basis: Basis
    hour_of_day: int
    sample_size: int


def _engagement_score(clicks: int | None, impressions: int | None, metrics: dict) -> float:
    """Click-through rate when both numbers exist (Search Console,
    LinkedIn — see ``app.services.ab_testing`` for why CTR is the right
    comparison when it's available); otherwise the sum of whatever numeric
    engagement counts the channel does report (Instagram's reach/likes/...,
    GA4's page views/sessions) — not perfectly comparable across sources,
    but this only ever ranks one channel's own hours against each other,
    which stay on the same source."""
    if clicks is not None and impressions:
        return clicks / impressions
    return sum(value for value in metrics.values() if isinstance(value, int | float))


def _historical_best_hour(
    session: Session, workspace: Workspace, channel: Channel
) -> tuple[int, int] | None:
    rows = session.execute(
        select(
            Publication.scheduled_at,
            MetricSnapshot.clicks,
            MetricSnapshot.impressions,
            MetricSnapshot.metrics,
        )
        .join(MetricSnapshot, MetricSnapshot.publication_id == Publication.id)
        .join(ContentPackage, Publication.package_id == ContentPackage.id)
        .where(ContentPackage.workspace_id == workspace.id, Publication.channel == channel)
    ).all()
    if not rows:
        return None

    tz = ZoneInfo(workspace.timezone)
    scores_by_hour: dict[int, list[float]] = defaultdict(list)
    for scheduled_at, clicks, impressions, metrics in rows:
        aware = (
            scheduled_at if scheduled_at.tzinfo is not None else scheduled_at.replace(tzinfo=UTC)
        )
        hour = aware.astimezone(tz).hour
        scores_by_hour[hour].append(_engagement_score(clicks, impressions, metrics))

    candidates = {
        hour: (sum(scores) / len(scores), len(scores))
        for hour, scores in scores_by_hour.items()
        if len(scores) >= _MIN_SAMPLES_PER_HOUR
    }
    if not candidates:
        return None
    best_hour = max(candidates, key=lambda hour: candidates[hour][0])
    return best_hour, candidates[best_hour][1]


def _next_occurrence(after: datetime, hour: int, tz: ZoneInfo) -> datetime:
    local_after = after.astimezone(tz)
    candidate = local_after.replace(hour=hour, minute=0, second=0, microsecond=0)
    if candidate <= local_after:
        candidate += timedelta(days=1)
    return candidate.astimezone(UTC)


def suggest_publish_time(
    session: Session,
    workspace: Workspace,
    channel: Channel,
    *,
    after: datetime | None = None,
) -> SuggestedTime:
    """The next reasonable time to schedule ``channel`` in ``workspace``,
    at or after ``after`` (default: now) — the chosen hour of day, pushed
    forward past any known holiday (``docs/calendar.md``) or spacing
    conflict (``docs/publishing.md``) on the first ``_MAX_DAYS_AHEAD``
    days; if none of them are free, returns the plain next occurrence of
    that hour rather than failing outright."""
    after = after or datetime.now(UTC)
    tz = ZoneInfo(workspace.timezone)

    historical = _historical_best_hour(session, workspace, channel)
    if historical is not None:
        hour, sample_size = historical
        basis: Basis = "historical_engagement"
    else:
        hour = _RULE_OF_THUMB_HOUR.get(channel, _DEFAULT_HOUR)
        sample_size = 0
        basis = "rule_of_thumb"

    candidate = _next_occurrence(after, hour, tz)
    for _ in range(_MAX_DAYS_AHEAD):
        local_date = candidate.astimezone(tz).date()
        if holiday_on(local_date) is None and (
            find_spacing_conflict(
                session, workspace_id=workspace.id, channel=channel, scheduled_at=candidate
            )
            is None
        ):
            return SuggestedTime(
                scheduled_at=candidate, basis=basis, hour_of_day=hour, sample_size=sample_size
            )
        candidate += timedelta(days=1)

    return SuggestedTime(
        scheduled_at=candidate, basis=basis, hour_of_day=hour, sample_size=sample_size
    )


__all__ = ["SuggestedTime", "suggest_publish_time"]
