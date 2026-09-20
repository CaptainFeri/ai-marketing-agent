"""Brand brief payload — the questionnaire result (handoff section 9).

The wizard in the panel writes this; every downstream agent reads it.  Keeping
it a validated model rather than free-form JSON means a malformed brief fails
at the API boundary instead of halfway through a ten-minute pipeline run.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, HttpUrl, field_validator

from app.db.enums import Channel, VideoMode
from app.schemas.common import ORMModel


class Persona(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    pains: list[str] = Field(default_factory=list)
    goals: list[str] = Field(default_factory=list)
    locale: str | None = None


class Competitor(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    url: HttpUrl | None = None
    notes: str | None = Field(default=None, max_length=1000)


class BrandVoice(BaseModel):
    tone: list[str] = Field(default_factory=list)
    person: str | None = Field(default=None, description="e.g. first plural, second singular")
    do: list[str] = Field(default_factory=list)
    dont: list[str] = Field(default_factory=list)
    reading_level: str | None = None


class VisualIdentity(BaseModel):
    palette: list[str] = Field(default_factory=list)
    fonts: list[str] = Field(default_factory=list)
    style_keywords: list[str] = Field(default_factory=list)
    logo_storage_key: str | None = None
    # Image models mangle Persian and Arabic script, so type is always
    # rendered over the image with an HTML template (handoff section 5).
    overlay_template: str | None = None


class Pillar(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    target_share: float | None = Field(default=None, ge=0, le=1)


class SeedKeywords(BaseModel):
    """Keywords are researched per language, never translated (decision D3)."""

    fa: list[str] = Field(default_factory=list)
    en: list[str] = Field(default_factory=list)
    ar: list[str] = Field(default_factory=list)

    def for_locale(self, locale: str) -> list[str]:
        return list(getattr(self, locale, []) or [])


class BrandBriefData(BaseModel):
    brand: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)
    website: HttpUrl | None = None
    voice: BrandVoice = Field(default_factory=BrandVoice)
    visual_identity: VisualIdentity = Field(default_factory=VisualIdentity)
    competitors: list[Competitor] = Field(default_factory=list)
    personas: list[Persona] = Field(default_factory=list)
    seed_keywords: SeedKeywords = Field(default_factory=SeedKeywords)
    pillars: list[Pillar] = Field(default_factory=list)
    # The fixed question set the GEO monitor agent asks about this brand.
    ai_questions: list[str] = Field(default_factory=list)
    channels: list[Channel] = Field(default_factory=list)
    goals: dict[str, str] = Field(default_factory=dict)
    video_mode: VideoMode = VideoMode.VOICE
    locales: list[str] = Field(default_factory=lambda: ["fa"])
    # Claims need a source; the QA agent checks this (handoff section 13).
    evidence_policy: str = "every statistic must carry a source URL"

    @field_validator("locales")
    @classmethod
    def _known_locales(cls, value: list[str]) -> list[str]:
        allowed = {"fa", "en", "ar"}
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"unsupported locales: {sorted(unknown)}")
        if not value:
            raise ValueError("at least one locale is required")
        return value


class BrandBriefCreate(BaseModel):
    data: BrandBriefData
    activate: bool = True


class BrandBriefOut(ORMModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    version: int
    is_active: bool
    video_mode: VideoMode
    data: dict
    created_at: datetime
