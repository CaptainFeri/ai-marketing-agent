"""Enumerations shared by the ORM models, the API schemas and the workers.

They are stored as VARCHAR with a CHECK constraint (``native_enum=False``)
rather than PostgreSQL ENUM types: adding a value is then an ordinary
migration instead of an ``ALTER TYPE`` that locks the table.
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    """Workspace roles (handoff section 4 — auth)."""

    OWNER = "owner"
    ADMIN = "admin"
    EDITOR = "editor"
    VIEWER = "viewer"

    @property
    def rank(self) -> int:
        return _ROLE_RANK[self]

    def satisfies(self, required: Role) -> bool:
        """True when this role is at least as privileged as ``required``."""
        return self.rank >= required.rank


_ROLE_RANK = {Role.VIEWER: 0, Role.EDITOR: 1, Role.ADMIN: 2, Role.OWNER: 3}


class Plan(StrEnum):
    """Tenant plan; drives the GPU quota weight (section 7)."""

    TRIAL = "trial"
    STARTER = "starter"
    GROWTH = "growth"
    SCALE = "scale"


class PackageStatus(StrEnum):
    """ContentPackage lifecycle from handoff section 3."""

    PLANNED = "planned"
    DRAFTING = "drafting"
    TEXT_REVIEW = "text_review"
    REJECTED = "rejected"
    MEDIA_GENERATING = "media_generating"
    SELECTION = "selection"
    SCHEDULED = "scheduled"
    PUBLISHED = "published"
    MEASURING = "measuring"
    REFRESH = "refresh"
    FAILED = "failed"


class VideoMode(StrEnum):
    """Decision D4."""

    NONE = "none"
    VOICE = "voice"
    FACE = "face"


class PipelineStep(StrEnum):
    """The seven-stage line; one StepRun row per execution of a step."""

    RESEARCHER = "researcher"
    STRATEGIST = "strategist"
    WRITER = "writer"
    GEO_OPTIMIZER = "geo_optimizer"
    SEO_OPTIMIZER = "seo_optimizer"
    QA = "qa"
    MARKETIZER = "marketizer"
    TOPIC_PLANNER = "topic_planner"
    # Not part of a package's chain: it drafts a brand brief from the
    # customer's website for the questionnaire's "guess it" button.
    BRIEF_ASSISTANT = "brief_assistant"


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class Channel(StrEnum):
    """Handoff section 10."""

    WORDPRESS = "wordpress"
    TELEGRAM = "telegram"
    INSTAGRAM = "instagram"
    LINKEDIN = "linkedin"
    X = "x"
    YOUTUBE = "youtube"
    APARAT = "aparat"


class AnalyticsProvider(StrEnum):
    """Handoff section 7 (weeks 7-8): a read-only measurement source, not a
    publish target — kept separate from ``Channel`` since a publication can
    never be posted "to" Search Console or GA4."""

    SEARCH_CONSOLE = "search_console"
    GA4 = "ga4"


class MediaKind(StrEnum):
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    SUBTITLE = "subtitle"


class ApprovalGate(StrEnum):
    """Gate 1 = text approval, gate 2 = media selection + scheduling."""

    TEXT = "text"
    MEDIA = "media"


class ApprovalDecision(StrEnum):
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"
    REJECTED = "rejected"


class PublicationStatus(StrEnum):
    SCHEDULED = "scheduled"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SpeakerKind(StrEnum):
    FACE = "face"
    VOICE = "voice"
    FACE_AND_VOICE = "face_and_voice"


class GpuWindow(StrEnum):
    """Section 6: the GPU alternates between two working windows."""

    TEXT = "text"
    MEDIA = "media"


class GpuJobKind(StrEnum):
    """One kind == one model to load.  Batching groups jobs by this value."""

    LLM_TEXT = "llm_text"
    EMBEDDING = "embedding"
    IMAGE_FLUX = "image_flux"
    VIDEO_WAN = "video_wan"
    TTS = "tts"
    TRANSCRIBE_WHISPER = "transcribe_whisper"
    LIPSYNC_LATENTSYNC = "lipsync_latentsync"
    LIPSYNC_SADTALKER = "lipsync_sadtalker"

    @property
    def window(self) -> GpuWindow:
        return GpuWindow.TEXT if self in _TEXT_WINDOW_KINDS else GpuWindow.MEDIA

    @property
    def nightly_only(self) -> bool:
        """Heavy video work is deferred to the low-traffic window."""
        return self in _NIGHTLY_KINDS


_TEXT_WINDOW_KINDS = {GpuJobKind.LLM_TEXT, GpuJobKind.EMBEDDING}
_NIGHTLY_KINDS = {
    GpuJobKind.VIDEO_WAN,
    GpuJobKind.LIPSYNC_LATENTSYNC,
    GpuJobKind.LIPSYNC_SADTALKER,
}


class GpuJobStatus(StrEnum):
    PENDING = "pending"
    LEASED = "leased"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SuggestionStatus(StrEnum):
    """State of the questionnaire's "guess it" button.

    The work runs on the GPU queue like everything else, so the panel polls
    rather than waiting on the request.
    """

    IDLE = "idle"
    RUNNING = "running"
    READY = "ready"
    FAILED = "failed"


class TopicStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    IN_PRODUCTION = "in_production"
    DONE = "done"
    DISCARDED = "discarded"
