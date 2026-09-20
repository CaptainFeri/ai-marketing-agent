"""The questionnaire's saved state (handoff phase 1, week 3).

One draft per workspace, with row level security like every other
tenant-scoped table — a half-finished brand brief is as sensitive as a
finished one.

Revision ID: 0005_brief_draft
Revises: 0004_switch_calibration
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_brief_draft"
down_revision: str | None = "0004_switch_calibration"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "brief_draft",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column(
            "answers",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("suggestion", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("suggestion_status", sa.String(length=16), nullable=False),
        sa.Column("suggestion_error", sa.Text(), nullable=True),
        sa.Column("suggestion_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("website_url", sa.String(length=2048), nullable=True),
        sa.Column("website_excerpt", sa.Text(), nullable=True),
        sa.Column("website_fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_brief_id", sa.Uuid(), nullable=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "suggestion_status IN ('idle', 'running', 'ready', 'failed')",
            name=op.f("ck_brief_draft_suggestion_status"),
        ),
        sa.ForeignKeyConstraint(
            ["submitted_brief_id"],
            ["brand_brief.id"],
            name=op.f("fk_brief_draft_submitted_brief_id"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenant.id"],
            name=op.f("fk_brief_draft_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_brief_draft_workspace_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_brief_draft")),
        sa.UniqueConstraint("workspace_id", name="one_per_workspace"),
    )
    op.create_index(op.f("ix_brief_draft_tenant_id"), "brief_draft", ["tenant_id"])

    op.execute("ALTER TABLE brief_draft ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE brief_draft FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON brief_draft
            USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON brief_draft")
    op.drop_index(op.f("ix_brief_draft_tenant_id"), table_name="brief_draft")
    op.drop_table("brief_draft")
