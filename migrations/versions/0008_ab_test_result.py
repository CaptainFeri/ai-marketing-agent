"""A/B hook result tracking (handoff section 11, phase 2).

One row per (package, channel), holding the current CTR comparison between
the "a" and "b" hook variants and, once decidable, the winner. Re-evaluated
and upserted in place as more metrics data arrives rather than versioned.

Revision ID: 0008_ab_test_result
Revises: 0007_analytics_credential
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_ab_test_result"
down_revision: str | None = "0007_analytics_credential"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ab_test_result",
        sa.Column("package_id", sa.Uuid(), nullable=False),
        sa.Column(
            "channel",
            sa.Enum(
                "WORDPRESS",
                "TELEGRAM",
                "INSTAGRAM",
                "LINKEDIN",
                "X",
                "YOUTUBE",
                "APARAT",
                name="channel",
                native_enum=False,
                length=32,
                values_callable=lambda cls: [member.value for member in cls],
            ),
            nullable=False,
        ),
        sa.Column("a_variant_id", sa.Uuid(), nullable=False),
        sa.Column("b_variant_id", sa.Uuid(), nullable=False),
        sa.Column("winner_variant_id", sa.Uuid(), nullable=True),
        sa.Column("a_clicks", sa.Integer(), nullable=False),
        sa.Column("a_impressions", sa.Integer(), nullable=False),
        sa.Column("b_clicks", sa.Integer(), nullable=False),
        sa.Column("b_impressions", sa.Integer(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f("fk_ab_test_result_tenant_id"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["package_id"],
            ["content_package.id"],
            name=op.f("fk_ab_test_result_package_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["a_variant_id"],
            ["variant.id"],
            name=op.f("fk_ab_test_result_a_variant_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["b_variant_id"],
            ["variant.id"],
            name=op.f("fk_ab_test_result_b_variant_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["winner_variant_id"],
            ["variant.id"],
            name=op.f("fk_ab_test_result_winner_variant_id"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ab_test_result")),
        sa.UniqueConstraint("package_id", "channel", name="package_channel_ab"),
    )
    op.create_index(op.f("ix_ab_test_result_tenant_id"), "ab_test_result", ["tenant_id"])

    op.execute("ALTER TABLE ab_test_result ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE ab_test_result FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON ab_test_result
            USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON ab_test_result")
    op.drop_index(op.f("ix_ab_test_result_tenant_id"), table_name="ab_test_result")
    op.drop_table("ab_test_result")
