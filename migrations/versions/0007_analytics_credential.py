"""Search Console / GA4 credentials (handoff section 7, weeks 7-8).

A Google service account key per workspace per provider, encrypted the same
way as a channel credential — but its own table, since nothing is ever
published "to" Search Console or GA4.

Revision ID: 0007_analytics_credential
Revises: 0006_enum_values
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_analytics_credential"
down_revision: str | None = "0006_enum_values"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "analytics_credential",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column(
            "provider",
            sa.Enum(
                "SEARCH_CONSOLE",
                "GA4",
                name="analytics_provider",
                native_enum=False,
                length=32,
                values_callable=lambda cls: [member.value for member in cls],
            ),
            nullable=False,
        ),
        sa.Column("label", sa.String(length=64), nullable=False),
        sa.Column("encrypted_payload", sa.LargeBinary(), nullable=False),
        sa.Column("key_version", sa.Integer(), nullable=False),
        sa.Column("public_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
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
            ["tenant_id"],
            ["tenant.id"],
            name=op.f("fk_analytics_credential_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_analytics_credential_workspace_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_analytics_credential")),
        sa.UniqueConstraint(
            "workspace_id", "provider", "label", name="workspace_provider_label"
        ),
    )
    op.create_index(
        op.f("ix_analytics_credential_tenant_id"), "analytics_credential", ["tenant_id"]
    )

    op.execute("ALTER TABLE analytics_credential ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE analytics_credential FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON analytics_credential
            USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON analytics_credential")
    op.drop_index(op.f("ix_analytics_credential_tenant_id"), table_name="analytics_credential")
    op.drop_table("analytics_credential")
