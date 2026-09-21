"""Scheduling and running a publish attempt (handoff section 3, step 6).

Content is gathered once from the database into a plain
:class:`~app.connectors.base.PublishContent`, so a connector never touches a
session — the same separation :mod:`app.agents.executor` keeps between the
database and the model call.

Retries: three attempts, then an operator is alerted, per the handoff. That
state lives entirely on the ``Publication`` row (``attempt_count``,
``last_error``, ``operator_alerted``) rather than in Celery's own retry
machinery, for the same reason ``app.worker.dispatcher`` does its own GPU job
retries instead of leaning on ``@task(retry=...)``: a row anyone can query is
easier to reason about, to test without a broker, and to show an editor in
the panel than a retry counter that only exists inside Celery.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors import ConnectorError, MediaForPublish, PublishContent, build_connector
from app.core.errors import InvalidStateError, NotFoundError
from app.db.enums import Channel, MediaKind, PackageStatus, PublicationStatus
from app.db.models import ContentPackage, MediaAsset, Publication, Variant, Workspace
from app.services import channel_credentials, storage
from app.services import packages as package_service

logger = logging.getLogger(__name__)

#: Handoff section 3, step 6: three attempts, then the operator is alerted.
MAX_ATTEMPTS = 3


#: Publications that still occupy a slot in the audience's feed — a
#: cancelled or failed one never went out and frees its slot back up.
_OCCUPIES_A_SLOT = (
    PublicationStatus.SCHEDULED,
    PublicationStatus.PUBLISHING,
    PublicationStatus.PUBLISHED,
)


def _as_aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _check_spacing(
    session: Session,
    *,
    workspace_id: uuid.UUID,
    channel: Channel,
    scheduled_at: datetime,
    exclude_publication_id: uuid.UUID | None = None,
) -> None:
    """Handoff section 11: "قوانین فاصله بین پست‌ها" — refuse a slot that
    lands too close to another post already going out on the same channel,
    rather than crowding the audience's feed. ``0`` turns the rule off."""
    workspace = session.get(Workspace, workspace_id)
    if workspace is None or workspace.min_publish_spacing_minutes <= 0:
        return
    window = timedelta(minutes=workspace.min_publish_spacing_minutes)
    target_at = _as_aware(scheduled_at)

    query = (
        select(Publication)
        .join(ContentPackage, Publication.package_id == ContentPackage.id)
        .where(
            ContentPackage.workspace_id == workspace_id,
            Publication.channel == channel,
            Publication.status.in_(_OCCUPIES_A_SLOT),
        )
    )
    if exclude_publication_id is not None:
        query = query.where(Publication.id != exclude_publication_id)

    for other in session.scalars(query):
        other_at = _as_aware(other.scheduled_at)
        if abs(target_at - other_at) < window:
            raise InvalidStateError(
                f"another {channel.value} post is already scheduled for "
                f"{other_at.isoformat()}, within this workspace's "
                f"{workspace.min_publish_spacing_minutes}-minute spacing rule",
                details={
                    "conflicting_publication_id": str(other.id),
                    "conflicting_scheduled_at": other_at.isoformat(),
                    "min_publish_spacing_minutes": workspace.min_publish_spacing_minutes,
                },
            )


def schedule_publication(
    session: Session,
    package: ContentPackage,
    variant: Variant,
    scheduled_at: datetime,
) -> Publication:
    """Queue one channel's selected variant for publishing.

    Does not require the package to have finished gate 2 — an editor may
    schedule channels one at a time as they are approved — but does require
    the variant to actually belong to this package and to have been marked
    selected, since scheduling an option nobody chose would publish
    something the gate never actually approved.
    """
    if variant.package_id != package.id:
        raise InvalidStateError("that variant does not belong to this package")
    if not variant.is_selected:
        raise InvalidStateError("only a selected variant can be scheduled")
    if variant.channel is Channel.X:
        raise InvalidStateError(
            "X has no publish connector (handoff section 11) — use "
            "GET /packages/{package_id}/x-export for a manual-publish package instead "
            "of scheduling one"
        )
    _check_spacing(
        session,
        workspace_id=package.workspace_id,
        channel=variant.channel,
        scheduled_at=scheduled_at,
    )

    publication = Publication(
        tenant_id=package.tenant_id,
        package_id=package.id,
        variant_id=variant.id,
        channel=variant.channel,
        status=PublicationStatus.SCHEDULED,
        scheduled_at=scheduled_at,
    )
    session.add(publication)
    session.flush()
    return publication


