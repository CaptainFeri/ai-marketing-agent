"""The workspace calendar (handoff section 11, phase 2): every scheduled or
published post across every package in a workspace, plus known holidays —
what backs the panel's drag-and-drop calendar view.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ContentPackage, Publication, Workspace
from app.schemas.calendar import CalendarHolidayOut, CalendarOut, CalendarPublicationOut
from app.services.holidays import holidays_in_range


def workspace_calendar(
    session: Session, workspace: Workspace, start: date, end: date
) -> CalendarOut:
    range_start = datetime.combine(start, time.min, tzinfo=UTC)
    range_end = datetime.combine(end, time.max, tzinfo=UTC)

    rows = session.execute(
        select(Publication, ContentPackage.title)
        .join(ContentPackage, Publication.package_id == ContentPackage.id)
        .where(
            ContentPackage.workspace_id == workspace.id,
            Publication.scheduled_at >= range_start,
            Publication.scheduled_at <= range_end,
        )
        .order_by(Publication.scheduled_at)
    ).all()

    publications = [
        CalendarPublicationOut(
            id=publication.id,
            package_id=publication.package_id,
            package_title=title,
            channel=publication.channel,
            status=publication.status,
            scheduled_at=publication.scheduled_at,
            external_url=publication.external_url,
        )
        for publication, title in rows
    ]
    holidays = [
        CalendarHolidayOut(date=h.date, name_fa=h.name_fa, name_en=h.name_en)
        for h in holidays_in_range(start, end)
    ]
    return CalendarOut(
        timezone=workspace.timezone,
        calendar=workspace.calendar,
        min_publish_spacing_minutes=workspace.min_publish_spacing_minutes,
        publications=publications,
        holidays=holidays,
    )


__all__ = ["workspace_calendar"]
