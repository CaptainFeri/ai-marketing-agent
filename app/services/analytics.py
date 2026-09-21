"""Pulling daily metrics from Search Console, GA4 (handoff section 7) and,
for Instagram/LinkedIn, the channels' own post insights (handoff section
11: "دریافت آمار Insights از کانال‌های اجتماعی").

One publication may show up in more than one provider (a WordPress post has
both search traffic and page views) or none (a Telegram post has nothing
measurable). Matching a provider's result rows to *this tenant's*
publications is done in Python rather than by asking each API to filter —
walking the returned rows once and matching by URL/path/id is simpler than
any of these APIs' filter syntax and identical in cost, since a whole day's
results (or, for Instagram/LinkedIn, one post's own insights) is already
one HTTP response either way.

Search Console and GA4 read from a separate ``AnalyticsCredential`` (a
Google service account, configured once per workspace independent of any
channel). Instagram and LinkedIn need no separate credential at all — they
read with the *same* ``ChannelCredential`` token already stored for
publishing, since it is the same API the post itself went out through.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, timedelta
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.connectors import ga4 as ga4_connector
from app.connectors import instagram as instagram_connector
from app.connectors import linkedin as linkedin_connector
from app.connectors import search_console as search_console_connector
from app.connectors.analytics_credentials import Ga4Credential, SearchConsoleCredential
from app.connectors.credentials import InstagramCredential, LinkedInCredential
from app.db.enums import AnalyticsProvider, Channel, PackageStatus, PublicationStatus
from app.db.models import (
    AnalyticsCredential,
    ChannelCredential,
    ContentPackage,
    MetricSnapshot,
    Publication,
)
from app.services import ab_testing, analytics_credentials, channel_credentials
from app.services import packages as package_service

logger = logging.getLogger(__name__)


def yesterday(today: date | None = None) -> date:
    return (today or date.today()) - timedelta(days=1)


def _published_publications(session: Session, workspace_id: uuid.UUID) -> list[Publication]:
    return list(
        session.scalars(
            select(Publication)
            .join(ContentPackage, Publication.package_id == ContentPackage.id)
            .where(
                ContentPackage.workspace_id == workspace_id,
                Publication.status == PublicationStatus.PUBLISHED,
                Publication.external_url.is_not(None),
            )
        ).all()
    )


def _upsert_snapshot(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    publication_id: uuid.UUID,
    source: str,
    captured_for: date,
    impressions: int | None,
    clicks: int | None,
    position: float | None,
    metrics: dict,
) -> None:
    captured_for_dt = captured_for.isoformat()
    statement = (
        pg_insert(MetricSnapshot)
        .values(
            tenant_id=tenant_id,
            publication_id=publication_id,
            source=source,
            captured_for=captured_for_dt,
            impressions=impressions,
            clicks=clicks,
            position=position,
            metrics=metrics,
        )
        .on_conflict_do_update(
            constraint="publication_source_day",
            set_={
                "impressions": impressions,
                "clicks": clicks,
                "position": position,
                "metrics": metrics,
            },
        )
    )
    session.execute(statement)


def _pull_search_console(
    session: Session,
    credential: AnalyticsCredential,
    publications: list[Publication],
    day: date,
    *,
    client=None,
) -> set[uuid.UUID]:
    payload = analytics_credentials.decrypt_for_read(credential)
    assert isinstance(payload, SearchConsoleCredential)  # noqa: S101 - schema-enforced by provider

    rows = search_console_connector.SearchConsoleClient(client).daily_page_metrics(
        payload.service_account.to_service_account(), payload.site_url, day
    )
    by_url = {row.page: row for row in rows}

    written: set[uuid.UUID] = set()
    for publication in publications:
        # _published_publications already filters to external_url IS NOT
        # NULL; the Optional here is only the column's static type.
        if publication.external_url is None:
            continue
        row = by_url.get(publication.external_url)
        if row is None:
            continue
        _upsert_snapshot(
            session,
            tenant_id=publication.tenant_id,
            publication_id=publication.id,
            source=AnalyticsProvider.SEARCH_CONSOLE.value,
            captured_for=day,
            impressions=row.impressions,
            clicks=row.clicks,
            position=row.position,
            metrics={"ctr": row.ctr},
        )
        written.add(publication.id)
    return written


def _pull_ga4(
    session: Session,
    credential: AnalyticsCredential,
    publications: list[Publication],
    day: date,
    *,
    client=None,
) -> set[uuid.UUID]:
    payload = analytics_credentials.decrypt_for_read(credential)
    assert isinstance(payload, Ga4Credential)  # noqa: S101 - schema-enforced by provider

    rows = ga4_connector.Ga4Client(client).daily_page_metrics(
        payload.service_account.to_service_account(), payload.property_id, day
    )
    by_path = {row.page_path: row for row in rows}

    written: set[uuid.UUID] = set()
    for publication in publications:
        path = urlparse(publication.external_url or "").path
        row = by_path.get(path)
        if row is None:
            continue
        _upsert_snapshot(
            session,
            tenant_id=publication.tenant_id,
            publication_id=publication.id,
            source=AnalyticsProvider.GA4.value,
            captured_for=day,
            impressions=None,
            clicks=None,
            position=None,
            metrics={
                "screen_page_views": row.screen_page_views,
                "sessions": row.sessions,
                "engaged_sessions": row.engaged_sessions,
                "average_session_duration": row.average_session_duration,
            },
        )
        written.add(publication.id)
    return written


def _pull_instagram(
    session: Session,
    credential: ChannelCredential,
    publications: list[Publication],
    day: date,
    *,
    client=None,
) -> set[uuid.UUID]:
    payload = channel_credentials.decrypt_for_publish(credential)
    assert isinstance(payload, InstagramCredential)  # noqa: S101 - schema-enforced by channel

    insights = instagram_connector.InstagramInsightsClient(client)
    written: set[uuid.UUID] = set()
    for publication in publications:
        if publication.channel is not Channel.INSTAGRAM or not publication.external_id:
            continue
        try:
            data = insights.media_insights(payload, publication.external_id)
        except Exception:  # noqa: BLE001 - one post's failure must not sink the sweep
            logger.exception(
                "instagram insights pull failed", extra={"publication_id": str(publication.id)}
            )
            continue
        if not data:
            continue
        _upsert_snapshot(
            session,
            tenant_id=publication.tenant_id,
            publication_id=publication.id,
            source=Channel.INSTAGRAM.value,
            captured_for=day,
            impressions=None,
            clicks=None,
            position=None,
            metrics=data,
        )
        written.add(publication.id)
    return written


def _pull_linkedin(
    session: Session,
    credential: ChannelCredential,
    publications: list[Publication],
    day: date,
    *,
    client=None,
) -> set[uuid.UUID]:
    payload = channel_credentials.decrypt_for_publish(credential)
    assert isinstance(payload, LinkedInCredential)  # noqa: S101 - schema-enforced by channel

    insights = linkedin_connector.LinkedInInsightsClient(client)
    written: set[uuid.UUID] = set()
    for publication in publications:
        if publication.channel is not Channel.LINKEDIN or not publication.external_id:
            continue
        try:
            stats = insights.share_statistics(payload, publication.external_id)
        except Exception:  # noqa: BLE001 - one post's failure must not sink the sweep
            logger.exception(
                "linkedin insights pull failed", extra={"publication_id": str(publication.id)}
            )
            continue
        if not stats:
            continue
        _upsert_snapshot(
            session,
            tenant_id=publication.tenant_id,
            publication_id=publication.id,
            source=Channel.LINKEDIN.value,
            captured_for=day,
            impressions=stats.get("impressions"),
            clicks=stats.get("clicks"),
            position=None,
            metrics={k: v for k, v in stats.items() if k not in {"impressions", "clicks"}},
        )
        written.add(publication.id)
    return written


def pull_metrics_for_workspace(
    session: Session,
    workspace_id: uuid.UUID,
    day: date | None = None,
    *,
    search_console_client=None,
    ga4_client=None,
    instagram_client=None,
    linkedin_client=None,
) -> int:
    """Pull and store one day's metrics for every published, measurable
    publication in a workspace. Returns how many snapshot rows were written
    — one publication measured by more than one provider counts once per
    provider, since each is its own row in ``metric_snapshot``.

    A provider with no active credential is skipped quietly — most
    workspaces will only ever configure some of the four. A provider whose
    credential *is* configured but whose request fails is logged and
    skipped too, so one broken integration never blocks the others.
    """
    day = day or yesterday()
    publications = _published_publications(session, workspace_id)
    if not publications:
        return 0

    row_count = 0
    measured_publication_ids: set[uuid.UUID] = set()
    for provider, puller, client in (
        (AnalyticsProvider.SEARCH_CONSOLE, _pull_search_console, search_console_client),
        (AnalyticsProvider.GA4, _pull_ga4, ga4_client),
    ):
        credential = analytics_credentials.active_credential_for(session, workspace_id, provider)
        if credential is None:
            continue
        try:
            matched = puller(session, credential, publications, day, client=client)
        except Exception:  # noqa: BLE001 - one provider's failure must not sink the sweep
            logger.exception(
                "metrics pull failed",
                extra={"workspace_id": str(workspace_id), "provider": provider.value},
            )
            continue
        row_count += len(matched)
        measured_publication_ids |= matched

    for channel, social_puller, social_client in (
        (Channel.INSTAGRAM, _pull_instagram, instagram_client),
        (Channel.LINKEDIN, _pull_linkedin, linkedin_client),
    ):
        channel_credential = channel_credentials.active_credential_or_none(
            session, workspace_id, channel
        )
        if channel_credential is None:
            continue
        try:
            matched = social_puller(
                session, channel_credential, publications, day, client=social_client
            )
        except Exception:  # noqa: BLE001 - one channel's failure must not sink the sweep
            logger.exception(
                "metrics pull failed",
                extra={"workspace_id": str(workspace_id), "provider": channel.value},
            )
            continue
        row_count += len(matched)
        measured_publication_ids |= matched

    if measured_publication_ids:
        measured = [pub for pub in publications if pub.id in measured_publication_ids]
        _advance_measured_packages(session, measured)
        _evaluate_ab_results(session, measured)
    return row_count


def _evaluate_ab_results(session: Session, measured_publications: list[Publication]) -> None:
    """Re-run the A/B comparison (handoff section 11) for every package that
    just got new metrics data — cheap to call unconditionally since a
    package with no "a"/"b" pair simply has nothing to compare."""
    package_ids = {publication.package_id for publication in measured_publications}
    for package_id in package_ids:
        package = session.get(ContentPackage, package_id)
        if package is not None:
            ab_testing.evaluate_package(session, package)


def _advance_measured_packages(session: Session, measured_publications: list[Publication]) -> None:
    """The first metric snapshot a package's own publication gets is what
    moves it from ``published`` to ``measuring`` (handoff section 3, step 7)
    — a package can have more than one publication, so this only needs one
    of them to have data, but it must be one of *this* package's own.
    """
    package_ids = {publication.package_id for publication in measured_publications}
    for package_id in package_ids:
        package = session.get(ContentPackage, package_id)
        if package is not None and package.status is PackageStatus.PUBLISHED:
            package_service.transition(package, PackageStatus.MEASURING)