def reschedule_publication(
    session: Session, publication: Publication, scheduled_at: datetime
) -> Publication:
    """Move a still-pending publication to a new time — what the panel's
    drag-and-drop calendar (handoff section 11) calls when a chip is moved
    to a different day. Re-checks the same spacing rule
    ``schedule_publication`` does, excluding the publication's own current
    slot so moving it a few minutes within its own window is not refused
    against itself."""
    if publication.status is not PublicationStatus.SCHEDULED:
        raise InvalidStateError(
            "only a scheduled publication can be rescheduled, not one that is "
            f"{publication.status.value}"
        )
    package = session.get(ContentPackage, publication.package_id)
    if package is None:
        raise NotFoundError("the publication's own package no longer exists")
    _check_spacing(
        session,
        workspace_id=package.workspace_id,
        channel=publication.channel,
        scheduled_at=scheduled_at,
        exclude_publication_id=publication.id,
    )
    publication.scheduled_at = scheduled_at
    session.flush()
    return publication


def due_publications(
    session: Session, now: datetime | None = None, limit: int = 100
) -> list[Publication]:
    """Publications ready to attempt: scheduled, due, not yet exhausted."""
    now = now or datetime.now(UTC)
    return list(
        session.scalars(
            select(Publication)
            .where(
                Publication.status == PublicationStatus.SCHEDULED,
                Publication.scheduled_at <= now,
                Publication.attempt_count < MAX_ATTEMPTS,
            )
            .order_by(Publication.scheduled_at)
            .limit(limit)
        ).all()
    )


def gather_content(session: Session, publication: Publication) -> PublishContent:
    """Assemble what a connector needs for one publication, once."""
    package = session.get(ContentPackage, publication.package_id)
    if package is None:
        raise NotFoundError("content package not found")

    variant = session.get(Variant, publication.variant_id) if publication.variant_id else None
    if variant is None:
        raise InvalidStateError("this publication has no variant to publish")

    return content_for_variant(session, package, variant)


def content_for_variant(
    session: Session, package: ContentPackage, variant: Variant
) -> PublishContent:
    """The same assembly ``gather_content`` does, for a variant that is not
    (and for X, never will be) behind a ``Publication`` row —
    ``app.services.x_export`` is the other caller."""
    backend = storage.get_backend()
    media: list[MediaForPublish] = []
    for asset in session.scalars(
        select(MediaAsset).where(
            MediaAsset.package_id == package.id,
            MediaAsset.is_selected.is_(True),
            MediaAsset.kind == MediaKind.IMAGE,
            MediaAsset.storage_key.is_not(None),
        )
    ):
        try:
            data = backend.get(asset.storage_key)
        except storage.StorageError:
            logger.warning(
                "selected media asset missing from storage",
                extra={"asset_id": str(asset.id), "key": asset.storage_key},
            )
            continue
        try:
            url = backend.url(asset.storage_key)
        except storage.StorageError:
            url = None
        media.append(
            MediaForPublish(
                data=data,
                mime_type=asset.mime_type or "image/png",
                filename=asset.storage_key.rsplit("/", 1)[-1],
                alt_text=_alt_text_for(package, asset),
                url=url,
            )
        )

    body = variant.body or {}
    utm_campaign = body.get("utm_campaign")
    return PublishContent(
        title=package.title,
        body=str(body.get("body") or ""),
        hook=body.get("hook"),
        hashtags=tuple(body.get("hashtags") or ()),
        call_to_action=body.get("call_to_action"),
        article=package.article,
        media=tuple(media),
        utm={"campaign": utm_campaign} if utm_campaign else {},
        hreflang_alternates=(
            _hreflang_alternates(session, package) if variant.channel is Channel.WORDPRESS else {}
        ),
    )


