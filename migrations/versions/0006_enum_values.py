"""Store enum values rather than member names.

SQLAlchemy persists ``PipelineStep.GEO_OPTIMIZER`` as ``GEO_OPTIMIZER`` unless
told otherwise, while the API, the JSONB payloads and every hand-written query
use ``geo_optimizer``. That mismatch makes ``WHERE status = 'drafting'``
silently return nothing, which is the worst kind of wrong.

``app.db.base.enum_column`` now stores values. Every member's value is the
lowercase of its name, so existing rows convert with ``lower()`` — and running
it twice changes nothing.

Revision ID: 0006_enum_values
Revises: 0005_brief_draft
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0006_enum_values"
down_revision: str | None = "0005_brief_draft"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Every enum-backed column, as (table, column).
ENUM_COLUMNS: tuple[tuple[str, str], ...] = (
    ("tenant", "plan"),
    ("membership", "role"),
    ("brand_brief", "video_mode"),
    ("brief_draft", "suggestion_status"),
    ("topic", "status"),
    ("content_package", "status"),
    ("content_package", "video_mode"),
    ("content_package", "current_step"),
    ("step_run", "step"),
    ("step_run", "status"),
    ("variant", "channel"),
    ("approval", "gate"),
    ("approval", "decision"),
    ("approval", "return_to_step"),
    ("media_asset", "kind"),
    ("speaker_profile", "kind"),
    ("publication", "channel"),
    ("publication", "status"),
    ("channel_credential", "channel"),
    ("gpu_job", "kind"),
    ("gpu_job", "window"),
    ("gpu_job", "status"),
    ("gpu_cost_estimate", "kind"),
    ("gpu_window_state", "current_window"),
    ("gpu_window_state", "current_kind"),
)


def upgrade() -> None:
    # The check constraint on brief_draft was written against values already,
    # which is how the mismatch surfaced; drop it so the update cannot trip
    # over a half-converted table, then put it back.
    op.execute("ALTER TABLE brief_draft DROP CONSTRAINT IF EXISTS ck_brief_draft_suggestion_status")

    for table, column in ENUM_COLUMNS:
        # Identifiers are quoted: "window" is a reserved word in PostgreSQL.
        op.execute(
            f'UPDATE "{table}" SET "{column}" = lower("{column}") WHERE "{column}" IS NOT NULL'
        )

    op.execute(
        "ALTER TABLE brief_draft ADD CONSTRAINT ck_brief_draft_suggestion_status "
        "CHECK (suggestion_status IN ('idle', 'running', 'ready', 'failed'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE brief_draft DROP CONSTRAINT IF EXISTS ck_brief_draft_suggestion_status")
    for table, column in ENUM_COLUMNS:
        op.execute(
            f'UPDATE "{table}" SET "{column}" = upper("{column}") WHERE "{column}" IS NOT NULL'
        )
    op.execute(
        "ALTER TABLE brief_draft ADD CONSTRAINT ck_brief_draft_suggestion_status "
        "CHECK (suggestion_status IN ('IDLE', 'RUNNING', 'READY', 'FAILED'))"
    )
