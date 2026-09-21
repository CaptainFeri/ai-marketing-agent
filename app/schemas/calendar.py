"""The workspace calendar (handoff section 11, phase 2): every scheduled or
published post across every package, plus known holidays, for the panel's
drag-and-drop calendar view."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel

from app.db.enums import Channel, PublicationStatus


class CalendarPublicationOut(BaseModel):
    id: uuid.UUID
    package_id: uuid.UUID
    package_title: str
    channel: Channel
    status: PublicationStatus
    scheduled_at: datetime
    external_url: str | None


class CalendarHolidayOut(BaseModel):
    date: date
    name_fa: str
    name_en: str


class CalendarOut(BaseModel):
    timezone: str
    calendar: str
    min_publish_spacing_minutes: int
    publications: list[CalendarPublicationOut]
    holidays: list[CalendarHolidayOut]


class PublishTimeSuggestionOut(BaseModel):
    scheduled_at: datetime
    #: "rule_of_thumb" until the channel has enough of the workspace's own
    #: engagement history, then "historical_engagement".
    basis: str
    hour_of_day: int
    sample_size: int