def _hreflang_alternates(session: Session, package: ContentPackage) -> dict[str, str]:
    """Every other language version's own published WordPress URL, keyed by
    locale (handoff section 11) — empty when there are no siblings, or none
    of them has a published WordPress post yet."""
    siblings = package_service.language_siblings(session, package)
    if not siblings:
        return {}
    sibling_ids = [sibling.id for sibling in siblings]
    rows = session.execute(
        select(Publication.package_id, Publication.external_url).where(
            Publication.package_id.in_(sibling_ids),
            Publication.channel == Channel.WORDPRESS,
            Publication.status == PublicationStatus.PUBLISHED,
            Publication.external_url.is_not(None),
        )
    ).all()
    url_by_package = {row.package_id: row.external_url for row in rows}
    return {
        sibling.locale: url_by_package[sibling.id]
        for sibling in siblings
        if sibling.id in url_by_package
    }


def _alt_text_for(package: ContentPackage, asset: MediaAsset) -> str | None:
    article = package.article or {}
    alts = article.get("image_alts") or []
    if alts and isinstance(alts, list):
        first = alts[0]
        if isinstance(first, dict) and first.get("alt_text"):
            return str(first["alt_text"])
    return package.title


def attempt(session: Session, publication: Publication) -> Publication:
    """Make one publish attempt and update the row accordingly.

    Never raises for an ordinary publish failure — the outcome is written to
    the row (retryable versus exhausted) and returned, so the caller (the
    Celery task, or a test) never has to catch a connector's exception
    itself. A programming error (a missing package, an unimplemented
    channel) still raises, since retrying that would never succeed.
    """
    package = session.get(ContentPackage, publication.package_id)
    if package is None:
        raise NotFoundError("content package not found")

    publication.status = PublicationStatus.PUBLISHING
    publication.attempt_count += 1

    try:
        credential_row = channel_credentials.active_credential_for(
            session, package.workspace_id, publication.channel
        )
        credential = channel_credentials.decrypt_for_publish(credential_row)
        content = gather_content(session, publication)
        connector = build_connector(publication.channel)
        result = connector.publish(content, credential)
    except (ConnectorError, NotFoundError) as exc:
        _record_failure(publication, str(exc))
        return publication

    publication.status = PublicationStatus.PUBLISHED
    publication.published_at = datetime.now(UTC)
    publication.external_id = result.external_id
    publication.external_url = result.external_url
    publication.last_error = None
    # The package-wide status only tracks "has this gone out anywhere yet",
    # not per-channel state — the first publication to succeed is what
    # starts the measuring stage (handoff section 3, step 7), the same
    # coarseness the rest of the seven-stage state machine already uses.
    if package.status is PackageStatus.SCHEDULED:
        package_service.transition(package, PackageStatus.PUBLISHED)
    logger.info(
        "publication succeeded",
        extra={
            "publication_id": str(publication.id),
            "channel": publication.channel.value,
            "attempt": publication.attempt_count,
        },
    )
    return publication


def _record_failure(publication: Publication, reason: str) -> None:
    publication.last_error = reason[:4000]
    if publication.attempt_count >= MAX_ATTEMPTS:
        publication.status = PublicationStatus.FAILED
        publication.operator_alerted = True
        logger.error(
            "publication failed permanently; operator alert due",
            extra={
                "publication_id": str(publication.id),
                "channel": publication.channel.value,
                "attempts": publication.attempt_count,
                "reason": reason,
            },
        )
    else:
        # Left SCHEDULED: the next sweep of due_publications() picks it back
        # up. No explicit backoff delay — the sweep interval itself is the
        # backoff, which is enough for the three-attempt budget the handoff
        # asks for without adding a second timing mechanism to reason about.
        publication.status = PublicationStatus.SCHEDULED
        logger.warning(
            "publication attempt failed, will retry",
            extra={
                "publication_id": str(publication.id),
                "channel": publication.channel.value,
                "attempt": publication.attempt_count,
                "reason": reason,
            },
        )


def cancel_publication(session: Session, publication: Publication) -> Publication:
    if publication.status in {PublicationStatus.PUBLISHED, PublicationStatus.CANCELLED}:
        raise InvalidStateError(f"cannot cancel a publication that is {publication.status.value}")
    publication.status = PublicationStatus.CANCELLED
    return publication
