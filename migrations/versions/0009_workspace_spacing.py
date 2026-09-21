"""Minimum spacing between two publications on the same channel (handoff
section 11, phase 2: "قوانین فاصله بین پست‌ها").

Revision ID: 0009_workspace_spacing
Revises: 0008_ab_test_result
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_workspace_spacing"
down_revision: str | None = "0008_ab_test_result"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workspace",
        sa.Column(
            "min_publish_spacing_minutes", sa.Integer(), server_default="60", nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_column("workspace", "min_publish_spacing_minutes")
